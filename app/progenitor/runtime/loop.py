"""
Progenitor Loop Runner.

Perpetual loop execution with backoff and cycle control.
The loop is intentionally infinite - when there's nothing queued,
it idles and waits for input or scheduled tasks.

Controls:
- idle sleep/backoff when no new input
- max actions per cycle (tool/A2A/Copilot)
- restart-safe checkpoints (write state every cycle)
"""

import asyncio
import logging
from typing import Any, Optional, TYPE_CHECKING
import uuid

from .time_utils import utcnow_isoformat

if TYPE_CHECKING:
    from ..config import ProgenitorConfig

logger = logging.getLogger(__name__)


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
        self._input_queue: asyncio.Queue = asyncio.Queue()
        self._output_queue: asyncio.Queue = asyncio.Queue()

    async def run_forever(self) -> None:
        """Run the perpetual loop.

        The loop processes user input and scheduled tasks continuously.
        When idle, it backs off exponentially to avoid busy-waiting.
        State is checkpointed after each cycle for crash recovery.
        """
        self.running = True

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

        logger.info(f"Starting perpetual loop (run_id: {run_id})")
        print(f"Starting perpetual loop (run_id: {run_id})")

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

                    async for event in self.graph.astream(
                        state,
                        config={"configurable": {"thread_id": thread_id}},
                    ):
                        # Process graph events
                        for node_name, node_output in event.items():
                            if isinstance(node_output, dict):
                                state.update(node_output)

                        # Check for response to send
                        pending = state.get("pending_response")
                        if pending:
                            await self._send_output(str(pending))
                            state["pending_response"] = None

                    # Checkpoint state after each cycle
                    await self._checkpoint_state(state)

                else:
                    # No input - check for scheduled tasks or backlog
                    has_work = await self._check_pending_work(state)

                    if has_work:
                        # Reset backoff when work is available
                        backoff = runtime.idle_backoff_seconds
                        # Run a cycle to process pending work
                        state["current_input"] = None
                        state["cycle_count"] = state.get("cycle_count", 0) + 1

                        async for event in self.graph.astream(
                            state,
                            config={"configurable": {"thread_id": f"cycle_{state['cycle_count']}"}},
                        ):
                            for node_name, node_output in event.items():
                                if isinstance(node_output, dict):
                                    state.update(node_output)

                        await self._checkpoint_state(state)
                    else:
                        # Increase backoff exponentially when truly idle
                        backoff = min(backoff * backoff_multiplier, max_backoff)
                        logger.debug(f"Idle, backoff now {backoff:.1f}s")

            except asyncio.CancelledError:
                logger.info("Loop cancelled by signal")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)
                print(f"Loop error: {e}")
                await asyncio.sleep(backoff)

        # Final checkpoint before shutdown
        await self._checkpoint_state(state)
        logger.info("Loop terminated")
        print("Loop terminated")

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
        """Send output to the user."""
        await self._output_queue.put(message)
        print(f"[Progenitor] {message}")

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
        """Save state checkpoint."""
        checkpoint = {
            "run_id": state.get("run_id"),
            "cycle_count": state.get("cycle_count"),
            "last_checkpoint": utcnow_isoformat(),
        }
        await self.state_manager.save_state(checkpoint)

    async def enqueue_input(self, message: str) -> None:
        """Add a message to the input queue."""
        await self._input_queue.put(message)

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
