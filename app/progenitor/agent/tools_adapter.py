"""DeepAgent tool adapters.

Progenitor's system tools live under `app/progenitor/tools/` as `BaseSystemTool`
implementations. The DeepAgent needs to expose these as LangChain tools so the
LLM can invoke them directly during a conversation (e.g. `current_time`).

External MCP tools are loaded via langchain-mcp-adapters and surfaced directly
to DeepAgent as LangChain tools.
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


def create_deepagent_tools(
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
) -> list[StructuredTool]:
    """Create DeepAgent-compatible system tools."""

    async def _resolve_state() -> Any:
        state = None
        loop = get_current_loop()
        if loop is not None and isinstance(getattr(loop, "_state", None), dict):
            state = loop._state
        if state_manager is not None:
            get_state = getattr(state_manager, "get_current_state", None)
            if callable(get_state) and inspect.iscoroutinefunction(get_state):
                with contextlib.suppress(Exception):
                    state = state or await get_state()
        return state

    async def _execute_stateless_tool(
        system_tool: Any,
        *,
        user_input: str = "",
        tool_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx = ToolContext(
            tool_registry=tool_registry,
            llm_client=llm_client,
            state=None,
            state_manager=state_manager,
            user_input=user_input,
            tool_args=tool_args,
        )

        result = await system_tool.execute(ctx)
        if result.success:
            return result.data or {}
        return {
            "success": False,
            "error": result.error or f"{system_tool.name} failed",
            "should_retry_slow_path": bool(getattr(result, "should_retry_slow_path", False)),
        }

    async def _execute_stateful_tool(
        system_tool: Any,
        *,
        user_input: str = "",
        tool_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = await _resolve_state()
        ctx = ToolContext(
            tool_registry=tool_registry,
            llm_client=llm_client,
            state=cast(Any, state),
            state_manager=state_manager,
            user_input=user_input,
            tool_args=tool_args,
        )

        result = await system_tool.execute(ctx)
        if result.success:
            if result.state_updates and isinstance(state, dict):
                state.update(result.state_updates)
                update_state = (
                    getattr(state_manager, "update_current_state", None) if state_manager else None
                )
                if callable(update_state) and inspect.iscoroutinefunction(update_state):
                    with contextlib.suppress(Exception):
                        await update_state(result.state_updates)
            return result.data or {}
        return {
            "success": False,
            "error": result.error or f"{system_tool.name} failed",
            "should_retry_slow_path": bool(getattr(result, "should_retry_slow_path", False)),
        }

    def _build_tool(system_tool: Any) -> StructuredTool:
        name = str(getattr(system_tool, "name", "system_tool"))
        description = str(getattr(system_tool, "description", "Progenitor system tool"))
        args_schema = getattr(system_tool, "deepagent_args_schema", None)
        execute_tool = (
            _execute_stateful_tool
            if bool(getattr(system_tool, "deepagent_requires_state", False))
            else _execute_stateless_tool
        )

        if args_schema is None:

            async def _run_no_args() -> dict[str, Any]:
                return await execute_tool(system_tool)

            return StructuredTool.from_function(
                coroutine=_run_no_args,
                name=name,
                description=description,
            )

        async def _run_with_schema(**kwargs: Any) -> dict[str, Any]:
            user_input, tool_args = system_tool.deepagent_prepare_args(**kwargs)
            return await execute_tool(system_tool, user_input=user_input, tool_args=tool_args)

        return StructuredTool.from_function(
            coroutine=_run_with_schema,
            name=name,
            description=description,
            args_schema=args_schema,
        )

    return [_build_tool(system_tool) for system_tool in get_all_system_tools()]
