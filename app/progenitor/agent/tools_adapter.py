"""DeepAgent tool adapters.

Progenitor's system tools live under `app/progenitor/tools/` as `BaseSystemTool`
implementations. The DeepAgent needs to expose these as LangChain tools so the
LLM can invoke them directly during a conversation (e.g. `current_time`).
"""

import contextlib
import inspect
from typing import Any, cast

from langchain_core.tools import StructuredTool

from ..nodes.node_helpers.registry import get_all_system_tools
from ..runtime.loop import get_current_loop
from ..tools.protocol import ToolContext


def create_deepagent_tools(
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
 ) -> list:
    """Create DeepAgent-compatible tools (system tools + DeepAgent-only helpers)."""
    def _wrap_system_tool(system_tool: Any) -> StructuredTool:
        async def _execute_wrapped_tool(
            request: str = "",
            tool_args: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            # External deploy pipelines can update agents.json; reload best-effort so new
            # agents/tools show up without requiring a restart.
            load_registry = getattr(tool_registry, "load", None) if tool_registry is not None else None
            if callable(load_registry):
                with contextlib.suppress(Exception):
                    maybe = load_registry()
                    if inspect.isawaitable(maybe):
                        await cast(Any, maybe)

            # Prefer the live loop state when running under ProgenitorLoop so tools that
            # mutate state (e.g. new_conversation) take effect immediately.
            state = None
            loop = get_current_loop()
            if loop is not None and isinstance(getattr(loop, "_state", None), dict):
                state = loop._state
            if state_manager is not None:
                get_state = getattr(state_manager, "get_current_state", None)
                if callable(get_state) and inspect.iscoroutinefunction(get_state):
                    with contextlib.suppress(Exception):
                        state = state or await get_state()

            ctx = ToolContext(
                tool_registry=tool_registry,
                llm_client=llm_client,
                state=cast(Any, state),
                state_manager=state_manager,
                user_input=request or "",
                tool_args=tool_args,
            )

            result = await system_tool.execute(ctx)
            if result.success:
                # Persist/propagate state updates when provided (DeepAgent doesn't
                # automatically merge ToolResult.state_updates like our node path).
                if result.state_updates and isinstance(state, dict):
                    state.update(result.state_updates)
                    update_state = getattr(state_manager, "update_current_state", None) if state_manager else None
                    if callable(update_state) and inspect.iscoroutinefunction(update_state):
                        with contextlib.suppress(Exception):
                            await update_state(result.state_updates)
                return result.data or {}
            return {
                "success": False,
                "error": result.error or f"{system_tool.name} failed",
                "should_retry_slow_path": bool(getattr(result, "should_retry_slow_path", False)),
            }

        async def _run(request: str = "", tool_args: dict[str, Any] | None = None) -> dict[str, Any]:
            return await _execute_wrapped_tool(request=request, tool_args=tool_args)

        async def _run_call_agent(
            request: str = "",
            agent: str = "",
            agent_name: str = "",
            name: str = "",
            timeout_seconds: float = 60.0,
            context: dict[str, Any] | None = None,
            tool_args: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            merged_args: dict[str, Any] = dict(tool_args or {})
            merged_args.setdefault("agent", agent)
            merged_args.setdefault("agent_name", agent_name)
            merged_args.setdefault("name", name)
            merged_args.setdefault("request", request)
            merged_args.setdefault("timeout_seconds", timeout_seconds)
            if isinstance(context, dict) and context:
                merged_args.setdefault("context", context)
            return await _execute_wrapped_tool(request=request, tool_args=merged_args)

        coroutine_fn = _run_call_agent if str(getattr(system_tool, "name", "")) == "call_agent" else _run

        return StructuredTool.from_function(
            coroutine=coroutine_fn,
            name=str(getattr(system_tool, "name", "system_tool")),
            description=str(getattr(system_tool, "description", "Progenitor system tool")),
        )

    # System tools (dynamic discovery under app/progenitor/tools/)
    return [_wrap_system_tool(system_tool) for system_tool in get_all_system_tools()]
