"""
Invoke Agent — Dispatch a task to any registered A2A agent asynchronously.

Flow when called as a DeepAgent tool:
 1. LLM calls the tool with request + optional tool_args["agent"]
 2. If agent is not specified → small LLM routing call selects best agent
     from agents.json based on capabilities
  3. A2A message/send is submitted in non-blocking mode and persisted as a tracked task
  4. task_id is returned immediately; Progenitor can query and notify on completion

Uses A2AClient from app.progenitor.tools.mcp — the project existing A2A transport.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import logging
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from ..runtime.a2a_tasks import build_task_response, dispatch_tracked_a2a_task
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_AGENTS_JSON = Path(__file__).resolve().parents[3] / "data" / "config" / "agents.json"


# ---------------------------------------------------------------------------
# Structured-output schema for the agent-selection LLM routing call
# ---------------------------------------------------------------------------


class AgentSelection(BaseModel):
    """LLM-structured response: which agent to call and what message to send."""

    agent_name: str = Field(
        description="Exact key from agents.json (e.g. 'builder', 'builder_agent', 'observe_agent'). "
        "Use 'none' if no agent is suitable.",
    )
    message: str = Field(
        description="The fully-formed message to send to the selected agent — clear, actionable, "
        "and complete enough for the agent to act without further clarification.",
    )
    reasoning: str = Field(
        description="One-sentence explanation of why this agent was selected.",
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class InvokeAgentTool(BaseSystemTool):
    """Dispatch a task to a registered A2A agent in the background.

    Pass tool_args={"agent": "<name>"} to target a specific agent directly.
    If omitted, an LLM routing call selects the best agent from agents.json.
    The A2A call runs as a background asyncio task — the tool returns a task_id
    immediately without blocking.

    Available agents are in data/config/agents.json.
    Use the list_agents tool to see current agent status before invoking.
    """

    @property
    def name(self) -> str:
        return "invoke_agent"

    @property
    def description(self) -> str:
        return (
            "Dispatch a task to a registered A2A agent asynchronously and track it via A2A task_id. "
            "Returns task_id immediately; use get_a2a_task for progress and Progenitor will notify on completion. "
            "Pass tool_args={'agent': 'builder'} to target a specific agent, or omit to auto-route. "
            "Use for: code tasks, PRs, builds, documentation, monitoring, or any delegatable work. "
            "Available agents: builder (small code tasks/PRs), builder_agent (full build loop), "
            "observe_agent (state/log analysis)."
        )

    @property
    def keywords(self) -> list[str]:
        return [
            "invoke agent",
            "delegate",
            "call agent",
            "builder copilot",
            "builder agent",
            "observe agent",
            "build",
            "create pr",
            "code generation",
            "a2a",
            "dispatch",
        ]

    async def execute(self, context: ToolContext) -> ToolResult:
        """
        Dispatch a task to a registered A2A agent asynchronously and track it.

        context.user_input: task description
        context.tool_args (optional):
          {
            "agent":   "builder",  # exact key from agents.json; omit to auto-route
            "message": "...",              # override the message; defaults to user_input
            "timeout": 30,                 # A2A request timeout in seconds (default 30)
          }
        """
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot track async A2A tasks")

        request_text = (context.user_input or "").strip()
        if not request_text:
            return ToolResult.fail("Missing task description. What should the agent do?")

        args: dict[str, Any] = context.tool_args if isinstance(context.tool_args, dict) else {}
        forced_agent: str | None = args.get("agent") or None
        message_override: str = args.get("message", "")
        timeout: float = float(args.get("timeout", 30))
        tool_registry = context.tool_registry

        # ------------------------------------------------------------------
        # 1. Reload registry best-effort (picks up agents.json edits at runtime)
        # ------------------------------------------------------------------
        load = getattr(tool_registry, "load", None) if tool_registry is not None else None
        if callable(load):
            with contextlib.suppress(Exception):
                out = load()
                if inspect.isawaitable(out):
                    await cast(Any, out)

        # ------------------------------------------------------------------
        # 2. Select agent and resolve config via tool_registry (same as call_agent)
        # ------------------------------------------------------------------
        # For LLM routing we still need the full agents list for context
        agents = _load_agents()

        if forced_agent:
            agent_name = forced_agent
            dispatch_message = message_override or request_text
        else:
            if not agents:
                return ToolResult.fail(f"No agents registered — add entries to {_AGENTS_JSON}.")
            selection = await _route_agent(context, agents, request_text)
            if selection.agent_name == "none":
                return ToolResult.fail(
                    f"No suitable agent found for this task. "
                    f"Reason: {selection.reasoning}. "
                    f"Available: {', '.join(agents)}."
                )
            agent_name = selection.agent_name
            dispatch_message = message_override or selection.message
            logger.info("Agent routing: %s — %s", agent_name, selection.reasoning)

        # Resolve config via tool_registry (find_by_name → find_agent_by_capability)
        # identical to call_agent lookup so both tools share the same source of truth
        cfg: dict[str, Any] | None = None
        if hasattr(tool_registry, "find_by_name"):
            with contextlib.suppress(Exception):
                cfg = tool_registry.find_by_name(agent_name)
        if not cfg and hasattr(tool_registry, "find_agent_by_capability"):
            with contextlib.suppress(Exception):
                cfg = tool_registry.find_agent_by_capability(agent_name)
        # Fall back to agents.json entry when registry lookup is unavailable
        if not isinstance(cfg, dict):
            fallback_cfg = agents.get(agent_name)
            if isinstance(fallback_cfg, dict):
                cfg = dict(fallback_cfg)
        if not isinstance(cfg, dict):
            return ToolResult.fail(f"Unknown agent '{agent_name}'.")

        response, tracked_task = await dispatch_tracked_a2a_task(
            state_manager=context.state_manager,
            agent_name=agent_name,
            agent_config=cfg,
            request_text=dispatch_message,
            request_metadata={
                "caller": "progenitor",
                "dispatch_mode": "async",
            },
            source="invoke_agent",
            timeout=timeout,
            blocking=False,
            notify_on_completion=True,
        )
        if not response.get("success"):
            return ToolResult.fail(str(response.get("error") or f"{agent_name} dispatch failed"))
        if tracked_task is None:
            return ToolResult.fail(
                f"{agent_name} did not return a trackable A2A task for async dispatch"
            )

        result = build_task_response(tracked_task)
        result["message"] = (
            f"Task dispatched to '{agent_name}' (task_id: {tracked_task.task_id}). "
            "Use get_a2a_task to check progress; Progenitor will notify you when it finishes."
        )
        result["url"] = cfg.get("url", "")
        return ToolResult.ok(result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_agents() -> dict[str, Any]:
    """Load agents.json. Returns empty dict on error."""
    try:
        if not _AGENTS_JSON.exists():
            logger.warning("agents.json not found at %s", _AGENTS_JSON)
            return {}
        loaded = json.loads(_AGENTS_JSON.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        logger.error("Failed to load agents.json: %s", exc)
        return {}


def _format_agents_for_prompt(agents: dict[str, Any]) -> str:
    """Render agents as a compact context block for the routing LLM prompt."""
    lines: list[str] = []
    for name, cfg in agents.items():
        caps = ", ".join(cfg.get("capabilities", []))
        desc = cfg.get("description", "")
        status = cfg.get("status", "unknown")
        lines.append(f"- **{name}** (status: {status})\n  {desc}\n  capabilities: [{caps}]")
    return "\n".join(lines)


async def _route_agent(
    context: ToolContext,
    agents: dict[str, Any],
    request_text: str,
) -> AgentSelection:
    """Use a small LLM structured-output call to select the best agent."""
    agents_block = _format_agents_for_prompt(agents)

    system_prompt = (
        "You are an agent router. Select the best A2A agent for the user task and craft a "
        "clear, complete message to send to that agent.\n"
        "Rules:\n"
        "- Match the task to the agent capabilities.\n"
        "- If no agent fits, set agent_name to 'none'.\n"
        "- The message field must be the full instruction — self-contained.\n"
        "- Keep reasoning to one sentence."
    )

    user_prompt = (
        f"## Available Agents\n\n{agents_block}\n\n"
        f"## Task\n\n{request_text}\n\n"
        "Select the best agent and craft the message."
    )

    try:
        selection = await context.llm_client.chat_structured(
            messages=[{"role": "user", "content": user_prompt}],
            response_model=AgentSelection,
            schema_name="AgentSelection",
            system_prompt=system_prompt,
            temperature=0.2,
            max_tokens=512,
        )
        return cast(AgentSelection, selection)
    except Exception as exc:
        logger.error("Agent routing LLM call failed: %s", exc)
        return AgentSelection(
            agent_name="none",
            message=request_text,
            reasoning=f"LLM routing failed: {exc}",
        )


__all__ = ["InvokeAgentTool"]
