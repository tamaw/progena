"""DeepAgent tool adapters.

Progenitor's system tools live under `app/progenitor/tools/` as `BaseSystemTool`
implementations. The DeepAgent needs to expose these as LangChain tools so the
LLM can invoke them directly during a conversation (e.g. `current_time`).
"""

import contextlib
import inspect
from typing import Any

from langchain_core.tools import StructuredTool

from ..nodes.node_helpers.registry import get_all_system_tools
from ..tools.protocol import ToolContext


def create_deepagent_tools(
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
) -> list:
    """Create DeepAgent-compatible tools (system tools + DeepAgent-only helpers)."""
    def _wrap_system_tool(system_tool: Any) -> StructuredTool:
        async def _run(request: str = "", tool_args: dict[str, Any] | None = None) -> dict[str, Any]:
            state = None
            if state_manager is not None:
                get_state = getattr(state_manager, "get_current_state", None)
                if callable(get_state) and inspect.iscoroutinefunction(get_state):
                    with contextlib.suppress(Exception):
                        state = await get_state()

            ctx = ToolContext(
                tool_registry=tool_registry,
                llm_client=llm_client,
                state=state,
                state_manager=state_manager,
                user_input=request or "",
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

        return StructuredTool.from_function(
            coroutine=_run,
            name=str(getattr(system_tool, "name", "system_tool")),
            description=str(getattr(system_tool, "description", "Progenitor system tool")),
        )

    # System tools (dynamic discovery under app/progenitor/tools/)
    return [_wrap_system_tool(system_tool) for system_tool in get_all_system_tools()]
