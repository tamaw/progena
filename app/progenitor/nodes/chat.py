"""
Chat Node - main user interaction path using LLM tool-calling.

Replaces Decide routing for normal chat turns:
Intake -> Chat -> Respond
"""

import inspect
import logging
import re
from collections.abc import Callable, Coroutine
from contextlib import suppress
from typing import Any, cast

from ..llm.prompts import PromptTemplates
from ..state.models import Decision, DecisionAction, ProgenitorState
from ..tools.registry import normalize_tool_name
from ..utils import env_int

logger = logging.getLogger(__name__)

_prompts = PromptTemplates()

# Trigger keywords that activate extended reasoning mode.
# Matched case-insensitively at the start of the user message.
_REASONING_TRIGGER_RE = re.compile(
    r"^(?:"
    r"think\s+(?:through|hard|deeply)\b"
    r"|reason\s+about\b"
    r"|deep\s+dive\b"
    r"|step[- ]by[- ]step\b"
    r"|think\b"
    r"|reason\b"
    r"|analyze\b"
    r"|consider\b"
    r"|reflect\b"
    r"|ponder\b"
    r"|evaluate\b"
    r"|deliberate\b"
    r")\s*",
    re.IGNORECASE,
)


def _detect_reasoning_trigger(user_input: str) -> tuple[str, bool]:
    """Detect reasoning trigger keywords at the start of user input.

    Returns:
        Tuple of (stripped_input, should_reason). If a trigger is found the
        keyword prefix is removed so the LLM receives a clean prompt.
    """
    m = _REASONING_TRIGGER_RE.match(user_input)
    if m:
        stripped = user_input[m.end():].strip()
        # Don't trigger on bare keyword with no actual query
        if stripped:
            return stripped, True
    return user_input, False


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


def _get_a2a_agent_tool_definitions(tool_registry: Any) -> list[dict[str, Any]]:
    """Expose registered A2A agents as callable tools (conversational delegation)."""
    agents: list[dict[str, Any]] = []
    if tool_registry is not None and hasattr(tool_registry, "get_agents"):
        try:
            agents = tool_registry.get_agents() or []
        except Exception:
            agents = []

    tool_defs: list[dict[str, Any]] = []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        name = agent.get("name")
        if not name or not isinstance(name, str):
            continue
        tool_name = normalize_tool_name(name)
        desc = str(agent.get("description") or "A2A agent")
        caps = agent.get("capabilities") if isinstance(agent.get("capabilities"), list) else []
        caps_str = ", ".join(str(c) for c in caps[:10] if c) if caps else ""
        if caps_str:
            desc = f"{desc} (capabilities: {caps_str})"
        tool_defs.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": f"A2A agent ({name}): {desc}",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "request": {
                                "type": "string",
                                "description": "Natural language request for the agent.",
                            },
                            "context": {
                                "type": "object",
                                "description": "Optional structured context for the agent.",
                            },
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

        # Fast-pass: detect reasoning trigger keywords
        cleaned_input, use_reasoning = _detect_reasoning_trigger(user_input)
        if use_reasoning:
            logger.info("Reasoning trigger detected, enabling extended thinking")

        messages = _coerce_messages(state.get("messages", []), limit=20)

        tool_exec_ctx: dict[str, Any] = {
            "tool_registry": tool_registry,
            "state_manager": state_manager,
            "state": state,
            "state_updates": {},
            "default_user_input": cleaned_input,
        }

        # Registry can be modified by external deploy pipelines; reload best-effort
        # so A2A agents appear as tools without requiring a container restart.
        if tool_registry is not None and hasattr(tool_registry, "load"):
            with suppress(Exception):
                maybe = tool_registry.load()
                if inspect.isawaitable(maybe):
                    await maybe

        tool_defs = system_tools + _get_a2a_agent_tool_definitions(tool_registry)

        reasoning_trace: str | None = None
        if use_reasoning and hasattr(llm_client, "chat_with_reasoning"):
            response_text, reasoning_trace = await llm_client.chat_with_reasoning(
                messages=messages,
                system_prompt=system_prompt,
                temperature=0.7,
                tools=tool_defs,
            )
        else:
            response_text = await llm_client.chat(
                messages=messages,
                system_prompt=system_prompt,
                temperature=0.7,
                tools=tool_defs,
                max_tool_rounds=max_tool_rounds,
                system_tool_context=tool_exec_ctx,
            )

        decision = Decision(
            action=DecisionAction.ANSWER_DIRECTLY,
            reasoning="LLM tool-calling chat (with extended thinking)" if use_reasoning else "LLM tool-calling chat",
            requires_planning=False,
            target_capabilities=[],
            direct_response=response_text,
        )

        state_updates = tool_exec_ctx.get("state_updates") or {}
        if reasoning_trace:
            state_updates["last_reasoning_trace"] = reasoning_trace
        # Ensure tool-provided state resets (e.g., new_conversation) don't wipe out this turn's decision.
        return cast(ProgenitorState, {**state_updates, "current_decision": decision})

    return chat_node
