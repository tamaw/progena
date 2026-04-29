"""DeepAgent tool adapters.

Wraps each `BaseSystemTool` as a LangChain `StructuredTool` so the deepagents
package can register them alongside its built-in tools. Each tool's
`args_model` is the single source of truth for the LLM-facing schema, and the
LLM's structured kwargs land in `ToolContext.tool_args` for `execute()` to
consume.
"""

import contextlib
import inspect
from typing import Any, cast

from langchain_core.tools import BaseTool, StructuredTool

from ..nodes.node_helpers.registry import get_all_system_tools
from ..runtime.loop import get_current_loop
from ..tools.protocol import ToolContext


def create_dynamic_mcp_tools(llm_client: Any = None) -> list[BaseTool]:
    """Return the live MCP tools loaded through langchain-mcp-adapters."""
    raw_tools = getattr(llm_client, "mcp_langchain_tools", None)
    if not isinstance(raw_tools, list):
        return []
    return [tool for tool in raw_tools if isinstance(tool, BaseTool)]


async def _resolve_state(state_manager: Any) -> Any:
    """Return the live ProgenitorLoop state, falling back to the state manager."""
    loop = get_current_loop()
    if loop is not None and isinstance(getattr(loop, "_state", None), dict):
        return loop._state
    if state_manager is None:
        return None
    get_state = getattr(state_manager, "get_current_state", None)
    if callable(get_state) and inspect.iscoroutinefunction(get_state):
        with contextlib.suppress(Exception):
            return await get_state()
    return None


async def _maybe_reload(tool_registry: Any) -> None:
    """Best-effort hot-reload so newly-deployed agents/tools show up."""
    if tool_registry is None:
        return
    load = getattr(tool_registry, "load", None)
    if not callable(load):
        return
    with contextlib.suppress(Exception):
        maybe = load()
        if inspect.isawaitable(maybe):
            await cast(Any, maybe)


def create_deepagent_tools(
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
) -> list[StructuredTool]:
    """Wrap every registered system tool as a DeepAgent-compatible StructuredTool."""

    def _wrap(system_tool: Any) -> StructuredTool:
        async def _run(**kwargs: Any) -> dict[str, Any]:
            await _maybe_reload(tool_registry)
            state = await _resolve_state(state_manager)

            ctx = ToolContext(
                tool_registry=tool_registry,
                llm_client=llm_client,
                state=cast(Any, state),
                state_manager=state_manager,
                user_input=str(kwargs.get("request") or ""),
                tool_args=dict(kwargs),
            )
            result = await system_tool.execute(ctx)
            if not result.success:
                return {
                    "success": False,
                    "error": result.error or f"{system_tool.name} failed",
                    "should_retry_slow_path": bool(
                        getattr(result, "should_retry_slow_path", False)
                    ),
                }

            # DeepAgent doesn't merge ToolResult.state_updates automatically; do it here.
            if result.state_updates and isinstance(state, dict):
                state.update(result.state_updates)
                update = (
                    getattr(state_manager, "update_current_state", None) if state_manager else None
                )
                if callable(update) and inspect.iscoroutinefunction(update):
                    with contextlib.suppress(Exception):
                        await update(result.state_updates)
            return result.data or {}

        return StructuredTool.from_function(
            coroutine=_run,
            name=str(getattr(system_tool, "name", "system_tool")),
            description=str(getattr(system_tool, "description", "Progenitor system tool")),
            args_schema=system_tool.args_model,
        )

    return [_wrap(tool) for tool in get_all_system_tools()]
