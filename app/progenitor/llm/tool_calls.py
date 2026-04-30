"""Responses API tool-call extraction and result construction."""

import json
from typing import Any


def extract_tool_calls(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract tool calls from a Responses API payload."""
    tool_calls: list[dict[str, Any]] = []
    tool_call_types = {"tool_call", "function_call"}

    for item in data.get("output", []):
        if item.get("type") in tool_call_types:
            tool_calls.append(item)
            continue

        if item.get("type") == "message":
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") in tool_call_types:
                        tool_calls.append(part)

    if isinstance(data.get("tool_calls"), list):
        tool_calls.extend(data["tool_calls"])

    return tool_calls


def get_tool_call_id(tool_call: dict[str, Any]) -> Any:
    """Return the provider identifier for a tool call."""
    return tool_call.get("call_id") or tool_call.get("id") or tool_call.get("tool_call_id")


def get_tool_call_name(tool_call: dict[str, Any]) -> str:
    """Return the function/tool name for a tool call."""
    return str(
        tool_call.get("name")
        or tool_call.get("tool_name")
        or tool_call.get("function", {}).get("name")
        or "(unknown)"
    )


def parse_tool_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
    """Parse tool-call arguments into a dictionary."""
    raw_args = (
        tool_call.get("arguments")
        or tool_call.get("input")
        or tool_call.get("params")
        or tool_call.get("function", {}).get("arguments")
        or {}
    )
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            return {"raw": raw_args}
        return parsed if isinstance(parsed, dict) else {"raw": parsed}
    if isinstance(raw_args, dict):
        return raw_args
    return {"raw": raw_args}


def build_tool_call_signature(tool_name: str, tool_args: dict[str, Any]) -> str:
    """Build a stable signature for repeated-tool-call detection."""
    try:
        return f"{tool_name}:{json.dumps(tool_args, sort_keys=True, default=str)}"
    except Exception:
        return f"{tool_name}:{str(tool_args)}"


def build_tool_result_message(
    *,
    tool_name: str,
    tool_call_id: Any,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Build a Responses API message containing a tool result."""
    result_text = json.dumps(result, default=str)
    return {
        "role": "user",
        "content": f"[TOOL_RESULT tool={tool_name} id={tool_call_id}] {result_text}",
    }


async def execute_system_or_agent_tool(
    *,
    llm_client: Any,
    tool_name: str,
    tool_args: dict[str, Any],
    system_tool_context: dict[str, Any],
) -> dict[str, Any] | None:
    """Execute a registered system tool or direct A2A agent tool if available."""
    from ..tools.system_registry import execute_system_tool, get_system_tool

    tool_registry = system_tool_context.get("tool_registry")
    state_manager = system_tool_context.get("state_manager")
    state = system_tool_context.get("state")
    default_user_input = system_tool_context.get("default_user_input", "")
    tool_user_input = str(tool_args.get("request") or default_user_input or "")

    if get_system_tool(tool_name):
        data_out, err, state_updates, _retry = await execute_system_tool(
            tool_name=tool_name,
            tool_registry=tool_registry,
            llm_client=llm_client,
            state=state,
            state_manager=state_manager,
            user_input=tool_user_input,
            tool_args=tool_args,
        )
        propagate_state_updates(system_tool_context, state, state_updates)
        if err:
            return {"error": err}
        return {"result": data_out}

    if tool_registry is None or not hasattr(tool_registry, "find_by_name"):
        return None

    try:
        agent_cfg = tool_registry.find_by_name(tool_name)
    except Exception:
        agent_cfg = None

    agent_url = str(agent_cfg.get("url") or "").strip() if isinstance(agent_cfg, dict) else ""
    agent_type = str(agent_cfg.get("type") or "").strip() if isinstance(agent_cfg, dict) else ""
    agent_kind = str(agent_cfg.get("kind") or "").strip() if isinstance(agent_cfg, dict) else ""
    agent_transport = (
        str(agent_cfg.get("transport") or "http").strip() if isinstance(agent_cfg, dict) else ""
    )
    if not (
        isinstance(agent_cfg, dict)
        and agent_url
        and agent_transport == "http"
        and agent_type != "mcp_tool"
        and agent_kind != "mcp_tool"
    ):
        return None

    forwarded_args = dict(tool_args)
    forwarded_args.setdefault("agent", str(agent_cfg.get("name") or tool_name))

    data_out, err, state_updates, _retry = await execute_system_tool(
        tool_name="call_agent",
        tool_registry=tool_registry,
        llm_client=llm_client,
        state=state,
        state_manager=state_manager,
        user_input=tool_user_input,
        tool_args=forwarded_args,
    )
    propagate_state_updates(system_tool_context, state, state_updates)
    if err:
        return {"error": err}
    return {"result": data_out}


def propagate_state_updates(
    system_tool_context: dict[str, Any],
    state: Any,
    state_updates: Any,
) -> None:
    """Apply state updates returned by a system tool to live state holders."""
    if state_updates and isinstance(system_tool_context.get("state_updates"), dict):
        system_tool_context["state_updates"].update(state_updates)
    if state_updates and isinstance(state, dict):
        state.update(state_updates)
