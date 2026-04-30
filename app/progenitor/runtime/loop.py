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
import copy
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from ..state.models import redact_secrets
from ..stream_events import build_reasoning_summary_event
from ..streaming_utils import extract_reasoning_summary_text, extract_reasoning_token_count
from .a2a_completion import A2ACompletionBridge
from .a2a_tasks import A2ATaskTracker
from .graph_results import (
    coerce_messages_list,
    extract_message_text,
    get_last_ai_message,
    serialize_messages,
    summarize_message_schema,
)
from .health import update_health_file
from .scheduled_work import SCHEDULED_INPUT_PREFIX, ScheduledWorkRunner
from .streaming import StreamEventRouter
from .time_utils import utcnow_isoformat

if TYPE_CHECKING:
    from ..config import ProgenitorConfig

logger = logging.getLogger(__name__)


class QueuedInput(NamedTuple):
    """Input message bundled with its enqueue time."""

    content: Any
    enqueue_time: float


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
        self._input_queue: asyncio.Queue[QueuedInput] = asyncio.Queue(
            maxsize=runtime.max_input_queue_size
        )
        self._output_queue: asyncio.Queue = asyncio.Queue(maxsize=runtime.max_output_queue_size)
        self._stream_queue: asyncio.Queue = asyncio.Queue(maxsize=runtime.max_stream_queue_size)
        # Event queue for pushing background events to clients
        # Events include: backlog updates, scheduled task triggers, work progress
        self._event_queue: asyncio.Queue = asyncio.Queue(
            maxsize=runtime.max_output_queue_size  # Same size as output queue
        )
        # Event to signal when a graph cycle completes (for IPC synchronization)
        self._cycle_complete_event: asyncio.Event = asyncio.Event()

        self.stream = StreamEventRouter(self._stream_queue)

        # Captures the most recent pending_response emitted by the graph during a cycle.
        # Used for background job cycles where we don't stream to the client.
        self._last_cycle_response: str | None = None
        self._last_reasoning_trace: str | None = None

        # Lock to prevent concurrent graph cycles
        self._graph_lock = asyncio.Lock()

        # Timing metadata captured when an input is dequeued.
        self._current_input_wait_seconds = 0.0
        self._current_input_pending_count = 0
        self._idle_cycles = 0
        self._current_backoff_seconds = float(config.runtime.idle_backoff_seconds)

        # Shared state for the loop (protected by _graph_lock)
        self._state: dict[str, Any] = {}
        self._a2a_task_tracker: A2ATaskTracker | None = None
        self._a2a_task_poller: asyncio.Task[None] | None = None
        self.scheduled_work = ScheduledWorkRunner(
            config=config,
            state_manager=state_manager,
            enqueue_input=lambda message, timeout: self.enqueue_input(message, timeout=timeout),
            push_event=self._push_event,
            is_running=lambda: self.running,
        )
        self.a2a_completion = A2ACompletionBridge(
            state_manager=state_manager,
            get_state=lambda: self._state,
            graph_lock=self._graph_lock,
        )

    async def run_forever(self) -> None:
        """Run the perpetual loop.

        Core-only profile: only processes explicit user input messages.
        """
        self.running = True

        boot_thread_id = str(uuid.uuid4())

        # Initialize shared state
        self._state = {
            "thread_id": boot_thread_id,
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": False,
            "messages": [],
            "agents": [],
            "current_input_role": None,
            "current_input_metadata": None,
            "input_wait_seconds": 0.0,
            "pending_messages_in_queue": 0,
        }

        # Load persisted state (recover from previous run)
        current_state = await self.state_manager.get_current_state()
        self._state.update(current_state)
        restored_thread_id = str(
            current_state.get("thread_id") or self._state.get("thread_id") or ""
        ).strip()
        if restored_thread_id:
            self._state["thread_id"] = restored_thread_id

        logger.info(
            "Starting perpetual loop (boot_thread_id: %s, conversation_thread_id: %s)",
            boot_thread_id,
            self._state.get("thread_id"),
        )

        # Update health immediately on startup
        await self._update_health_file()

        # Push startup event
        await self._push_event(
            "loop_started", f"Perpetual loop started (thread_id: {boot_thread_id})"
        )

        # Core-only input handling plus scheduler dispatch loop.
        input_task = asyncio.create_task(self._input_handler_loop(boot_thread_id))
        scheduler_task: asyncio.Task | None = None
        poller_task: asyncio.Task[None] | None = None
        if self.scheduled_work.is_enabled():
            scheduler_task = asyncio.create_task(self.scheduled_work.run_loop())
            logger.info("Scheduler loop started")
        self._a2a_task_tracker = A2ATaskTracker(
            self.state_manager,
            self.llm_client,
            completion_callback=self.a2a_completion.record_completed_task,
        )
        self._a2a_task_poller = asyncio.create_task(self._a2a_task_tracker.run())
        poller_task = self._a2a_task_poller

        try:
            if scheduler_task:
                done, pending = await asyncio.wait(
                    [
                        task
                        for task in [input_task, scheduler_task, poller_task]
                        if task is not None
                    ],
                    return_when=asyncio.FIRST_EXCEPTION,
                )
                for task in pending:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                for task in done:
                    exc = task.exception()
                    if exc:
                        raise exc
            else:
                await asyncio.gather(
                    input_task,
                    poller_task,
                )
        except asyncio.CancelledError:
            logger.info("Loop cancelled by signal")
            input_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await input_task
            if scheduler_task:
                scheduler_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await scheduler_task
            if poller_task:
                poller_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poller_task
        finally:
            if poller_task and not poller_task.done():
                poller_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poller_task

        # Final checkpoint
        await self._checkpoint_state(self._state)
        await self._update_health_file(status="stopped")
        await self._push_event("loop_stopped", "Perpetual loop stopped")
        logger.info("Loop terminated")

    async def _input_handler_loop(self, boot_thread_id: str) -> None:
        """Handle user input messages.

        This task waits for user input and processes it through the graph.
        Core-only profile: no background worker.
        """
        runtime = self.config.runtime
        last_health = time.monotonic()

        while self.running:
            try:
                queued_input = await self._get_input(timeout=1.0)

                if not queued_input:
                    now = time.monotonic()
                    self._idle_cycles += 1
                    if now - last_health >= float(self.config.runtime.checkpoint_interval_seconds):
                        await self._update_health_file()
                        last_health = now
                    continue

                user_input, input_metadata = self._extract_input_payload(queued_input)
                if not user_input:
                    continue

                input_role = str((input_metadata or {}).get("role") or "").strip() or None
                if input_role:
                    input_metadata = {
                        key: value for key, value in (input_metadata or {}).items() if key != "role"
                    }
                requested_thread_id = (
                    str((input_metadata or {}).get("thread_id") or "").strip() or None
                )

                logger.info(
                    "Got user input: %s", user_input[:50] if len(user_input) > 50 else user_input
                )
                is_scheduled_input = user_input.startswith(SCHEDULED_INPUT_PREFIX)
                scheduled_message = (
                    user_input[len(SCHEDULED_INPUT_PREFIX) :].strip() if is_scheduled_input else ""
                )

                async with self._graph_lock:
                    self._current_backoff_seconds = float(runtime.idle_backoff_seconds)
                    self._idle_cycles = 0

                    self._state["current_input"] = user_input
                    self._state["current_input_role"] = input_role
                    self._state["current_input_metadata"] = input_metadata or None
                    self._state["cycle_count"] = self._state.get("cycle_count", 0) + 1
                    self._state["actions_this_cycle"] = 0
                    self._state["input_wait_seconds"] = self._current_input_wait_seconds
                    self._state["pending_messages_in_queue"] = self._current_input_pending_count

                    self.stream.reset(enabled=not is_scheduled_input)

                    is_isolated_thread = bool(
                        requested_thread_id
                        and requested_thread_id != str(self._state.get("thread_id") or "")
                    )

                    try:
                        if is_isolated_thread and requested_thread_id:
                            await self._run_isolated_graph_cycle(
                                boot_thread_id,
                                source="user_input",
                                thread_id=requested_thread_id,
                            )
                        else:
                            await self._run_graph_cycle(
                                boot_thread_id,
                                source="user_input",
                                thread_id=requested_thread_id,
                            )
                    except Exception as e:
                        safe_error = redact_secrets(str(e))
                        logger.error(
                            "Graph cycle failed for user input: %s", safe_error, exc_info=True
                        )
                        await self._push_event("error", safe_error)

                        if self.stream.enabled and not self.stream.final_sent:
                            if self.stream.response_was_streamed:
                                await self.stream.stream_final()
                            else:
                                await self.stream.stream_response(f"Error: {safe_error}")
                            self.stream.final_sent = True

                        self._last_cycle_response = f"Error: {safe_error}"
                        self._state["last_cycle_response"] = self._last_cycle_response
                        await self._checkpoint_state(self._state)
                        await self._update_health_file(status="error")
                    finally:
                        if is_scheduled_input:
                            response_text = str(
                                self._state.get("last_cycle_response") or ""
                            ).strip()
                            result_status = (
                                "completed" if response_text else "completed_no_response"
                            )
                            await self._push_event(
                                "scheduled_response",
                                response_text or "(no response)",
                                {
                                    "schedule_message": scheduled_message or None,
                                    "status": result_status,
                                },
                            )
                            await self._push_event(
                                "work_completed",
                                f"Completed scheduled work: {scheduled_message or 'Scheduled task'}",
                                {
                                    "work_type": "scheduled",
                                    "status": result_status,
                                },
                            )

                        self.stream.reset(enabled=False)

                        logger.info("Graph cycle complete, signaling")
                        await self._signal_cycle_complete()

            except asyncio.CancelledError:
                logger.info("Input handler cancelled")
                break
            except Exception as e:
                logger.error("Input handler error: %s", e, exc_info=True)
                await self._push_event("error", f"Input handler error: {e}")
                await asyncio.sleep(1.0)

    def _extract_input_payload(self, queued_input: Any) -> tuple[str, dict[str, Any]]:
        """Normalize queued input into plain content plus optional metadata."""

        if isinstance(queued_input, dict):
            content = str(queued_input.get("content") or "").strip()
            metadata = dict(queued_input.get("metadata") or {})
            return content, metadata
        return str(queued_input).strip(), {}

    def _snapshot_conversation_state(self) -> dict[str, Any]:
        """Capture the main-thread conversation state before an isolated execution."""

        keys = (
            "thread_id",
            "messages",
            "attachments",
            "tool_calls",
            "current_input",
            "current_input_role",
            "current_input_metadata",
            "pending_response",
            "last_reasoning_trace",
        )
        return {key: copy.deepcopy(self._state.get(key)) for key in keys}

    def _restore_conversation_state(self, snapshot: dict[str, Any]) -> None:
        """Restore a previously captured main-thread conversation state."""

        for key, value in snapshot.items():
            self._state[key] = value

    async def _run_isolated_graph_cycle(
        self,
        boot_thread_id: str,
        *,
        source: str,
        thread_id: str,
    ) -> None:
        """Run a cycle against a temporary thread without overwriting the main thread state."""

        snapshot = self._snapshot_conversation_state()
        try:
            self._state["thread_id"] = thread_id
            self._state["messages"] = []
            self._state["attachments"] = []
            self._state["tool_calls"] = []
            self._state["pending_response"] = None
            self._state["last_reasoning_trace"] = None
            await self._run_graph_cycle(
                boot_thread_id,
                source=source,
                thread_id=thread_id,
                checkpoint_state=False,
            )
        finally:
            self._restore_conversation_state(snapshot)
            await self._checkpoint_state(self._state)
            await self._update_health_file()

    async def _run_graph_cycle(
        self,
        _boot_thread_id: str,
        source: str = "unknown",
        thread_id: str | None = None,
        checkpoint_state: bool = True,
    ) -> None:
        """Run a single graph cycle.

        Args:
            _boot_thread_id: The boot thread ID for this process.
            source: Source of the cycle (e.g., 'user_input', 'background_backlog').
            thread_id: Optional request-scoped thread override.
            checkpoint_state: Whether to persist the current in-memory state after the cycle.
        """
        cycle_num = self._state["cycle_count"]
        current_input = self._state.get("current_input") if source == "user_input" else None

        # DeepAgent uses the LangGraph checkpointer keyed by thread_id.
        # Keep the default interactive thread aligned to persisted state, while
        # allowing workspace automation requests to supply a request-scoped thread override.
        effective_thread_id = str(thread_id or self._state.get("thread_id") or "progenitor_main")
        logger.info("Running graph cycle %d (source: %s)", cycle_num, source)

        # Reset per-cycle capture
        self._last_cycle_response = None

        # Build graph input: for user_input cycles, pass only the new HumanMessage.
        # The checkpointer (per stable thread_id) maintains conversation history.
        # Passing the full self._state would flood the model with all accumulated messages.
        if current_input:
            from langchain_core.messages import HumanMessage

            graph_input: dict = {"messages": [HumanMessage(content=current_input)]}
        else:
            graph_input = {}

        try:
            astream_kwargs: dict[str, Any] = {
                "config": {"configurable": {"thread_id": effective_thread_id}},
                "stream_mode": ["messages", "updates", "custom"],
            }
            pending_stream_final = False

            async for stream_item in self.graph.astream(
                graph_input,
                **astream_kwargs,
            ):
                mode = "updates"
                event = stream_item
                if isinstance(stream_item, tuple) and len(stream_item) == 2:
                    mode, event = stream_item

                if mode == "messages":
                    await self.stream.handle_message_stream_event(event)
                    continue

                if mode == "custom":
                    await self.stream.handle_custom_stream_event(event)
                    continue

                if mode != "updates" or not isinstance(event, dict):
                    logger.debug("Ignoring graph stream item for mode=%s", mode)
                    continue

                # Process graph events
                for node_name, node_output in event.items():
                    output_info = (
                        list(node_output.keys())
                        if isinstance(node_output, dict)
                        else type(node_output)
                    )
                    logger.info("GRAPH EVENT: node=%s, output_keys=%s", node_name, output_info)
                    if isinstance(node_output, dict):
                        if "messages" in node_output:
                            node_output = {
                                **node_output,
                                "messages": coerce_messages_list(node_output.get("messages")),
                            }
                        self._state.update(node_output)
                        if self.stream.enabled:
                            await self.stream.send_stream_event(
                                "node_update",
                                metadata={
                                    "node_name": node_name,
                                    "output_keys": output_info,
                                    "contains_messages": "messages" in node_output,
                                    "contains_pending_response": "pending_response" in node_output,
                                },
                            )

                # Check for response to send - stream it if not already streamed
                pending = self._state.get("pending_response")
                # Prefer explicit pending_response (final user response) even if some node streamed tokens.
                if pending:
                    pending_text = str(pending)
                    self._last_cycle_response = pending_text
                    self._state["last_cycle_response"] = pending_text
                    if self.stream.enabled:
                        await self.stream.ensure_response_streamed(pending_text)
                        pending_stream_final = True
                    self._state["pending_response"] = None
                elif self.stream.response_was_streamed and not self.stream.final_sent:
                    streamed_text = self.stream.streamed_response_content
                    if streamed_text:
                        self._last_cycle_response = streamed_text
                        self._state["last_cycle_response"] = streamed_text
                    if self.stream.enabled:
                        pending_stream_final = True
                    self._state["pending_response"] = None

            # Ensure streaming clients never hang waiting for an is_final marker
            # and capture a final text response even when this cycle has no live
            # stream consumer (for example scheduled tasks).
            last_ai_msg = get_last_ai_message(self._state.get("messages"))
            response_text: str | None = self._last_cycle_response
            if not self.stream.final_sent:
                response_text = extract_message_text(last_ai_msg)
                if response_text:
                    logger.info(
                        "Extracting AI response from messages (%d chars)", len(response_text)
                    )
                    if self.stream.enabled:
                        await self.stream.ensure_response_streamed(response_text)
                        pending_stream_final = True
                    self._last_cycle_response = response_text
                    self._state["last_cycle_response"] = response_text
                elif self.stream.enabled:
                    pending_stream_final = True

            reasoning_trace = (
                extract_reasoning_summary_text(last_ai_msg) if last_ai_msg is not None else None
            )
            self._last_reasoning_trace = reasoning_trace
            self._state["last_reasoning_trace"] = reasoning_trace
            reasoning_tokens = (
                extract_reasoning_token_count(last_ai_msg) if last_ai_msg is not None else None
            )
            if self.stream.enabled and last_ai_msg is not None and bool(reasoning_trace):
                summary_event = build_reasoning_summary_event(
                    reasoning_trace or "",
                    metadata={
                        "reasoning_tokens": reasoning_tokens,
                    },
                )
                await self.stream.send_stream_event(
                    summary_event["event_type"],
                    content=summary_event["content"],
                    metadata=cast(dict[str, Any], summary_event["metadata"]),
                )
            if self.stream.enabled and pending_stream_final and not self.stream.final_sent:
                await self.stream.stream_final()
                self.stream.final_sent = True
            if reasoning_trace:
                logger.info("Extracted reasoning trace (%d chars)", len(reasoning_trace))
            elif last_ai_msg is not None and self.config.llm.reasoning_enabled:
                logger.info(
                    "No reasoning trace found in assistant message schema: %s",
                    json.dumps(summarize_message_schema(last_ai_msg), default=str),
                )

            # If the thread_id changed during this cycle (e.g. the LLM invoked the
            # new_conversation system tool), treat it as a hard context reset.
            current_thread_id = str(self._state.get("thread_id") or "")
            if current_thread_id != effective_thread_id:
                self._state["messages"] = []
                self._state["attachments"] = []
                self._state["tool_calls"] = []

            if checkpoint_state:
                await self._checkpoint_state(self._state)
                await self._update_health_file()

        except Exception as e:
            logger.error("Graph cycle %d failed: %s", cycle_num, e, exc_info=True)
            raise

    async def _push_event(
        self, event_type: str, content: str = "", metadata: dict[str, Any] | None = None
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
                event_type,
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
                return cast(
                    dict[str, Any] | None,
                    await asyncio.wait_for(self._event_queue.get(), timeout=timeout),
                )
            return cast(dict[str, Any] | None, await self._event_queue.get())
        except TimeoutError:
            return None

    async def _get_input(self, timeout: float) -> Any:
        """Get user input with timeout.

        Caps timeout at 30 seconds to ensure health updates happen frequently.
        """
        # Cap timeout to ensure health gets updated frequently
        effective_timeout = min(timeout, 30.0)
        try:
            queued_input = await asyncio.wait_for(
                self._input_queue.get(), timeout=effective_timeout
            )
            self._current_input_wait_seconds = time.monotonic() - queued_input.enqueue_time
            self._current_input_pending_count = self._input_queue.qsize()
            return queued_input.content
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
                self._output_queue.maxsize,
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
                await asyncio.wait_for(self._cycle_complete_event.wait(), timeout=timeout)
            else:
                await self._cycle_complete_event.wait()
            # Clear the event for the next cycle
            self._cycle_complete_event.clear()
            return True
        except TimeoutError:
            return False

    async def _checkpoint_state(self, state: dict[str, Any]) -> None:
        """Save full recoverable state checkpoint.

        Core-only profile: persist current_state.json only (conversation state).
        """
        # Current state checkpoint (progenitor agent state - user session)
        current_thread_id = state.get("thread_id")
        current_checkpoint = {
            "thread_id": current_thread_id,
            "cycle_count": state.get("cycle_count", 0),
            "actions_this_cycle": state.get("actions_this_cycle", 0),
            "shutdown_requested": state.get("shutdown_requested", False),
            "last_checkpoint": utcnow_isoformat(),
            "messages": serialize_messages(state.get("messages", [])),
            "tool_calls": state.get("tool_calls", []),  # Session tool calls
            "attachments": state.get("attachments", []),  # Attachment path links
            "current_input": state.get("current_input"),
            "pending_response": state.get("pending_response"),
            "last_reasoning_trace": state.get("last_reasoning_trace"),
        }

        await self.state_manager.save_current_state(current_checkpoint)
        logger.debug("Checkpointed state at cycle %d", current_checkpoint["cycle_count"])

    async def enqueue_input(
        self,
        message: str,
        timeout: float | None = 30.0,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Add a message to the input queue.

        Args:
            message: The message to enqueue.
            timeout: Max seconds to wait if queue is full. None = wait forever.

        Returns:
            True if message was queued, False if queue was full and timed out.

        Raises:
            asyncio.QueueFull: If timeout is 0 and queue is full (non-blocking mode).
        """
        payload: Any = {"content": message, "metadata": metadata or {}} if metadata else message
        queued_input = QueuedInput(content=payload, enqueue_time=time.monotonic())
        try:
            if timeout == 0:
                # Non-blocking mode
                self._input_queue.put_nowait(queued_input)
            elif timeout is None:
                # Wait forever
                await self._input_queue.put(queued_input)
            else:
                # Wait with timeout
                await asyncio.wait_for(self._input_queue.put(queued_input), timeout=timeout)
            return True
        except asyncio.QueueFull:
            logger.warning("Input queue full, message rejected (non-blocking)")
            raise
        except TimeoutError:
            logger.warning(
                "Input queue full, message rejected after %.1fs timeout. Queue size: %d/%d",
                timeout,
                self._input_queue.qsize(),
                self._input_queue.maxsize,
            )
            return False

    async def get_output(self, timeout: float | None = None) -> str | None:
        """Get output from the agent."""
        try:
            if timeout:
                return cast(
                    str | None, await asyncio.wait_for(self._output_queue.get(), timeout=timeout)
                )
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
        status: str = "running",
    ) -> None:
        """Update health file for Docker health checks."""
        await update_health_file(
            state_dir=self.state_manager.state_dir,
            status=status,
        )
