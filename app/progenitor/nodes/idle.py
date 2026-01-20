"""
Idle Node - Handles idle state when no work is pending.

Per spec: when no backlog is available and nothing is due soon,
idle with backoff and prompt: "Anything to do?"
"""

import asyncio
import logging
from typing import Callable, Coroutine, Any, TYPE_CHECKING

from ..state.models import ProgenitorState

if TYPE_CHECKING:
    from ..config import ProgenitorConfig

logger = logging.getLogger(__name__)


def create_node(config: "ProgenitorConfig", state_manager=None) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the idle node function.

    Args:
        config: ProgenitorConfig with runtime settings.
        state_manager: Optional state manager.
    """
    # Extract config values once at node creation time
    backoff = config.runtime.idle_backoff_seconds
    # Default prompt interval: prompt every 10 cycles
    prompt_interval = 10

    async def idle_node(state: ProgenitorState) -> ProgenitorState:
        """Handle idle state - prompt user and backoff.

        This node:
        1. Checks if we should prompt the user
        2. Applies backoff sleep to avoid busy-waiting
        3. Resets cycle counters for next iteration
        """
        # Check if we should prompt the user
        cycle_count = state.get("cycle_count", 0)

        response_text = None
        if cycle_count % prompt_interval == 0:
            response_text = "Anything to do?"
            logger.debug(f"Idle at cycle {cycle_count}, prompting user")
        else:
            logger.debug(f"Idle at cycle {cycle_count}, backing off for {backoff}s")

        # Backoff sleep to avoid busy-waiting
        await asyncio.sleep(backoff)

        return {
            "pending_response": response_text,
            "actions_this_cycle": 0,  # Reset for next cycle
        }

    return idle_node
