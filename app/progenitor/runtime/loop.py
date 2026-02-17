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
- error handling with user notification
- graceful cancellation/suspend with state persistence
"""

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast

from .scheduler import Scheduler
from .time_utils import utcnow, utcnow_isoformat

if TYPE_CHECKING:
    from ..config import ProgenitorConfig

logger = logging.getLogger(__name__)

# Health file location (relative to state directory)
HEALTH_FILE = "health.json"

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
        worktrees_dir: Path | None = None,
        copilot_acp_manager=None,
        background_graph=None,
    ):
        self.graph = graph
        self.background_graph = background_graph
        self.state_manager = state_manager
        self.config = config
        self.llm_client = llm_client
        self.worktrees_dir = worktrees_dir or config.storage.worktree_dir
        self.copilot_acp_manager = copilot_acp_manager
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
        self._scheduler = Scheduler(timezone=config.runtime.timezone)

        # Stream callback for real-time LLM streaming
        # Nodes can use this to stream tokens directly to the client
        self._stream_callback: Callable[[str, bool], Awaitable[None]] | None = None

        # Streaming state - controlled by the loop, accessed by nodes
        self._streaming_enabled = False
        self._response_was_streamed = False
        self._stream_final_sent = False
        self._streamed_response_content: str = ""  # Accumulated streamed text for saving to messages

        # Captures the most recent pending_response emitted by the graph during a cycle.
        # Used for background job cycles where we don't stream to the client.
        self._last_cycle_response: str | None = None

        # Background build runner (initialized in run_forever)
        self._background_runner: Any = None

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
                state_manager=self.state_manager,
                config=self.config,
                llm_client=self.llm_client,
                worktrees_dir=self.worktrees_dir,
                copilot_acp_manager=self.copilot_acp_manager,
            )
            set_background_runner(self._background_runner)
            self._background_runner.set_completion_callback(self._handle_build_completion)
            logger.info("Background build runner initialized with inner loop graph")

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
            "current_input_role": None,
            "current_input_metadata": None,
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

        # Merge build-specific fields from build_state (nested structure)
        for key in ["build_in_progress", "build_phase", "build_started_at",
                "task", "iteration", "spec", "phases",
                    "history", "best", "clarification"]:
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
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        except asyncio.CancelledError:
            logger.info("Loop cancelled by signal")
            # Graceful shutdown - suspend any active build
            await self._suspend_active_build("Cancelled by signal")
            input_task.cancel()
            background_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(input_task, background_task, return_exceptions=True)

        # Final checkpoint
        await self._checkpoint_state(self._state)
        await self._update_health_file(self._state, status="stopped")
        await self._push_event("loop_stopped", "Perpetual loop stopped")
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
                    self._streamed_response_content = ""

                    # Run graph cycle
                    await self._run_graph_cycle(run_id, source="user_input")

                    # Disable streaming mode
                    self._streaming_enabled = False
                    self._response_was_streamed = False
                    self._stream_final_sent = False
                    self._streamed_response_content = ""

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
        max_backoff = runtime.max_backoff_seconds
        backoff_multiplier = runtime.backoff_multiplier
        last_idle_log_backoff: float | None = None

        if not self.background_graph:
            logger.warning("Background worker disabled (no background_graph configured)")
            return

        logger.info("Background worker started")
        await self._push_event("background_worker_started", "Background worker is now running")

        while self.running:
            try:
                # Prioritize user input responsiveness: if a user message is queued,
                # don't start a background cycle that would contend for the graph lock.
                if not self._input_queue.empty():
                    logger.debug("User input queued; yielding background cycle")
                    await asyncio.sleep(0.1)
                    continue

                # Try to acquire lock (non-blocking check first)
                if self._graph_lock.locked():
                    # User input is being processed, wait a bit
                    logger.debug("Graph lock held, waiting for user input to complete")
                    await asyncio.sleep(0.5)
                    continue

                sleep_seconds: float | None = None

                async with self._graph_lock:
                    # Background cycles must not stream to the client queues.
                    self._streaming_enabled = False
                    self._response_was_streamed = False
                    self._stream_final_sent = False
                    self._streamed_response_content = ""

                    self._state["cycle_count"] = self._state.get("cycle_count", 0) + 1
                    self._state["actions_this_cycle"] = 0

                    await self._run_background_graph_cycle(run_id, source="background")

                    had_work = bool(self._state.get("bg_had_work"))
                    if had_work:
                        # Reset backoff when work was processed
                        self._state["current_backoff_seconds"] = runtime.idle_backoff_seconds
                        self._state["idle_cycles"] = 0
                        # Small throttle to avoid a tight loop if many items are due.
                        sleep_seconds = 0.1
                    else:
                        # No work - increase backoff exponentially
                        self._state["idle_cycles"] = self._state.get("idle_cycles", 0) + 1
                        current_backoff = float(
                            self._state.get("current_backoff_seconds", runtime.idle_backoff_seconds)
                        )
                        next_backoff = min(current_backoff * backoff_multiplier, max_backoff)
                        self._state["current_backoff_seconds"] = next_backoff

                        # Update health periodically even when idle
                        await self._update_health_file(self._state)

                        if next_backoff >= 60 and next_backoff != last_idle_log_backoff:
                            logger.info(
                                "Background worker idle; next check in %.1f s (idle_cycles=%d)",
                                next_backoff,
                                self._state.get("idle_cycles", 0),
                            )
                            last_idle_log_backoff = next_backoff
                        else:
                            logger.debug("Background worker idle, sleeping %.1f s", next_backoff)
                        sleep_seconds = next_backoff

                # Sleep OUTSIDE the graph lock so user input can be processed.
                if sleep_seconds is not None and sleep_seconds > 0:
                    await asyncio.sleep(sleep_seconds)

            except asyncio.CancelledError:
                logger.info("Background worker cancelled")
                break
            except Exception as e:
                logger.error("Background worker error: %s", e, exc_info=True)
                await self._push_event("error", f"Background worker error: {e}")
                await self._update_health_file(self._state, status="error", error=str(e))
                error_backoff = float(
                    self._state.get("current_backoff_seconds", runtime.idle_backoff_seconds)
                )
                await asyncio.sleep(error_backoff)

        logger.info("Background worker stopped")

    async def _run_graph_cycle(self, _run_id: str, source: str = "unknown") -> None:
        """Run a single graph cycle.

        Args:
            run_id: The run ID for this session.
            source: Source of the cycle (e.g., 'user_input', 'background_backlog').
        """
        cycle_num = self._state["cycle_count"]
        thread_id = f"cycle_{cycle_num}"
        logger.info("Running graph cycle %d (source: %s)", cycle_num, source)

        # Reset per-cycle capture
        self._last_cycle_response = None

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

                # Check for response to send - stream it if not already streamed
                pending = self._state.get("pending_response")
                # Prefer explicit pending_response (final user response) even if some node streamed tokens.
                if pending:
                    pending_text = str(pending)
                    self._last_cycle_response = pending_text
                    self._state["last_cycle_response"] = pending_text
                    if self._streaming_enabled:
                        await self._stream_response(pending_text)
                    self._state["pending_response"] = None
                elif self._response_was_streamed and not self._stream_final_sent:
                    # Response was already streamed by a node, just send final marker ONCE
                    if self._streaming_enabled:
                        await self.stream_final()
                    self._stream_final_sent = True
                    self._state["pending_response"] = None

            # Checkpoint state after cycle
            await self._checkpoint_state(self._state)
            await self._update_health_file(self._state)

        except Exception as e:
            logger.error("Graph cycle %d failed: %s", cycle_num, e, exc_info=True)
            raise

    async def _run_background_graph_cycle(self, run_id: str, source: str = "background") -> None:
        """Run a background job graph cycle.

        Background cycles must not write to the user stream/output queues.
        They still checkpoint state and may emit IPC events via nodes.
        """
        if not self.background_graph:
            raise RuntimeError("background_graph not configured")

        cycle_num = self._state["cycle_count"]
        thread_id = f"bg_cycle_{cycle_num}"
        logger.info("Running background graph cycle %d (source: %s)", cycle_num, source)

        self._last_cycle_response = None

        try:
            async for event in self.background_graph.astream(
                self._state,
                config={"configurable": {"thread_id": thread_id}},
            ):
                for node_name, node_output in event.items():
                    output_info = list(node_output.keys()) if isinstance(node_output, dict) else type(node_output)
                    logger.info("BG GRAPH EVENT: node=%s, output_keys=%s", node_name, output_info)
                    if isinstance(node_output, dict):
                        self._state.update(node_output)

                pending = self._state.get("pending_response")
                if pending:
                    pending_text = str(pending)
                    self._last_cycle_response = pending_text
                    self._state["last_cycle_response"] = pending_text
                    self._state["pending_response"] = None

            await self._checkpoint_state(self._state)
            await self._update_health_file(self._state)

        except Exception as e:
            logger.error("Background graph cycle %d failed: %s", cycle_num, e, exc_info=True)
            raise

    async def _push_event(
        self,
        event_type: str,
        content: str = "",
        metadata: dict[str, Any] | None = None
    ) -> None:
        """Push an event to clients via the event queue.

        Args:
            event_type: Type of event (e.g., 'backlog_update', 'work_started').
            content: Human-readable description.
            metadata: Additional event data.
        """
        metadata_safe: dict[str, Any] = {}
        if metadata:
            try:
                metadata_safe = json.loads(json.dumps(metadata, default=str))
            except Exception:
                metadata_safe = {"raw": str(metadata)}

        event = {
            "event_type": event_type,
            "content": content,
            "metadata": metadata_safe,
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

    async def get_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Get an event from the event queue.

        Args:
            timeout: Max seconds to wait. None = wait forever.

        Returns:
            Event dict or None on timeout.
        """
        try:
            if timeout:
                return cast(dict[str, Any] | None, await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=timeout
                ))
            return cast(dict[str, Any] | None, await self._event_queue.get())
        except TimeoutError:
            return None

    async def _get_input(self, timeout: float) -> str | None:
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
        except TimeoutError:
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

    async def wait_cycle_complete(self, timeout: float | None = None) -> bool:
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
        except TimeoutError:
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

    async def get_stream_chunk(self, timeout: float | None = None) -> dict | None:
        """Get a streaming chunk from the agent.

        Returns:
            Dict with 'type', 'content', and 'is_final' keys, or None on timeout.
        """
        try:
            if timeout:
                return cast(dict[str, Any] | None, await asyncio.wait_for(
                    self._stream_queue.get(),
                    timeout=timeout
                ))
            return cast(dict[str, Any] | None, await self._stream_queue.get())
        except TimeoutError:
            return None

    async def _checkpoint_state(self, state: dict[str, Any]) -> None:
        """Save full recoverable state checkpoint.

        State is split across two files:
        - build_state.json: Agent builds only
        - current_state.json: Progenitor agent state

        Per SPEC: restart-safe checkpoints (write state every cycle).

        IMPORTANT:
        - When build is SUSPENDED, we DON'T touch build_state.json at all since
          _suspend_active_build already handled it with a safe read-modify-write.
        - When build is not in progress (idle, error, etc.), use update_build_state
          to safely merge status changes without overwriting existing data.
        - When actively building, do a full checkpoint.
        """
        # Check build status
        is_suspended = state.get("build_phase") == "suspended"
        is_actively_building = state.get("build_in_progress", False)

        if is_suspended:
            # SUSPENDED: _suspend_active_build already saved the file correctly
            logger.debug("Checkpoint: Skipping build state (already saved by suspend handler)")
        elif not is_actively_building:
            # NOT ACTIVELY BUILDING (idle, error, etc.): Use safe update pattern
            # Read current file, merge status, write back - preserves existing data
            status_update = {
                "build_in_progress": False,
                "build_phase": state.get("build_phase"),
            }
            # Include build_settings if it exists in the in-memory state
            build_settings = state.get("build_settings")
            if build_settings:
                if hasattr(build_settings, "model_dump"):
                    status_update["build_settings"] = build_settings.model_dump()
                elif isinstance(build_settings, dict):
                    status_update["build_settings"] = build_settings
            await self.state_manager.update_build_state(status_update)
            logger.debug("Checkpoint: Updated build state via merge (not actively building)")
        else:
            # Full checkpoint when actively building
            build_checkpoint = {
                "build_in_progress": state.get("build_in_progress", False),
                "build_started_at": state.get("build_started_at"),
                "build_phase": state.get("build_phase"),
                "task_id": state.get("task_id"),
                "should_iterate": state.get("should_iterate", True),
                "iteration_stop_reason": state.get("iteration_stop_reason"),
                "backlog": self._serialize_backlog(state.get("backlog", [])),
                # Nested objects
                "task": state.get("task", {}),
                "iteration": state.get("iteration", {}),
                "spec": state.get("spec", {}),
                "phases": state.get("phases", {}),
                "history": state.get("history", {}),
                "best": state.get("best", {}),
                "clarification": state.get("clarification", {}),
            }

            # BL-007: Include build_settings for structured build configuration
            build_settings = state.get("build_settings")
            if build_settings:
                if hasattr(build_settings, "model_dump"):
                    build_checkpoint["build_settings"] = build_settings.model_dump()
                elif isinstance(build_settings, dict):
                    build_checkpoint["build_settings"] = build_settings

            # Only include ideal_state if it exists and is serializable
            from ..state.helpers import get_ideal_state, set_ideal_state

            ideal_state = get_ideal_state(state)
            if ideal_state:
                phases = set_ideal_state(build_checkpoint.get("phases"), ideal_state)
                build_checkpoint["phases"] = phases

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
            "current_build_request": (state.get("build_settings") or {}).get("build_id") if state.get("build_in_progress") else None,
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

    def _serialize_decision(self, decision: Any) -> dict | None:
        """Serialize decision object for checkpoint."""
        if decision is None:
            return None
        if hasattr(decision, "model_dump"):
            return cast(dict, decision.model_dump())
        elif isinstance(decision, dict):
            return decision
        return None

    async def enqueue_input(self, message: str, timeout: float | None = 30.0) -> bool:
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
        except TimeoutError:
            logger.warning(
                "Input queue full, message rejected after %.1fs timeout. "
                "Queue size: %d/%d",
                timeout,
                self._input_queue.qsize(),
                self._input_queue.maxsize
            )
            return False

    async def get_output(self, timeout: float | None = None) -> str | None:
        """Get output from the agent."""
        try:
            if timeout:
                return cast(str | None, await asyncio.wait_for(
                    self._output_queue.get(),
                    timeout=timeout
                ))
            return cast(str | None, await self._output_queue.get())
        except TimeoutError:
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
        error: str | None = None,
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

    # =========================================================================
    # Build Error Handling and User Notification
    # =========================================================================

    async def handle_build_error(
        self,
        error_message: str,
        build_id: str | None = None,
        notify_user: bool = True,
    ) -> None:
        """Handle a build error by updating state and notifying user.

        BL-007: When an error occurs:
        1. Add error to backlog for user notification
        2. Update build_settings status to ERROR
        3. Write error status to state file
        4. Cancel the build loop (inner/outer)

        Args:
            error_message: Description of the error
            build_id: Optional build ID (uses current if not provided)
            notify_user: Whether to push notification to user
        """
        logger.error("BUILD ERROR: %s (build_id=%s)", error_message, build_id or "current")

        # Update build settings in state
        build_settings = self._state.get("build_settings")
        if build_settings:
            if isinstance(build_settings, dict):
                build_settings["status"] = "error"
                build_settings["error_message"] = error_message
                build_settings["updated_at"] = utcnow_isoformat()
            elif hasattr(build_settings, "mark_error"):
                build_settings.mark_error(error_message)
                self._state["build_settings"] = build_settings.model_dump()

        # Mark build as not in progress
        self._state["build_in_progress"] = False
        self._state["build_phase"] = "error"

        # Create error work item for user notification
        if notify_user:
            from ..state.models import WorkItem, WorkItemStatus, WorkItemType

            error_item = WorkItem(
                id=f"error_{uuid.uuid4().hex[:8]}",
                item_type=WorkItemType.DIRECT_ANSWER,
                status=WorkItemStatus.COMPLETED,
                description=f"Build Error: {error_message}",
                context={
                    "error_type": "build_error",
                    "build_id": build_id or (self._state.get("build_settings") or {}).get("build_id"),
                    "notify_user": True,
                },
                result={"error": error_message, "notify": True},
            )

            # Add to backlog for respond node to pick up
            backlog = list(self._state.get("backlog", []))
            backlog.append(error_item)
            self._state["backlog"] = backlog

            # Push event to notify user immediately
            await self._push_event(
                "build_error",
                f"❌ Build error: {error_message}",
                {
                    "build_id": build_id or (self._state.get("build_settings") or {}).get("build_id"),
                    "error": error_message,
                }
            )

        # Checkpoint state with error
        await self._checkpoint_state(self._state)
        await self._update_health_file(self._state, status="error", error=error_message)

        logger.info("BUILD ERROR: State persisted with error status")

    async def _suspend_active_build(self, reason: str = "Cancelled by signal") -> None:
        """Suspend any active build gracefully on cancellation.

        BL-007: When receiving a cancellation signal:
        1. Read the ACTUAL file state (not in-memory which may be stale)
        2. Update ONLY the suspend-related fields
        3. Write back to preserve all existing data

        Args:
            reason: Reason for suspension
        """
        logger.info("SUSPEND: Suspending active build: %s", reason)

        try:
            # CRITICAL: Read the actual file, not in-memory state which may be incomplete
            build_state = await self.state_manager.get_build_state()

            # Check if there's actually a build to suspend
            if not build_state.get("build_in_progress"):
                logger.info("SUSPEND: No active build to suspend")
                return

            # Update suspend status fields IN THE FILE DATA
            build_state["build_in_progress"] = False
            build_state["build_phase"] = "suspended"

            # Update build_settings if it exists in the file
            if "build_settings" in build_state and build_state["build_settings"]:
                build_settings = build_state["build_settings"]
                if isinstance(build_settings, dict):
                    build_settings["status"] = "suspended"
                    build_settings["error_message"] = reason
                    build_settings["updated_at"] = utcnow_isoformat()

            # Add suspension message to build_messages
            build_messages = build_state.get("build_messages", [])
            build_messages.append({
                "event": "suspended",
                "timestamp": utcnow_isoformat(),
                "reason": "shutdown_requested",
                "iteration": build_state.get("iteration", {}).get("current", 0),
            })
            build_state["build_messages"] = build_messages

            # Add last_build_result
            build_state["last_build_result"] = {
                "build_id": build_state.get("build_settings", {}).get("build_id") if build_state.get("build_settings") else None,
                "status": "cancelled",
                "completed_at": utcnow_isoformat(),
                "output_summary": None,
                "error_message": "Build was cancelled",
            }

            # Write the complete updated state back to file
            await self.state_manager.save_build_state(build_state)
            logger.info("SUSPEND: Build state saved successfully (read-modify-write)")

            # Also update in-memory state for consistency
            self._state["build_in_progress"] = False
            self._state["build_phase"] = "suspended"

        except Exception as e:
            logger.warning("SUSPEND: Failed to update build state: %s", e)

        # Push event for any connected clients
        await self._push_event(
            "build_suspended",
            f"⏸️ Build suspended: {reason}",
            {
                "build_id": (self._state.get("build_settings") or {}).get("build_id"),
                "reason": reason,
            }
        )

        # Update health file with suspended status
        await self._update_health_file(self._state, status="suspended", error=reason)

        logger.info("SUSPEND: Build suspended successfully")
