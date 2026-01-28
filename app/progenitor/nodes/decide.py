"""
Decide Node - Routes user input to appropriate action path.
"""

import logging
from datetime import datetime
from typing import Callable, Coroutine, Any, Optional

import re

from openai import APIConnectionError, RateLimitError

from ..state.models import ProgenitorState, Decision, DecisionAction, WorkItem
from ..state.helpers import is_work_item_actionable
from ..runtime.time_utils import utcnow_isoformat
from .errors import RecoverableError, LLMError, ValidationError

logger = logging.getLogger(__name__)


def _looks_like_agent_build_request(user_input: str) -> bool:
    """Heuristic: detect explicit requests to build/create a new agent."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    has_build_intent = any(
        phrase in text
        for phrase in (
            "build ",
            "create ",
            "scaffold ",
            "generate ",
            "make ",
            "implement ",
        )
    )
    has_agent_intent = any(
        phrase in text
        for phrase in (
            " a2a agent",
            " an a2a agent",
            " agent",
            " assistant agent",
        )
    )

    # Reduce false positives like "build a plan" or "build a docker image".
    has_non_agent_build = any(
        phrase in text
        for phrase in (
            "build a plan",
            "build the plan",
            "build a docker",
            "build an image",
            "build the image",
            "build a container",
            "build the container",
        )
    )

    return has_build_intent and has_agent_intent and not has_non_agent_build


def _infer_target_capabilities(user_input: str) -> list[str]:
    """Infer a likely capability name from the user's request."""
    text = (user_input or "").lower()

    if "hello world" in text or "hello-world" in text:
        return ["hello_world_a2a_agent"]

    # Try to extract an agent name: "build/create <name> agent"
    match = re.search(r"\b(?:build|create|scaffold|generate|make|implement)\s+(?:an?\s+)?([a-z0-9 _\-]{1,64})\s+agent\b", text)
    if not match:
        return []

    raw_name = match.group(1).strip()
    # Convert to snake_case-ish.
    slug = re.sub(r"[^a-z0-9]+", "_", raw_name).strip("_")
    if not slug:
        return []

    return [f"{slug}_a2a_agent"]


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
                if is_work_item_actionable(item)
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

            # If the LLM returns a non-actionable direct answer (no direct_response)
            # but the user is explicitly asking to build an agent, force build routing.
            action_str = decision_data.get("action", "answer_directly")
            direct_response = decision_data.get("direct_response")
            if (
                str(action_str).lower().strip() in ("answer", "answer_directly")
                and not direct_response
                and _looks_like_agent_build_request(user_input)
            ):
                decision_data = {
                    "action": "build_agent",
                    "reasoning": "User explicitly requested creation of a new agent.",
                    "requires_planning": True,
                    "target_capabilities": _infer_target_capabilities(user_input),
                    "direct_response": None,
                }

            # Parse decision
            action_str = decision_data.get("action", "answer_directly")
            try:
                action = DecisionAction(action_str)
            except ValueError:
                logger.warning("Unknown action '%s', falling back to answer_directly", action_str)
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

        except (RateLimitError, APIConnectionError) as e:
            # Recoverable errors - log and raise for retry handling
            logger.warning("Recoverable LLM error in decide node: %s", e)
            raise LLMError(
                message=f"LLM API error: {e}",
                node_name="decide",
                context={"user_input": user_input[:100] if user_input else None},
                retry_after_seconds=5.0,
            ) from e

        except Exception as e:
            # Unexpected errors - log at error level, but still try to recover
            logger.error("Unexpected error in decide node: %s", e, exc_info=True)

            # Fallback to direct answer
            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning=f"Fallback due to error: {type(e).__name__}: {e}",
                requires_planning=False,
            )

            # Log the fallback
            if state_manager:
                await state_manager.log_decision({
                    "event": "routing_decision_fallback",
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "timestamp": utcnow_isoformat(),
                })

            return {"current_decision": decision}

    return decide_node


def _extract_history(messages: list, limit: int = 10) -> list[dict[str, str]]:
    """Extract conversation history as list of dicts."""
    history = []
    for msg in messages[-limit:]:
        if isinstance(msg, dict):
            history.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        elif hasattr(msg, "role"):
            history.append({"role": msg.role, "content": msg.content})
    return history
