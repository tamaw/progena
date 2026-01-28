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
from typing import Any, Optional, TYPE_CHECKING
import uuid

from .time_utils import utcnow, utcnow_isoformat

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


class ProgenitorLoop:
    """Manages the perpetual execution loop."""

    def __init__(
        self,
        graph,
        state_manager,
        config: "ProgenitorConfig",
    ):
        self.graph = graph
        self.state_manager = state_manager
        self.config = config
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
        self._cycle_telemetry = None

    async def run_forever(self) -> None:
        """Run the perpetual loop.

        The loop processes user input and scheduled tasks continuously.
        When idle, it backs off exponentially to avoid busy-waiting.
        State is checkpointed after each cycle for crash recovery.
        """
        self.running = True

        # Initialize telemetry
        _init_telemetry(self.config)

        # Get runtime config values
        runtime = self.config.runtime
        backoff = runtime.idle_backoff_seconds
        max_backoff = runtime.max_backoff_seconds
        backoff_multiplier = runtime.backoff_multiplier

        run_id = str(uuid.uuid4())

        # Initialize state
        state = {
            "run_id": run_id,
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": False,
            "messages": [],
            "backlog": [],
            "schedule": [],
            "agents": [],
        }

        # Load persisted state (recover from previous run)
        persisted = await self.state_manager.get_state()
        state.update(persisted)
        state["run_id"] = run_id  # New run ID for this session

        logger.info("Starting perpetual loop (run_id: %s)", run_id)

        while self.running:
            try:
                # Check for user input
                user_input = await self._get_input(timeout=backoff)

                if user_input:
                    # Reset backoff on input
                    backoff = runtime.idle_backoff_seconds

                    state["current_input"] = user_input
                    state["cycle_count"] = state.get("cycle_count", 0) + 1
                    state["actions_this_cycle"] = 0

                    # Run graph cycle
                    thread_id = f"cycle_{state['cycle_count']}"

                    # Start cycle telemetry
                    cycle_telem = self._start_cycle_telemetry(state["cycle_count"], run_id)

                    async for event in self.graph.astream(
                        state,
                        config={"configurable": {"thread_id": thread_id}},
                    ):
                        # Process graph events
                        for node_name, node_output in event.items():
                            output_info = list(node_output.keys()) if isinstance(node_output, dict) else type(node_output)
                            logger.info("GRAPH EVENT: node=%s, output_keys=%s", node_name, output_info)
                            if isinstance(node_output, dict):
                                state.update(node_output)

                            # Record node event in telemetry
                            if cycle_telem:
                                cycle_telem.add_event(f"node.{node_name}", {"output_keys": str(output_info)})

                        # Check for response to send
                        pending = state.get("pending_response")
                        if pending:
                            await self._send_output(str(pending))
                            state["pending_response"] = None

                    # End cycle telemetry
                    if cycle_telem:
                        cycle_telem.complete(success=True)

                    # Checkpoint state after each cycle
                    await self._checkpoint_state(state)
                    await self._update_health_file(state)

                else:
                    # No input - check for scheduled tasks or backlog
                    has_work = await self._check_pending_work(state)

                    if has_work:
                        # Reset backoff when work is available
                        backoff = runtime.idle_backoff_seconds
                        # Run a cycle to process pending work
                        state["current_input"] = None
                        state["cycle_count"] = state.get("cycle_count", 0) + 1

                        # Start cycle telemetry
                        cycle_telem = self._start_cycle_telemetry(state["cycle_count"], run_id)

                        async for event in self.graph.astream(
                            state,
                            config={"configurable": {"thread_id": f"cycle_{state['cycle_count']}"}},
                        ):
                            for node_name, node_output in event.items():
                                if isinstance(node_output, dict):
                                    state.update(node_output)
                                if cycle_telem:
                                    cycle_telem.add_event(f"node.{node_name}")

                        if cycle_telem:
                            cycle_telem.complete(success=True)

                        await self._checkpoint_state(state)
                        await self._update_health_file(state)
                    else:
                        # Increase backoff exponentially when truly idle
                        # Still update health to show we're alive
                        await self._update_health_file(state)
                        backoff = min(backoff * backoff_multiplier, max_backoff)
                        logger.debug("Idle, backoff now %.1f s", backoff)

            except asyncio.CancelledError:
                logger.info("Loop cancelled by signal")
                break
            except Exception as e:
                logger.error("Loop error: %s", e, exc_info=True)
                if self._cycle_telemetry:
                    self._cycle_telemetry.complete(success=False, error=str(e))
                await asyncio.sleep(backoff)

        # Final checkpoint and telemetry shutdown
        await self._checkpoint_state(state)
        self._shutdown_telemetry()
        logger.info("Loop terminated")

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
        """Get user input with timeout."""
        try:
            return await asyncio.wait_for(
                self._input_queue.get(),
                timeout=timeout
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
        """Check if there's pending work in backlog or schedule."""
        backlog = state.get("backlog", [])
        pending = [
            item for item in backlog
            if isinstance(item, dict) and item.get("status") in ("pending", "in_progress")
        ]

        if pending:
            return True

        # Check schedule
        # (Simplified - full implementation would check due times)
        return False

    async def _checkpoint_state(self, state: dict[str, Any]) -> None:
        """Save full recoverable state checkpoint.

        Saves all state needed to recover from a crash, not just metadata.
        Per SPEC: restart-safe checkpoints (write state every cycle).
        """
        # Core recoverable state
        checkpoint = {
            # Metadata
            "run_id": state.get("run_id"),
            "cycle_count": state.get("cycle_count", 0),
            "last_checkpoint": utcnow_isoformat(),

            # Essential state for recovery
            "actions_this_cycle": state.get("actions_this_cycle", 0),
            "shutdown_requested": state.get("shutdown_requested", False),

            # Conversation state
            "messages": state.get("messages", []),

            # Work items
            "backlog": self._serialize_backlog(state.get("backlog", [])),

            # Schedule
            "schedule": state.get("schedule", []),

            # Current iteration state (for inner loop recovery)
            "current_iteration": state.get("current_iteration", 0),
            "verification_score": state.get("verification_score"),
            "best_artefact_score": state.get("best_artefact_score"),
        }

        # Only include ideal_state if it exists and is serializable
        ideal_state = state.get("ideal_state")
        if ideal_state:
            if hasattr(ideal_state, "model_dump"):
                checkpoint["ideal_state"] = ideal_state.model_dump()
            elif isinstance(ideal_state, dict):
                checkpoint["ideal_state"] = ideal_state

        await self.state_manager.save_state(checkpoint)
        logger.debug("Checkpointed state at cycle %d", checkpoint["cycle_count"])

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
        }

    async def _update_health_file(self, state: dict[str, Any]) -> None:
        """Update health file for Docker health checks.

        Creates/updates a JSON file with last activity timestamp.
        Docker can check this file's age to determine health.
        """
        health_path = self.state_manager.state_dir / HEALTH_FILE
        health_data = {
            "status": "running",
            "last_active": utcnow_isoformat(),
            "run_id": state.get("run_id"),
            "cycle_count": state.get("cycle_count", 0),
            "pid": __import__("os").getpid(),
        }

        try:
            health_path.write_text(json.dumps(health_data))
        except Exception as e:
            logger.warning("Failed to update health file: %s", e)
