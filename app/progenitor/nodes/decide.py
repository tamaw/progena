"""
Decide Node - Routes user input to appropriate action path.
"""

from datetime import datetime
from typing import Callable, Coroutine, Any, Optional

from ..state.models import ProgenitorState, Decision, DecisionAction, WorkItem
from ..runtime.time_utils import utcnow_isoformat


def create_node(
    llm_client,
    tool_registry,
    state_manager=None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the decide node function.

    Args:
        llm_client: The LLM client for making decisions
        tool_registry: Registry of available tools and agents
        state_manager: Optional state manager for logging decisions
    """

    async def decide_node(state: ProgenitorState) -> ProgenitorState:
        """Make a routing decision for the current input."""
        user_input = state.get("current_input")

        if not user_input:
            # No input - check if we have backlog to process
            backlog = state.get("backlog", [])
            pending = [
                item for item in backlog
                if _is_actionable(item)
            ]

            if pending:
                # Continue processing backlog
                return {"current_decision": None}

            return {"current_decision": None}

        # Get available capabilities
        capabilities = tool_registry.get_all_capabilities()

        # Get conversation history for context
        messages = state.get("messages", [])
        history = _extract_history(messages, limit=10)

        try:
            # Ask LLM for decision
            decision_data = await llm_client.decide(
                user_input=user_input,
                conversation_history=history,
                available_capabilities=capabilities,
            )

            # Parse decision
            action_str = decision_data.get("action", "answer_directly")
            try:
                action = DecisionAction(action_str)
            except ValueError:
                action = DecisionAction.ANSWER_DIRECTLY

            decision = Decision(
                action=action,
                reasoning=decision_data.get("reasoning", ""),
                requires_planning=decision_data.get("requires_planning", False),
                target_capabilities=decision_data.get("target_capabilities", []),
                direct_response=decision_data.get("direct_response"),
            )

            # Log decision
            if state_manager:
                await state_manager.log_decision({
                    "event": "routing_decision",
                    "action": action.value,
                    "reasoning": decision_data.get("reasoning", ""),
                    "requires_planning": decision_data.get("requires_planning", False),
                    "target_capabilities": decision_data.get("target_capabilities", []),
                    "timestamp": utcnow_isoformat(),
                })

            return {"current_decision": decision}

        except Exception as e:
            # Fallback to direct answer on error
            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning=f"Fallback due to error: {e}",
                requires_planning=False,
            )
            return {"current_decision": decision}

    return decide_node


def _is_actionable(item) -> bool:
    """Check if a work item is actionable (pending or in_progress)."""
    if isinstance(item, WorkItem):
        return item.is_actionable
    if isinstance(item, dict):
        return item.get("status") in ("pending", "in_progress")
    if hasattr(item, "status"):
        status = item.status.value if hasattr(item.status, "value") else item.status
        return status in ("pending", "in_progress")
    return False


def _extract_history(messages: list, limit: int = 10) -> list[dict[str, str]]:
    """Extract conversation history as list of dicts."""
    history = []
    for msg in messages[-limit:]:
        if isinstance(msg, dict):
            history.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        elif hasattr(msg, "role"):
            history.append({"role": msg.role, "content": msg.content})
    return history
