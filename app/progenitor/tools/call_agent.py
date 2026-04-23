"""Canonical outbound A2A tool for deployed agents."""

from __future__ import annotations

import contextlib
import inspect
import logging
from collections.abc import Awaitable
from typing import Any, cast

from pydantic import BaseModel, Field

from ..runtime.a2a_tasks import build_task_response, dispatch_tracked_a2a_task
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class AgentSelection(BaseModel):
    """LLM-structured response for selecting a deployed agent."""

    agent_name: str = Field(
        description="Exact registered agent name, or 'none' if no deployed agent is suitable."
    )
    message: str = Field(
        description="The complete, self-contained request to send to the selected agent."
    )
    reasoning: str = Field(description="One-sentence explanation of the routing choice.")


class CallAgentArgs(BaseModel):
    agent: str = Field(
        default="",
        description="Registered deployed agent name or capability. Leave empty to auto-route.",
    )
    request: str = Field(description="Complete request to send to the deployed agent.")
    timeout_seconds: float = Field(default=60.0, description="Request timeout in seconds.")
    blocking: bool = Field(
        default=False,
        description="Wait for the agent result instead of returning a tracked task immediately.",
    )
    context: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured context to pass through with the request.",
    )


class CallAgentTool(BaseSystemTool):
    @property
    def name(self) -> str:
        return "call_agent"

    @property
    def description(self) -> str:
        return (
            "Call a deployed A2A agent by name or capability, or omit agent to auto-route. "
            "Use for external/hosted agents only; prefer DeepAgent subagents for internal work. "
            "Defaults to async tracked dispatch; set blocking=true to wait for completion."
        )

    @property
    def keywords(self) -> list[str]:
        return [
            "call agent",
            "invoke agent",
            "delegate to deployed agent",
            "run external agent",
            "ask hosted agent",
            "a2a",
        ]

    @property
    def deepagent_args_schema(self) -> type[BaseModel] | None:
        return CallAgentArgs

    def deepagent_prepare_args(self, **kwargs: Any) -> tuple[str, dict[str, Any] | None]:
        request = str(kwargs.get("request") or "")
        tool_args: dict[str, Any] = {
            "agent": str(kwargs.get("agent") or ""),
            "request": request,
            "timeout_seconds": kwargs.get("timeout_seconds", 60.0),
            "blocking": kwargs.get("blocking", False),
        }
        if kwargs.get("context") is not None:
            tool_args["context"] = kwargs.get("context")
        return request, tool_args

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot track A2A tasks")

        tool_registry = context.tool_registry
        if not tool_registry:
            return ToolResult.fail("tool_registry unavailable")

        # Refresh best-effort so agents.json edits show up immediately.
        load = getattr(tool_registry, "load", None)
        if callable(load):
            with contextlib.suppress(Exception):
                out = load()
                if inspect.isawaitable(out):
                    await cast(Awaitable[Any], out)

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        nested_args = args.get("tool_args")
        if isinstance(nested_args, dict):
            merged_args = dict(nested_args)
            merged_args.update({k: v for k, v in args.items() if k != "tool_args"})
            args = merged_args

        agent = str(args.get("agent") or args.get("agent_name") or args.get("name") or "").strip()
        request = str(args.get("request") or context.user_input or "").strip()
        timeout_seconds = float(args.get("timeout_seconds") or args.get("timeout") or 60.0)
        blocking_arg = args.get("blocking", False)
        blocking = (
            blocking_arg
            if isinstance(blocking_arg, bool)
            else str(blocking_arg).strip().lower() in {"1", "true", "yes", "on"}
        )

        if not request:
            return ToolResult.fail("Missing 'request'")

        if not agent:
            agents = _agents_from_registry(tool_registry)
            if not agents:
                return ToolResult.fail("No deployed A2A agents are registered")
            selection = await _route_agent(context, agents, request)
            if selection.agent_name == "none":
                return ToolResult.fail(
                    f"No suitable deployed agent found for this task. Reason: {selection.reasoning}"
                )
            agent = selection.agent_name
            request = selection.message
            logger.info("Agent routing: %s — %s", agent, selection.reasoning)

        cfg: dict[str, Any] | None = None
        if hasattr(tool_registry, "find_by_name"):
            with contextlib.suppress(Exception):
                cfg = tool_registry.find_by_name(agent)
        if not cfg and hasattr(tool_registry, "find_agent_by_capability"):
            with contextlib.suppress(Exception):
                cfg = tool_registry.find_agent_by_capability(agent)

        if not isinstance(cfg, dict):
            return ToolResult.fail(f"Unknown agent: {agent}")

        result, tracked_task = await dispatch_tracked_a2a_task(
            state_manager=context.state_manager,
            agent_name=str(cfg.get("name") or agent or "").strip() or agent,
            agent_config=cfg,
            request_text=request,
            request_metadata={
                "caller": "progenitor",
                "dispatch_mode": "blocking" if blocking else "async",
                "routing_mode": "explicit"
                if args.get("agent") or args.get("agent_name")
                else "auto",
            },
            source="call_agent",
            timeout=timeout_seconds,
            blocking=blocking,
            notify_on_completion=not blocking,
        )
        if not result.get("success"):
            return ToolResult.fail(str(result.get("error") or f"Agent call failed: {agent}"))

        if tracked_task is not None:
            payload = build_task_response(tracked_task)
            if not blocking:
                payload["message"] = (
                    f"Task submitted to '{agent}' (task_id: {tracked_task.task_id}). "
                    "Use get_a2a_task for progress and final results."
                )
            return ToolResult.ok(payload)

        if not blocking:
            return ToolResult.fail(f"{agent} did not return a trackable A2A task")

        return ToolResult.ok(result if isinstance(result, dict) else {"result": result})


def _agents_from_registry(tool_registry: Any) -> dict[str, dict[str, Any]]:
    """Return registered deployed agents keyed by name."""
    if tool_registry is None or not hasattr(tool_registry, "get_agents"):
        return {}
    try:
        agents = tool_registry.get_agents()
    except Exception:
        logger.exception("Failed to load registered agents for routing")
        return {}

    mapped: dict[str, dict[str, Any]] = {}
    if not isinstance(agents, list):
        return mapped

    for agent in agents:
        if not isinstance(agent, dict):
            continue
        name = str(agent.get("name") or "").strip()
        if not name:
            continue
        mapped[name] = dict(agent)
    return mapped


def _format_agents_for_prompt(agents: dict[str, Any]) -> str:
    """Render registered agents as compact routing context."""
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
    """Use the main LLM to choose the best deployed agent for a task."""
    if context.llm_client is None:
        return AgentSelection(
            agent_name="none",
            message=request_text,
            reasoning="LLM routing unavailable because llm_client is missing.",
        )

    agents_block = _format_agents_for_prompt(agents)
    system_prompt = (
        "You are an agent router. Select the best deployed A2A agent for the user task and "
        "craft a clear, complete message to send to that agent.\n"
        "Rules:\n"
        "- Only choose from the listed deployed agents.\n"
        "- If no deployed agent fits, set agent_name to 'none'.\n"
        "- The message field must be self-contained.\n"
        "- Keep reasoning to one sentence."
    )
    user_prompt = (
        f"## Available Deployed Agents\n\n{agents_block}\n\n"
        f"## Task\n\n{request_text}\n\n"
        "Select the best deployed agent and craft the message."
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


__all__ = ["CallAgentTool"]
