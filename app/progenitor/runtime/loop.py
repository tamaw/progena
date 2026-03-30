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
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, NamedTuple, Optional, cast

from ..state.models import redact_secrets
from .scheduler import Scheduler
from .time_utils import utcnow, utcnow_isoformat

if TYPE_CHECKING:
    from ..config import ProgenitorConfig

logger = logging.getLogger(__name__)

# Health file location (relative to state directory)
HEALTH_FILE = "health.json"
SCHEDULED_INPUT_PREFIX = "[Scheduled Task]\n"


class QueuedInput(NamedTuple):
    """Input message bundled with the time it was enqueued.

    Wrapping inputs in this NamedTuple lets the loop measure how long a
    message waited in the queue before processing began – useful context
    for the LLM when a user sends several messages in quick succession.
    """
    content: str
    enqueue_time: float  # time.monotonic() at enqueue

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
        self._input_queue: asyncio.Queue[QueuedInput] = asyncio.Queue(
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
        self._last_reasoning_trace: str | None = None

        # Lock to prevent concurrent graph cycles
        self._graph_lock = asyncio.Lock()

        # Timing metadata captured at dequeue time; consumed once per input cycle.
        # _current_input_wait_seconds: how long the message sat in the input queue.
        # _current_input_pending_count: how many further messages remain after dequeue.
        # Both are written into ProgenitorState before the graph cycle runs so that
        # the intake node (and the LLM) can see them.
        self._current_input_wait_seconds: float = 0.0
        self._current_input_pending_count: int = 0

        # Shared state for the loop (protected by _graph_lock)
        self._state: dict[str, Any] = {}

    async def run_forever(self) -> None:
        """Run the perpetual loop.

        Core-only profile: only processes explicit user input messages.
        """
        global _current_loop
        _current_loop = self
        self.running = True

        run_id = str(uuid.uuid4())

        # Initialize shared state
        self._state = {
            "run_id": run_id,
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": False,
            "messages": [],
            "agents": [],
            "current_input_role": None,
            "current_input_metadata": None,
            "idle_cycles": 0,
            "current_backoff_seconds": self.config.runtime.idle_backoff_seconds,
        }

        # Load persisted state (recover from previous run)
        current_state = await self.state_manager.get_current_state()
        self._state.update(current_state)

        logger.info(
            "Starting perpetual loop (boot_run_id: %s, conversation_run_id: %s)",
            run_id,
            self._state.get("run_id"),
        )

        # Update health immediately on startup
        await self._update_health_file(self._state)

        # Push startup event
        await self._push_event("loop_started", f"Perpetual loop started (run_id: {run_id})")

        # Core-only input handling plus scheduler dispatch loop.
        input_task = asyncio.create_task(self._input_handler_loop(run_id))
        scheduler_task: asyncio.Task | None = None
        if self._is_scheduler_enabled():
            scheduler_task = asyncio.create_task(self._scheduler_loop())
            logger.info("Scheduler loop started")

        try:
            if scheduler_task:
                done, pending = await asyncio.wait(
                    [input_task, scheduler_task],
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
                await input_task
        except asyncio.CancelledError:
            logger.info("Loop cancelled by signal")
            input_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await input_task
            if scheduler_task:
                scheduler_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await scheduler_task

        # Final checkpoint
        await self._checkpoint_state(self._state)
        await self._update_health_file(self._state, status="stopped")
        await self._push_event("loop_stopped", "Perpetual loop stopped")
        logger.info("Loop terminated")

    async def _input_handler_loop(self, run_id: str) -> None:
        """Handle user input messages.

        This task waits for user input and processes it through the graph.
        Core-only profile: no background worker.
        """
        runtime = self.config.runtime
        last_health = time.monotonic()

        while self.running:
            try:
                # Wait for user input (with short timeout to check running flag)
                user_input = await self._get_input(timeout=1.0)

                if not user_input:
                    now = time.monotonic()
                    if now - last_health >= float(self.config.runtime.checkpoint_interval_seconds):
                        await self._update_health_file(self._state)
                        last_health = now
                    continue

                logger.info("Got user input: %s", user_input[:50] if len(user_input) > 50 else user_input)
                is_scheduled_input = user_input.startswith(SCHEDULED_INPUT_PREFIX)
                scheduled_message = (
                    user_input[len(SCHEDULED_INPUT_PREFIX):].strip()
                    if is_scheduled_input
                    else ""
                )

                # Acquire lock to run graph cycle
                async with self._graph_lock:
                    # Reset backoff on input
                    self._state["current_backoff_seconds"] = runtime.idle_backoff_seconds
                    self._state["idle_cycles"] = 0

                    self._state["current_input"] = user_input
                    self._state["cycle_count"] = self._state.get("cycle_count", 0) + 1
                    self._state["actions_this_cycle"] = 0

                    # Expose queue timing to the graph so the intake node and LLM
                    # can see how long this message waited and whether more are queued.
                    self._state["input_wait_seconds"] = self._current_input_wait_seconds
                    self._state["pending_messages_in_queue"] = self._current_input_pending_count

                    # Enable streaming mode
                    self._streaming_enabled = True
                    self._response_was_streamed = False
                    self._stream_final_sent = False
                    self._streamed_response_content = ""

                    try:
                        # Run graph cycle
                        await self._run_graph_cycle(run_id, source="user_input")
                    except Exception as e:
                        # Prevent clients from hanging on failures: emit a final chunk + mark cycle complete.
                        safe_error = redact_secrets(str(e))
                        logger.error("Graph cycle failed for user input: %s", safe_error, exc_info=True)
                        await self._push_event("error", safe_error)

                        if self._streaming_enabled and not self._stream_final_sent:
                            if self._response_was_streamed:
                                await self.stream_final()
                            else:
                                await self._stream_response(f"Error: {safe_error}")
                            self._stream_final_sent = True

                        self._last_cycle_response = f"Error: {safe_error}"
                        self._state["last_cycle_response"] = self._last_cycle_response
                        await self._checkpoint_state(self._state)
                        await self._update_health_file(self._state, status="error", error=safe_error)
                    finally:
                        # Background scheduled work is processed through the same input
                        # path as user messages, so emit an explicit completion event here.
                        if is_scheduled_input:
                            response_text = str(self._state.get("last_cycle_response") or "").strip()
                            result_status = "completed" if response_text else "completed_no_response"
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

    def _is_scheduler_enabled(self) -> bool:
        scheduler_cfg = getattr(self.config, "scheduler", None)
        if scheduler_cfg is not None and hasattr(scheduler_cfg, "enabled"):
            return bool(scheduler_cfg.enabled)
        return False

    def _scheduler_poll_seconds(self) -> float:
        scheduler_cfg = getattr(self.config, "scheduler", None)
        if scheduler_cfg is not None and hasattr(scheduler_cfg, "poll_seconds"):
            try:
                return max(1.0, float(scheduler_cfg.poll_seconds))
            except (TypeError, ValueError):
                return 30.0
        return 30.0

    async def _check_and_enqueue_scheduled_work(self, scheduler: Scheduler) -> int:
        schedule_entries = await self.state_manager.get_schedule()
        schedule_dicts: list[dict[str, Any]] = [
            entry.model_dump() if hasattr(entry, "model_dump") else entry
            for entry in schedule_entries
        ]
        due_items = scheduler.get_due_items(schedule_dicts, current_time=utcnow())
        if not due_items:
            return 0

        enqueued_count = 0
        now = utcnow()
        for entry in due_items:
            schedule_id = str(entry.get("id") or "")
            if not schedule_id:
                continue
            message = str(entry.get("message") or "").strip()
            if not message:
                continue
            user_request = (entry.get("user_request") or "").strip()

            if user_request:
                full_message = f"<original_message>{user_request}</original_message>\n\n{message}"
            else:
                full_message = message

            queued = await self.enqueue_input(
                f"{SCHEDULED_INPUT_PREFIX}{full_message}",
                timeout=0.5,
            )
            if not queued:
                break

            cron_expression = str(entry.get("cron_expression") or "")
            is_one_shot = scheduler.is_one_shot_expression(cron_expression)
            if is_one_shot:
                await self.state_manager.remove_schedule_entry(schedule_id)
            else:
                await self.state_manager.update_schedule_entry(
                    schedule_id,
                    {"last_run": now.isoformat()},
                )

            await self._push_event(
                "scheduled_task_triggered",
                f"Scheduled task triggered: {entry.get('message') or schedule_id}",
                {"schedule_id": schedule_id, "one_shot": is_one_shot},
            )
            enqueued_count += 1

        return enqueued_count

    async def _scheduler_loop(self) -> None:
        scheduler = Scheduler(timezone=getattr(self.config.runtime, "timezone", "UTC"))
        poll_seconds = self._scheduler_poll_seconds()
        while self.running:
            try:
                await self._check_and_enqueue_scheduled_work(scheduler)
                await asyncio.sleep(poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Scheduler loop error: %s", e, exc_info=True)
                await asyncio.sleep(poll_seconds)

    async def _run_graph_cycle(self, _run_id: str, source: str = "unknown") -> None:
        """Run a single graph cycle.

        Args:
            run_id: The run ID for this session.
            source: Source of the cycle (e.g., 'user_input', 'background_backlog').
        """
        cycle_num = self._state["cycle_count"]
        current_input = self._state.get("current_input") if source == "user_input" else None

        # DeepAgent uses the LangGraph checkpointer keyed by thread_id.
        # Keep thread_id aligned to persisted conversation run_id so context survives restarts.
        # The new_conversation tool rotates run_id, intentionally starting a fresh thread.
        thread_id = str(self._state.get("run_id") or "progenitor_main")
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
            async for event in self.graph.astream(
                graph_input,
                config={"configurable": {"thread_id": thread_id}},
            ):
                # Process graph events
                for node_name, node_output in event.items():
                    output_info = list(node_output.keys()) if isinstance(node_output, dict) else type(node_output)
                    logger.info("GRAPH EVENT: node=%s, output_keys=%s", node_name, output_info)
                    if isinstance(node_output, dict):
                        if "messages" in node_output:
                            node_output = {
                                **node_output,
                                "messages": self._coerce_messages_list(node_output.get("messages")),
                            }
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
                        # _stream_response sends an is_final chunk, so mark stream closed for this cycle.
                        self._stream_final_sent = True
                    self._state["pending_response"] = None
                elif self._response_was_streamed and not self._stream_final_sent:
                    # Response was already streamed by a node, just send final marker ONCE
                    if self._streaming_enabled:
                        await self.stream_final()
                    self._stream_final_sent = True
                    self._state["pending_response"] = None

            # Ensure streaming clients never hang waiting for an is_final marker.
            # Extract the last AI message from messages if nothing was streamed yet.
            last_ai_msg = self._get_last_ai_message(self._state.get("messages"))
            if self._streaming_enabled and not self._stream_final_sent:
                response_text = self._extract_message_text(last_ai_msg)
                if response_text:
                    logger.info("Extracting AI response from messages (%d chars)", len(response_text))
                    await self._stream_response(response_text)
                    self._last_cycle_response = response_text
                    self._state["last_cycle_response"] = response_text
                    self._stream_final_sent = True
                if not self._stream_final_sent:
                    await self.stream_final()
                    self._stream_final_sent = True

            reasoning_trace = self._extract_reasoning_trace(last_ai_msg) if last_ai_msg is not None else None
            self._last_reasoning_trace = reasoning_trace
            self._state["last_reasoning_trace"] = reasoning_trace
            if reasoning_trace:
                logger.info("Extracted reasoning trace (%d chars)", len(reasoning_trace))
            elif last_ai_msg is not None and self.config.llm.reasoning_enabled:
                logger.info(
                    "No reasoning trace found in assistant message schema: %s",
                    json.dumps(self._summarize_message_schema(last_ai_msg), default=str),
                )

            # If the run_id changed during this cycle (e.g. the LLM invoked the
            # new_conversation system tool), treat it as a hard context reset.
            if str(self._state.get("run_id") or "") != thread_id:
                self._state["messages"] = []
                self._state["attachments"] = []
                self._state["tool_calls"] = []

            # Checkpoint state after cycle
            await self._checkpoint_state(self._state)
            await self._update_health_file(self._state)

        except Exception as e:
            logger.error("Graph cycle %d failed: %s", cycle_num, e, exc_info=True)
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
        Unwraps the QueuedInput envelope and records queue-timing metadata so
        that _input_handler_loop can expose it to the graph state.
        """
        # Cap timeout to ensure health gets updated frequently
        effective_timeout = min(timeout, 30.0)
        try:
            queued = await asyncio.wait_for(
                self._input_queue.get(),
                timeout=effective_timeout
            )
            # Record how long this message sat in the queue and how many
            # more messages are still waiting.  Both are surfaced to nodes via
            # input_wait_seconds / pending_messages_in_queue in ProgenitorState.
            self._current_input_wait_seconds = time.monotonic() - queued.enqueue_time
            self._current_input_pending_count = self._input_queue.qsize()
            return queued.content
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

        Core-only profile: persist current_state.json only (conversation state).
        """
        # Current state checkpoint (progenitor agent state - user session)
        current_checkpoint = {
            "run_id": state.get("run_id"),
            "cycle_count": state.get("cycle_count", 0),
            "actions_this_cycle": state.get("actions_this_cycle", 0),
            "shutdown_requested": state.get("shutdown_requested", False),
            "last_checkpoint": utcnow_isoformat(),
            "messages": self._serialize_messages(state.get("messages", [])),
            "tool_calls": state.get("tool_calls", []),  # Session tool calls
            "attachments": state.get("attachments", []),  # Attachment path links
            "current_input": state.get("current_input"),
            "pending_response": state.get("pending_response"),
            "last_reasoning_trace": state.get("last_reasoning_trace"),
            "current_decision": self._serialize_decision(state.get("current_decision")),
        }

        await self.state_manager.save_current_state(current_checkpoint)
        logger.debug("Checkpointed state at cycle %d", current_checkpoint["cycle_count"])

    def _coerce_messages_list(self, messages: Any) -> list:
        """Coerce LangGraph/DeepAgent message updates into a plain list.

        Some graph implementations return sentinel update objects (e.g. Overwrite)
        that wrap the real list; our checkpoint expects an iterable list.
        """
        if messages is None:
            return []
        if isinstance(messages, list):
            return messages
        # Common wrapper pattern: obj.value holds the replacement value.
        value = getattr(messages, "value", None)
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        # Best-effort fallback.
        if isinstance(messages, dict):
            return [messages]
        try:
            return list(messages)
        except TypeError:
            return []

    def _serialize_messages(self, messages: Any) -> list:
        """Serialize message objects for checkpoint."""
        serialized = []
        for msg in self._coerce_messages_list(messages):
            if hasattr(msg, "model_dump"):
                serialized.append(msg.model_dump())
            elif isinstance(msg, dict):
                serialized.append(msg)
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

    def _get_last_ai_message(self, messages: Any) -> Any | None:
        """Get the last assistant/AI message from a messages collection."""
        for msg in reversed(self._coerce_messages_list(messages)):
            msg_type = str(getattr(msg, "type", "")).lower()
            msg_role = str(getattr(msg, "role", "")).lower()
            if msg_type == "ai" or msg_role == "assistant":
                return msg
            if isinstance(msg, dict):
                role = str(msg.get("role", "")).lower()
                mtype = str(msg.get("type", "")).lower()
                if role == "assistant" or mtype == "ai":
                    return msg
        return None

    def _extract_reasoning_trace(self, msg: Any) -> str | None:
        """Extract provider reasoning/thinking metadata from a message."""
        if msg is None:
            return None

        def _coerce(value: Any) -> str | None:
            if value is None:
                return None
            if isinstance(value, str):
                stripped = value.strip()
                return stripped or None
            try:
                text = json.dumps(value, default=str).strip()
            except Exception:
                text = str(value).strip()
            return text or None

        containers: list[dict[str, Any]] = []
        if isinstance(msg, dict):
            containers.append(msg)
        else:
            for attr in ("additional_kwargs", "response_metadata"):
                value = getattr(msg, attr, None)
                if isinstance(value, dict):
                    containers.append(value)

        for container in containers:
            for key in ("reasoning", "thinking"):
                if key in container and (text := _coerce(container.get(key))):
                    return text
            # Some providers surface reasoning summary fields directly.
            if "summary" in container or "effort" in container:
                summary_obj = {
                    "effort": container.get("effort"),
                    "summary": container.get("summary"),
                }
                if text := _coerce(summary_obj):
                    return text

        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type", "")).lower()
                if part_type in {"reasoning", "reasoning_text", "reasoning_summary", "thinking"}:
                    candidate = part.get("text") or part.get("summary") or part.get("content")
                    if text := _coerce(candidate):
                        parts.append(text)
            if parts:
                return "\n".join(parts)

        return None

    def _extract_message_text(self, msg: Any) -> str | None:
        """Extract assistant-facing text from message content payloads."""
        if msg is None:
            return None

        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if isinstance(content, str):
            text = content.strip()
            return text or None

        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    text = part.strip()
                    if text:
                        parts.append(text)
                    continue
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type", "")).lower()
                if part_type in {"text", "output_text", "message"}:
                    candidate = part.get("text") or part.get("content")
                    if isinstance(candidate, str) and candidate.strip():
                        parts.append(candidate.strip())
            if parts:
                return "\n".join(parts)

        return None

    def _summarize_message_schema(self, msg: Any) -> dict[str, Any]:
        """Return a compact schema summary for assistant messages."""
        if isinstance(msg, dict):
            additional = msg.get("additional_kwargs")
            metadata = msg.get("response_metadata")
            content = msg.get("content")
            return {
                "type": msg.get("type"),
                "role": msg.get("role"),
                "keys": sorted(msg.keys()),
                "additional_kwargs_keys": sorted(additional.keys()) if isinstance(additional, dict) else [],
                "response_metadata_keys": sorted(metadata.keys()) if isinstance(metadata, dict) else [],
                "content_type": type(content).__name__,
            }

        additional = getattr(msg, "additional_kwargs", None)
        metadata = getattr(msg, "response_metadata", None)
        content = getattr(msg, "content", None)
        return {
            "type": getattr(msg, "type", None),
            "role": getattr(msg, "role", None),
            "additional_kwargs_keys": sorted(additional.keys()) if isinstance(additional, dict) else [],
            "response_metadata_keys": sorted(metadata.keys()) if isinstance(metadata, dict) else [],
            "content_type": type(content).__name__,
        }

    async def enqueue_input(self, message: str, timeout: float | None = 30.0) -> bool:
        """Add a message to the input queue.

        The message is wrapped in a QueuedInput tuple that captures the current
        monotonic clock value.  When the message is later dequeued by _get_input(),
        the wait time is computed and stored in _current_input_wait_seconds so the
        LangGraph cycle can surface it to nodes via the input_wait_seconds state field.

        Args:
            message: The message to enqueue.
            timeout: Max seconds to wait if queue is full. None = wait forever.

        Returns:
            True if message was queued, False if queue was full and timed out.

        Raises:
            asyncio.QueueFull: If timeout is 0 and queue is full (non-blocking mode).
        """
        queued = QueuedInput(content=message, enqueue_time=time.monotonic())
        try:
            if timeout == 0:
                # Non-blocking mode
                self._input_queue.put_nowait(queued)
            elif timeout is None:
                # Wait forever
                await self._input_queue.put(queued)
            else:
                # Wait with timeout
                await asyncio.wait_for(
                    self._input_queue.put(queued),
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
            "message_count": len(state.get("messages") or []) if isinstance(state.get("messages"), list) else 0,
            "agent_count": len(state.get("agents") or []) if isinstance(state.get("agents"), list) else 0,
            "queues": self.get_queue_stats(),
        }

        if error:
            health_data["error"] = error

        try:
            health_path.write_text(json.dumps(health_data))
        except Exception as e:
            logger.warning("Failed to update health file: %s", e)
