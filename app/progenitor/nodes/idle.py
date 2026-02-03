"""
Idle Node - Handles idle state when no work is pending.

Per spec: when no backlog is available and nothing is due soon,
prompt: "Anything to do?"

Note: Backoff sleep is handled by the background worker loop, not here.
"""

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
    # Default prompt interval: prompt every 10 cycles
    prompt_interval = 10

    async def idle_node(state: ProgenitorState) -> ProgenitorState:
        """Handle idle state - prompt user periodically.

        This node:
        1. Checks if we should prompt the user
        2. Resets cycle counters for next iteration

        Note: Backoff sleep is handled by the background worker loop,
        not here, to avoid redundant delays after user responses.
        """
        # Check if we should prompt the user
        cycle_count = state.get("cycle_count", 0)

        response_text = None
        if cycle_count % prompt_interval == 0:
            response_text = "Anything to do?"
            logger.debug(f"Idle at cycle {cycle_count}, prompting user")
        else:
            logger.debug(f"Idle at cycle {cycle_count}")

        return {
            "pending_response": response_text,
            "actions_this_cycle": 0,  # Reset for next cycle
        }

    return idle_node
