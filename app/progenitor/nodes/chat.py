"""
Chat Node - main user interaction path using LLM tool-calling.

Replaces Decide routing for normal chat turns:
Intake -> Chat -> Respond
"""

import logging
from collections.abc import Callable, Coroutine
from typing import Any, cast

from ..utils import env_int
from ..llm.prompts import PromptTemplates
from ..state.models import Decision, DecisionAction, ProgenitorState

logger = logging.getLogger(__name__)

_prompts = PromptTemplates()


def _coerce_messages(raw_messages: list[Any], limit: int = 20) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for msg in raw_messages[-limit:]:
        if isinstance(msg, dict):
            role = str(msg.get("role", "user"))
            content = str(msg.get("content", ""))
        elif hasattr(msg, "role") and hasattr(msg, "content"):
            role = str(msg.role)
            content = str(msg.content)
        else:
            continue
        if content:
            messages.append({"role": role, "content": content})
    return messages


def _get_system_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI tool definitions for all registered system tools."""
    from .node_helpers.registry import get_all_system_tools

    tool_defs: list[dict[str, Any]] = []
    for tool in get_all_system_tools():
        tool_defs.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "request": {
                                "type": "string",
                                "description": "Natural language request for the tool.",
                            }
                        },
                        "required": ["request"],
                    },
                },
            }
        )
    return tool_defs


def create_node(
    llm_client,
    tool_registry,
    state_manager=None,
    config=None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the chat node function."""
    max_tool_rounds = env_int("PROGENITOR_MAX_TOOL_ROUNDS", 50)
    # Safety cap: never allow an unbounded loop.
    if max_tool_rounds < 1:
        max_tool_rounds = 50

    system_prompt = _prompts.get_genesis_prompt().strip()
    system_tools = _get_system_tool_definitions()

    async def chat_node(state: ProgenitorState) -> ProgenitorState:
        user_input = state.get("current_input")
        if not user_input:
            return {}

        messages = _coerce_messages(state.get("messages", []), limit=20)

        tool_exec_ctx: dict[str, Any] = {
            "tool_registry": tool_registry,
            "state_manager": state_manager,
            "state": state,
            "state_updates": {},
            "default_user_input": user_input,
        }

        response_text = await llm_client.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.7,
            tools=system_tools,
            max_tool_rounds=max_tool_rounds,
            system_tool_context=tool_exec_ctx,
        )

        decision = Decision(
            action=DecisionAction.ANSWER_DIRECTLY,
            reasoning="LLM tool-calling chat",
            requires_planning=False,
            target_capabilities=[],
            direct_response=response_text,
        )

        state_updates = tool_exec_ctx.get("state_updates") or {}
        return cast(ProgenitorState, {"current_decision": decision, **state_updates})

    return chat_node

