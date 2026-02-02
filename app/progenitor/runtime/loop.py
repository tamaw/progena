"""
Progenitor Loop Runner.

Perpetual loop execution with backoff and cycle control.
The loop is intentionally infinite - when there's nothing queued,
it idles and waits for input or scheduled tasks.

Controls:
- idle sleep/backoff when no new input
- max actions per cycle (tool/A2A/Copilot)
- restart-safe checkpoints (write state every cycle)
- health file updated each cycle for Docker health checks
- telemetry for observability (traces and metrics)
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, TYPE_CHECKING
import uuid

from .time_utils import utcnow, utcnow_isoformat
from .scheduler import Scheduler

if TYPE_CHECKING:
    from ..config import ProgenitorConfig

logger = logging.getLogger(__name__)

# Health file location (relative to state directory)
HEALTH_FILE = "health.json"

# Telemetry - imported lazily to avoid circular imports
_telemetry_initialized = False
_exporter = None


def _init_telemetry(config: "ProgenitorConfig"):
    """Initialize telemetry if enabled."""
    global _telemetry_initialized, _exporter

    if _telemetry_initialized:
        return

    if not config.telemetry.enabled:
        _telemetry_initialized = True
        return

    try:
        from ..telemetry import setup_telemetry

        output_dir = config.telemetry.output_dir
        if output_dir is None:
            output_dir = config.storage.log_dir / "telemetry"

        _, _, _exporter = setup_telemetry(
            output_dir=output_dir,
            enable_console=config.telemetry.console_export,
            metrics_interval=config.telemetry.metrics_interval_seconds,
        )

        logger.info("Telemetry initialized, output_dir=%s", output_dir)
        _telemetry_initialized = True

    except Exception as e:
        logger.warning("Failed to initialize telemetry: %s", e)
        _telemetry_initialized = True


# Module-level reference to the current loop instance for streaming access
_current_loop: Optional["ProgenitorLoop"] = None


def get_current_loop() -> Optional["ProgenitorLoop"]:
    """Get the current loop instance for streaming access."""
    return _current_loop


class ProgenitorLoop:
    """Manages the perpetual execution loop."""

    def __init__(
        self,
        graph,
        state_manager,
        config: "ProgenitorConfig",
        llm_client=None,
    ):
        self.graph = graph
        self.state_manager = state_manager
        self.config = config
        self.llm_client = llm_client
        self.running = False

        # Bounded queues to prevent memory exhaustion
        runtime = config.runtime
        self._input_queue: asyncio.Queue = asyncio.Queue(
            maxsize=runtime.max_input_queue_size
        )
        self._output_queue: asyncio.Queue = asyncio.Queue(
            maxsize=runtime.max_output_queue_size
        )
        self._stream_queue: asyncio.Queue = asyncio.Queue(
            maxsize=runtime.max_stream_queue_size
        )
        # Event queue for pushing background events to clients
        # Events include: backlog updates, scheduled task triggers, work progress
        self._event_queue: asyncio.Queue = asyncio.Queue(
            maxsize=runtime.max_output_queue_size  # Same size as output queue
        )
        # Event to signal when a graph cycle completes (for IPC synchronization)
        self._cycle_complete_event: asyncio.Event = asyncio.Event()
        self._cycle_telemetry = None
        self._scheduler = Scheduler(timezone=config.runtime.timezone)

        # Stream callback for real-time LLM streaming
        # Nodes can use this to stream tokens directly to the client
        self._stream_callback: Optional[Callable[[str, bool], Awaitable[None]]] = None

        # Streaming state - controlled by the loop, accessed by nodes
        self._streaming_enabled = False
        self._response_was_streamed = False
        self._stream_final_sent = False

        # Background build runner (initialized in run_forever)
        self._background_runner = None

        # Lock to prevent concurrent graph cycles
        self._graph_lock = asyncio.Lock()

        # Shared state for the loop (protected by _graph_lock)
        self._state: dict[str, Any] = {}

    async def run_forever(self) -> None:
        """Run the perpetual loop.

        The loop processes user input and scheduled tasks continuously.
        This runs TWO concurrent tasks:
        1. Input handler: Waits for user input and processes it immediately
        2. Background worker: Continuously checks for backlog/scheduled work

        This ensures background work happens even without user interaction.
        State is checkpointed after each cycle for crash recovery.
        """
        global _current_loop
        _current_loop = self
        self.running = True

        # Initialize background build runner
        if self.llm_client:
            from .background_build import BackgroundBuildRunner, set_background_runner
            self._background_runner = BackgroundBuildRunner(
                graph=self.graph,
                state_manager=self.state_manager,
                config=self.config,
                llm_client=self.llm_client,
            )
            set_background_runner(self._background_runner)
            self._background_runner.set_completion_callback(self._handle_build_completion)
            logger.info("Background build runner initialized")

        # Initialize telemetry
        _init_telemetry(self.config)

        run_id = str(uuid.uuid4())

        # Initialize shared state
        self._state = {
            "run_id": run_id,
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": False,
            "messages": [],
            "backlog": [],
            "schedule": [],
            "agents": [],
            "idle_cycles": 0,
            "current_backoff_seconds": self.config.runtime.idle_backoff_seconds,
        }

        # Load persisted state (recover from previous run)
        # Load from multiple files:
        # - current_state.json: user session state (messages, tool calls)
        # - build_state.json: build state (backlog, build progress)
        # - schedule.json: scheduled tasks (system feature, persists beyond sessions)
        current_state = await self.state_manager.get_current_state()
        build_state = await self.state_manager.get_build_state()
        schedule_entries = await self.state_manager.get_schedule()

        # Merge: progenitor state takes precedence for operational fields
        self._state.update(current_state)

        # Load schedule from schedule.json (system feature, not user session)
        self._state["schedule"] = [e.model_dump() if hasattr(e, "model_dump") else e for e in schedule_entries]

        # Merge build-specific fields from build_state
        for key in ["build_in_progress", "build_phase", "build_started_at",
                    "build_task_description", "build_messages", "ideal_state",
                    "current_iteration", "verification_score", "best_artefact_score",
                    "spec_doc", "verification_reports", "lessons_learned", "artefacts"]:
            if key in build_state:
                self._state[key] = build_state[key]
        # Backlog comes from build_state (build work items)
        if "backlog" in build_state:
            self._state["backlog"] = build_state["backlog"]

        self._state["run_id"] = run_id  # New run ID for this session

        logger.info("Starting perpetual loop (run_id: %s)", run_id)

        # Update health immediately on startup
        await self._update_health_file(self._state)

        # Push startup event
        await self._push_event("loop_started", f"Perpetual loop started (run_id: {run_id})")

        # Run both tasks concurrently
        input_task = asyncio.create_task(self._input_handler_loop(run_id))
        background_task = asyncio.create_task(self._background_worker_loop(run_id))

        try:
            # Wait for either task to complete (usually due to shutdown)
            done, pending = await asyncio.wait(
                [input_task, background_task],
                return_when=asyncio.FIRST_EXCEPTION,
            )

            # Check for exceptions
            for task in done:
                if task.exception():
                    logger.error("Task failed: %s", task.exception())

            # Cancel remaining tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        except asyncio.CancelledError:
            logger.info("Loop cancelled by signal")
            input_task.cancel()
            background_task.cancel()
            try:
                await asyncio.gather(input_task, background_task, return_exceptions=True)
            except asyncio.CancelledError:
                pass

        # Final checkpoint and telemetry shutdown
        await self._checkpoint_state(self._state)
        await self._update_health_file(self._state, status="stopped")
        await self._push_event("loop_stopped", "Perpetual loop stopped")
        self._shutdown_telemetry()
        logger.info("Loop terminated")

    async def _input_handler_loop(self, run_id: str) -> None:
        """Handle user input messages.

        This task waits for user input and processes it through the graph.
        It runs concurrently with the background worker.
        """
        runtime = self.config.runtime

        while self.running:
            try:
                # Wait for user input (with short timeout to check running flag)
                user_input = await self._get_input(timeout=1.0)

                if not user_input:
                    continue

                logger.info("Got user input: %s", user_input[:50] if len(user_input) > 50 else user_input)

                # Acquire lock to run graph cycle
                async with self._graph_lock:
                    # Reset backoff on input
                    self._state["current_backoff_seconds"] = runtime.idle_backoff_seconds
                    self._state["idle_cycles"] = 0

                    self._state["current_input"] = user_input
                    self._state["cycle_count"] = self._state.get("cycle_count", 0) + 1
                    self._state["actions_this_cycle"] = 0

                    # Enable streaming mode
                    self._streaming_enabled = True
                    self._response_was_streamed = False
                    self._stream_final_sent = False

                    # Run graph cycle
                    await self._run_graph_cycle(run_id, source="user_input")

                    # Disable streaming mode
                    self._streaming_enabled = False
                    self._response_was_streamed = False
                    self._stream_final_sent = False

                    # Signal cycle complete for IPC synchronization
                    logger.info("Graph cycle complete, signaling")
                    await self._signal_cycle_complete()

            except asyncio.CancelledError:
                logger.info("Input handler cancelled")
                break
            except Exception as e:
                logger.error("Input handler error: %s", e, exc_info=True)
                await self._push_event("error", f"Input handler error: {e}")
                await asyncio.sleep(1.0)

    async def _background_worker_loop(self, run_id: str) -> None:
        """Process backlog and scheduled tasks in the background.

        This task runs independently of user input, continuously checking
        for pending work and processing it. This is the TRUE perpetual loop.
        """
        runtime = self.config.runtime
        backoff = runtime.idle_backoff_seconds
        max_backoff = runtime.max_backoff_seconds
        backoff_multiplier = runtime.backoff_multiplier
        last_idle_log_backoff: Optional[float] = None

        logger.info("Background worker started")
        await self._push_event("background_worker_started", "Background worker is now running")

        while self.running:
            try:
                # Check if there's pending work (without holding the lock)
                has_work = await self._check_pending_work(self._state)

                if has_work:
                    # Try to acquire lock (non-blocking check first)
                    if self._graph_lock.locked():
                        # User input is being processed, wait a bit
                        logger.debug("Graph lock held, waiting for user input to complete")
                        await asyncio.sleep(0.5)
                        continue

                    async with self._graph_lock:
                        # Re-check work is still available (may have been processed)
                        has_work = await self._check_pending_work(self._state)
                        if not has_work:
                            continue

                        # Reset backoff when work is available
                        backoff = runtime.idle_backoff_seconds
                        self._state["current_backoff_seconds"] = backoff
                        self._state["idle_cycles"] = 0

                        # Notify clients that background work is starting
                        work_type = "scheduled" if self._state.get("due_items") else "backlog"
                        await self._push_event(
                            "work_started",
                            f"Processing {work_type} work",
                            {"work_type": work_type}
                        )

                        # Run a cycle to process pending work
                        self._state["current_input"] = None
                        self._state["cycle_count"] = self._state.get("cycle_count", 0) + 1

                        await self._run_graph_cycle(run_id, source=f"background_{work_type}")

                        # Notify clients that work completed
                        await self._push_event(
                            "work_completed",
                            f"Completed {work_type} work",
                            {"work_type": work_type}
                        )
                else:
                    # No work - increase backoff exponentially
                    self._state["idle_cycles"] = self._state.get("idle_cycles", 0) + 1
                    backoff = min(backoff * backoff_multiplier, max_backoff)
                    self._state["current_backoff_seconds"] = backoff

                    # Update health periodically even when idle
                    await self._update_health_file(self._state)

                    if backoff >= 60 and backoff != last_idle_log_backoff:
                        logger.info(
                            "Background worker idle; next check in %.1f s (idle_cycles=%d)",
                            backoff,
                            self._state.get("idle_cycles", 0),
                        )
                        last_idle_log_backoff = backoff
                    else:
                        logger.debug("Background worker idle, sleeping %.1f s", backoff)
                    await asyncio.sleep(backoff)

            except asyncio.CancelledError:
                logger.info("Background worker cancelled")
                break
            except Exception as e:
                logger.error("Background worker error: %s", e, exc_info=True)
                await self._push_event("error", f"Background worker error: {e}")
                if self._cycle_telemetry:
                    self._cycle_telemetry.complete(success=False, error=str(e))
                await self._update_health_file(self._state, status="error", error=str(e))
                await asyncio.sleep(backoff)

        logger.info("Background worker stopped")

    async def _run_graph_cycle(self, run_id: str, source: str = "unknown") -> None:
        """Run a single graph cycle.

        Args:
            run_id: The run ID for this session.
            source: Source of the cycle (e.g., 'user_input', 'background_backlog').
        """
        cycle_num = self._state["cycle_count"]
        thread_id = f"cycle_{cycle_num}"
        is_background = source.startswith("background_")

        logger.info("Running graph cycle %d (source: %s)", cycle_num, source)

        # Start cycle telemetry
        cycle_telem = self._start_cycle_telemetry(cycle_num, run_id)
        if cycle_telem:
            cycle_telem.add_event("cycle_start", {"source": source})

        try:
            async for event in self.graph.astream(
                self._state,
                config={"configurable": {"thread_id": thread_id}},
            ):
                # Process graph events
                for node_name, node_output in event.items():
                    output_info = list(node_output.keys()) if isinstance(node_output, dict) else type(node_output)
                    logger.info("GRAPH EVENT: node=%s, output_keys=%s", node_name, output_info)
                    if isinstance(node_output, dict):
                        self._state.update(node_output)

                    # Record node event in telemetry
                    if cycle_telem:
                        cycle_telem.add_event(f"node.{node_name}", {"output_keys": str(output_info)})

                # Check for response to send - stream it if not already streamed
                pending = self._state.get("pending_response")
                if self._response_was_streamed and not self._stream_final_sent:
                    # Response was already streamed by a node, just send final marker ONCE
                    await self.stream_final()
                    self._stream_final_sent = True
                    self._state["pending_response"] = None
                elif pending:
                    if is_background:
                        # Background task completed - push response as event for clients
                        current_item = self._state.get("current_work_item", {})
                        item_context = {}
                        if hasattr(current_item, "context"):
                            item_context = current_item.context
                        elif isinstance(current_item, dict):
                            item_context = current_item.get("context", {})

                        await self._push_event(
                            "scheduled_response",
                            str(pending),
                            {
                                "source": source,
                                "cycle": cycle_num,
                                "schedule_id": item_context.get("schedule_id"),
                                "original_request": item_context.get("user_request"),
                            }
                        )
                        logger.info("Pushed scheduled response event: %s", str(pending)[:100])
                    else:
                        # User input - stream response as usual
                        await self._stream_response(str(pending))
                    self._state["pending_response"] = None

            # End cycle telemetry
            if cycle_telem:
                cycle_telem.complete(success=True)

            # Checkpoint state after cycle
            await self._checkpoint_state(self._state)
            await self._update_health_file(self._state)

        except Exception as e:
            logger.error("Graph cycle %d failed: %s", cycle_num, e, exc_info=True)
            if cycle_telem:
                cycle_telem.complete(success=False, error=str(e))
            raise

    async def _push_event(
        self,
        event_type: str,
        content: str = "",
        metadata: Optional[dict[str, Any]] = None
    ) -> None:
        """Push an event to clients via the event queue.

        Args:
            event_type: Type of event (e.g., 'backlog_update', 'work_started').
            content: Human-readable description.
            metadata: Additional event data.
        """
        event = {
            "event_type": event_type,
            "content": content,
            "metadata": metadata or {},
            "timestamp": utcnow_isoformat(),
        }
        try:
            self._event_queue.put_nowait(event)
            logger.debug("Pushed event: %s", event_type)
        except asyncio.QueueFull:
            logger.warning(
                "Event queue full (%d/%d), dropping event: %s",
                self._event_queue.qsize(),
                self._event_queue.maxsize,
                event_type
            )

    def _handle_build_completion(self, result) -> None:
        """Handle background build completion by pushing an event to clients."""
        status = getattr(result.status, "value", str(result.status))
        output_summary = result.output_summary or ""
        error_message = result.error_message or ""

        if status == "completed":
            content = f"✅ Build completed. {output_summary}".strip()
        elif status == "failed":
            content = f"❌ Build failed. {error_message}".strip()
        elif status == "cancelled":
            content = "⏹️ Build cancelled."
        else:
            content = f"ℹ️ Build finished with status: {status}".strip()

        metadata = {
            "build_id": result.build_id,
            "status": status,
            "task_description": result.task_description,
            "output_summary": result.output_summary,
            "error_message": result.error_message,
            "agent_name": result.agent_name,
            "iterations": result.iterations,
            "verification_score": result.verification_score,
            "success_criteria_met": result.success_criteria_met,
            "success_criteria_unmet": result.success_criteria_unmet,
        }

        asyncio.create_task(
            self._push_event(
                event_type="build_completed",
                content=content,
                metadata=metadata,
            )
        )

    async def get_event(self, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
        """Get an event from the event queue.

        Args:
            timeout: Max seconds to wait. None = wait forever.

        Returns:
            Event dict or None on timeout.
        """
        try:
            if timeout:
                return await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=timeout
                )
            return await self._event_queue.get()
        except asyncio.TimeoutError:
            return None

    def _start_cycle_telemetry(self, cycle_number: int, run_id: str):
        """Start telemetry tracking for a cycle."""
        if not self.config.telemetry.enabled:
            return None

        try:
            from ..telemetry import CycleTelemetry

            self._cycle_telemetry = CycleTelemetry(cycle_number, run_id)
            self._cycle_telemetry.start()
            return self._cycle_telemetry
        except Exception as e:
            logger.debug("Failed to start cycle telemetry: %s", e)
            return None

    def _shutdown_telemetry(self):
        """Shutdown telemetry on loop exit."""
        global _exporter
        if _exporter:
            try:
                from ..telemetry import shutdown_telemetry
                shutdown_telemetry(_exporter)
            except Exception as e:
                logger.debug("Failed to shutdown telemetry: %s", e)

    async def _get_input(self, timeout: float) -> Optional[str]:
        """Get user input with timeout.

        Caps timeout at 30 seconds to ensure health updates happen frequently.
        """
        # Cap timeout to ensure health gets updated frequently
        effective_timeout = min(timeout, 30.0)
        try:
            return await asyncio.wait_for(
                self._input_queue.get(),
                timeout=effective_timeout
            )
        except asyncio.TimeoutError:
            return None

    async def _send_output(self, message: str) -> None:
        """Send output to the user.

        Uses non-blocking put to avoid deadlock. If queue is full,
        logs a warning and drops the message.
        """
        try:
            self._output_queue.put_nowait(message)
            logger.info("Queued output message for client")
        except asyncio.QueueFull:
            logger.warning(
                "Output queue full (%d/%d), dropping message",
                self._output_queue.qsize(),
                self._output_queue.maxsize
            )

    async def _signal_cycle_complete(self) -> None:
        """Signal that a graph cycle has completed.

        This sets the cycle_complete_event which the IPC server can wait on
        to ensure it doesn't start processing the next request until the
        current cycle is fully done.
        """
        self._cycle_complete_event.set()

    async def wait_cycle_complete(self, timeout: Optional[float] = None) -> bool:
        """Wait for the current graph cycle to complete.

        Args:
            timeout: Max seconds to wait. None = wait forever.

        Returns:
            True if cycle completed, False if timed out.
        """
        try:
            if timeout:
                await asyncio.wait_for(
                    self._cycle_complete_event.wait(),
                    timeout=timeout
                )
            else:
                await self._cycle_complete_event.wait()
            # Clear the event for the next cycle
            self._cycle_complete_event.clear()
            return True
        except asyncio.TimeoutError:
            return False

    async def _stream_response(self, text: str) -> None:
        """Stream a response text directly through the stream queue.

        If text is already streamed via stream_token(), this just sends the final marker.
        Otherwise, sends the complete text as a single chunk.

        Args:
            text: The complete response text to stream.
        """
        if not text:
            return

        # Send complete response as final chunk
        # (Real-time streaming should have already sent tokens via stream_token)
        await self._send_stream_chunk(text, is_final=True)
        logger.debug("Sent complete response (%d chars)", len(text))

    async def stream_token(self, token: str) -> None:
        """Stream a single token directly to the client.

        This is called by LLM streaming to send tokens in real-time.

        Args:
            token: A single token/chunk from the LLM.
        """
        if token:
            await self._send_stream_chunk(token, is_final=False)

    async def stream_final(self) -> None:
        """Mark the end of a streaming response."""
        await self._send_stream_chunk("", is_final=True)

    async def _send_stream_chunk(self, chunk: str, is_final: bool = False) -> None:
        """Send a streaming chunk to the output stream.

        Args:
            chunk: The text chunk to stream.
            is_final: Whether this is the final chunk (signals stream end).
        """
        try:
            self._stream_queue.put_nowait({
                "type": "stream_chunk",
                "content": chunk,
                "is_final": is_final,
            })
        except asyncio.QueueFull:
            logger.warning(
                "Stream queue full (%d/%d), dropping chunk",
                self._stream_queue.qsize(),
                self._stream_queue.maxsize
            )

    async def _send_stream_end(self) -> None:
        """Signal the end of a streaming response."""
        try:
            self._stream_queue.put_nowait({
                "type": "stream_end",
                "content": "",
                "is_final": True,
            })
        except asyncio.QueueFull:
            logger.warning("Stream queue full, could not send stream end marker")

    async def get_stream_chunk(self, timeout: Optional[float] = None) -> Optional[dict]:
        """Get a streaming chunk from the agent.

        Returns:
            Dict with 'type', 'content', and 'is_final' keys, or None on timeout.
        """
        try:
            if timeout:
                return await asyncio.wait_for(
                    self._stream_queue.get(),
                    timeout=timeout
                )
            return await self._stream_queue.get()
        except asyncio.TimeoutError:
            return None

    async def _check_pending_work(self, state: dict[str, Any]) -> bool:
        """Check if there's pending work in backlog or schedule.

        Reloads schedule from schedule.json to pick up any new scheduled tasks
        (schedule is a system feature that persists beyond user sessions).
        """
        backlog = state.get("backlog", [])
        pending = [
            item for item in backlog
            if isinstance(item, dict) and item.get("status") in ("pending", "in_progress")
        ]

        if pending:
            return True

        # Reload schedule from schedule.json (system feature, may have been updated)
        try:
            schedule_entries = await self.state_manager.get_schedule()
            schedule_dicts: list[dict[str, Any]] = [
                e.model_dump() if hasattr(e, "model_dump") else e for e in schedule_entries
            ]
            # Update state with latest schedule
            state["schedule"] = schedule_dicts
        except Exception as e:
            logger.warning("Failed to reload schedule: %s", e)
            schedule_dicts = []
            for item in state.get("schedule", []):
                if hasattr(item, "model_dump"):
                    schedule_dicts.append(item.model_dump())
                elif isinstance(item, dict):
                    schedule_dicts.append(item)

        if schedule_dicts:
            due_items = self._scheduler.get_due_items(schedule_dicts, utcnow())
            due_ids = [item.get("id") for item in due_items if item.get("id")]
            if due_ids:
                state["due_items"] = due_ids
                return True

        return False

    async def _checkpoint_state(self, state: dict[str, Any]) -> None:
        """Save full recoverable state checkpoint.

        State is split across two files:
        - build_state.json: Agent builds only
        - current_state.json: Progenitor agent state

        Per SPEC: restart-safe checkpoints (write state every cycle).
        """
        # Build state checkpoint (agent builds only)
        build_checkpoint = {
            "build_in_progress": state.get("build_in_progress", False),
            "build_id": state.get("build_id"),
            "build_started_at": state.get("build_started_at"),
            "build_task_description": state.get("build_task_description"),
            "build_phase": state.get("build_phase"),
            "build_messages": state.get("build_messages", []),
            "ideal_state": None,
            "current_iteration": state.get("current_iteration", 0),
            "verification_score": state.get("verification_score"),
            "best_artefact_score": state.get("best_artefact_score"),
            "spec_doc": state.get("spec_doc"),
            "backlog": self._serialize_backlog(state.get("backlog", [])),
            "verification_reports": state.get("verification_reports", []),
            "lessons_learned": state.get("lessons_learned", []),
            "artefacts": state.get("artefacts", []),
            "clarification_needed": state.get("clarification_needed", False),
            "clarification_question": state.get("clarification_question"),
            "clarification_context": state.get("clarification_context"),
            "awaiting_clarification": state.get("awaiting_clarification", False),
        }

        # Only include ideal_state if it exists and is serializable
        ideal_state = state.get("ideal_state")
        if ideal_state:
            if hasattr(ideal_state, "model_dump"):
                build_checkpoint["ideal_state"] = ideal_state.model_dump()
            elif isinstance(ideal_state, dict):
                build_checkpoint["ideal_state"] = ideal_state

        await self.state_manager.save_build_state(build_checkpoint)

        # Current state checkpoint (progenitor agent state - user session)
        # NOTE: schedule is NOT included here - it's saved to schedule.json separately
        current_checkpoint = {
            "run_id": state.get("run_id"),
            "cycle_count": state.get("cycle_count", 0),
            "actions_this_cycle": state.get("actions_this_cycle", 0),
            "shutdown_requested": state.get("shutdown_requested", False),
            "last_checkpoint": utcnow_isoformat(),
            "messages": self._serialize_messages(state.get("messages", [])),
            # schedule is stored in schedule.json (system feature, not user session)
            "tool_calls": state.get("tool_calls", []),  # Session tool calls
            "attachments": state.get("attachments", []),  # Attachment path links
            "current_input": state.get("current_input"),
            "pending_response": state.get("pending_response"),
            "current_decision": self._serialize_decision(state.get("current_decision")),
            "current_build_request": state.get("build_id") if state.get("build_in_progress") else None,
        }

        await self.state_manager.save_current_state(current_checkpoint)
        logger.debug("Checkpointed state at cycle %d", current_checkpoint["cycle_count"])

    def _serialize_messages(self, messages: list) -> list:
        """Serialize message objects for checkpoint."""
        serialized = []
        for msg in messages:
            if hasattr(msg, "model_dump"):
                serialized.append(msg.model_dump())
            elif isinstance(msg, dict):
                serialized.append(msg)
        return serialized

    def _serialize_schedule(self, schedule: list) -> list:
        """Serialize schedule entries for checkpoint."""
        serialized = []
        for item in schedule:
            if hasattr(item, "model_dump"):
                serialized.append(item.model_dump())
            elif isinstance(item, dict):
                serialized.append(item)
        return serialized

    def _serialize_backlog(self, backlog: list) -> list:
        """Serialize backlog items for checkpoint."""
        serialized = []
        for item in backlog:
            if hasattr(item, "model_dump"):
                serialized.append(item.model_dump())
            elif isinstance(item, dict):
                serialized.append(item)
            else:
                # Skip items that can't be serialized
                logger.warning("Skipping non-serializable backlog item: %s", type(item))
        return serialized

    def _serialize_decision(self, decision: Any) -> Optional[dict]:
        """Serialize decision object for checkpoint."""
        if decision is None:
            return None
        if hasattr(decision, "model_dump"):
            return decision.model_dump()
        elif isinstance(decision, dict):
            return decision
        return None

    async def enqueue_input(self, message: str, timeout: Optional[float] = 30.0) -> bool:
        """Add a message to the input queue.

        Args:
            message: The message to enqueue.
            timeout: Max seconds to wait if queue is full. None = wait forever.

        Returns:
            True if message was queued, False if queue was full and timed out.

        Raises:
            asyncio.QueueFull: If timeout is 0 and queue is full (non-blocking mode).
        """
        try:
            if timeout == 0:
                # Non-blocking mode
                self._input_queue.put_nowait(message)
            elif timeout is None:
                # Wait forever
                await self._input_queue.put(message)
            else:
                # Wait with timeout
                await asyncio.wait_for(
                    self._input_queue.put(message),
                    timeout=timeout
                )
            return True
        except asyncio.QueueFull:
            logger.warning("Input queue full, message rejected (non-blocking)")
            raise
        except asyncio.TimeoutError:
            logger.warning(
                "Input queue full, message rejected after %.1fs timeout. "
                "Queue size: %d/%d",
                timeout,
                self._input_queue.qsize(),
                self._input_queue.maxsize
            )
            return False

    async def get_output(self, timeout: Optional[float] = None) -> Optional[str]:
        """Get output from the agent."""
        try:
            if timeout:
                return await asyncio.wait_for(
                    self._output_queue.get(),
                    timeout=timeout
                )
            return await self._output_queue.get()
        except asyncio.TimeoutError:
            return None

    def stop(self) -> None:
        """Signal the loop to stop."""
        self.running = False

    def get_queue_stats(self) -> dict[str, Any]:
        """Get current queue statistics for monitoring.

        Returns:
            Dict with queue sizes and capacities.
        """
        return {
            "input_queue": {
                "size": self._input_queue.qsize(),
                "maxsize": self._input_queue.maxsize,
                "full": self._input_queue.full(),
            },
            "output_queue": {
                "size": self._output_queue.qsize(),
                "maxsize": self._output_queue.maxsize,
                "full": self._output_queue.full(),
            },
            "stream_queue": {
                "size": self._stream_queue.qsize(),
                "maxsize": self._stream_queue.maxsize,
                "full": self._stream_queue.full(),
            },
            "event_queue": {
                "size": self._event_queue.qsize(),
                "maxsize": self._event_queue.maxsize,
                "full": self._event_queue.full(),
            },
        }

    async def _update_health_file(
        self,
        state: dict[str, Any],
        status: str = "running",
        error: Optional[str] = None,
    ) -> None:
        """Update health file for Docker health checks.

        Creates/updates a JSON file with last activity timestamp.
        Docker can check this file's age to determine health.

        Args:
            state: Current loop state.
            status: Health status - 'running', 'error', 'suspended', or 'stopped'.
            error: Optional error message when status is 'error'.
        """
        health_path = self.state_manager.state_dir / HEALTH_FILE
        health_data = {
            "status": status,
            "last_active": utcnow_isoformat(),
            "run_id": state.get("run_id"),
            "cycle_count": state.get("cycle_count", 0),
            "pid": __import__("os").getpid(),
            "idle_cycles": state.get("idle_cycles", 0),
            "current_backoff_seconds": state.get("current_backoff_seconds"),
            "backlog_count": len(state.get("backlog", []) or []),
            "schedule_count": len(state.get("schedule", []) or []),
            "queues": self.get_queue_stats(),
        }

        if error:
            health_data["error"] = error

        try:
            health_path.write_text(json.dumps(health_data))
        except Exception as e:
            logger.warning("Failed to update health file: %s", e)
