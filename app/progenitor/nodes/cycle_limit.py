"""
CycleLimit Node - Caps actions per cycle to prevent runaway loops.
"""

from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from ..state.models import ProgenitorState

if TYPE_CHECKING:
    from ..config import ProgenitorConfig


def create_node(config: "ProgenitorConfig") -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the cycle limit node function.

    Args:
        config: ProgenitorConfig with runtime settings.
    """
    max_actions = config.runtime.max_actions_per_cycle

    async def cycle_limit_node(state: ProgenitorState) -> ProgenitorState:
        """Check and enforce cycle limits."""
        actions_this_cycle = state.get("actions_this_cycle", 0)

        if actions_this_cycle >= max_actions:
            # Force a pause - clear current work item to break the loop
            return {
                "current_work_item": None,
                "actions_this_cycle": 0,
            }

        return {}

    return cycle_limit_node
