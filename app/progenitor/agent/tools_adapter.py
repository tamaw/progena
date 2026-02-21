"""
Tool adapter layer: wraps Progenitor's BaseSystemTool instances as
DeepAgent-compatible tools (plain async functions with typed parameters).

DeepAgent accepts tools as plain Python callables. This module auto-discovers
all Progenitor system tools and creates wrapper functions that the LLM can
call with structured arguments.

Tools that DeepAgent replaces natively (filesystem ops, planning) are excluded.
Tools that are deprecated in favour of DeepAgent capabilities are also excluded.
"""

import json
import logging
from typing import Any

from langchain_core.tools import StructuredTool

from ..nodes.node_helpers.registry import get_all_system_tools
from ..tools.protocol import ToolContext, ToolResult

logger = logging.getLogger(__name__)

# Tools that DeepAgent provides natively — skip wrapping these.
# Tools that DeepAgent provides natively — skip wrapping these.
DEEPAGENT_NATIVE_TOOLS: set[str] = set()
# DeepAgent has built-in filesystem tools (ls, read_file, write_file, etc.)
# DeepAgent has built-in planning (write_todos)
# DeepAgent has built-in subagent delegation (task)

# Tools deprecated in favour of DeepAgent capabilities (already removed from codebase).
DEPRECATED_TOOLS: set[str] = set()

# Tools that are internal/infrastructure and shouldn't be exposed to LLM.
INTERNAL_TOOLS = {
    "user_communication",     # Internal event pushing
    "rediscover_agents",      # Internal registry refresh
    "registry",               # Internal registry management
    "semantic_registry",      # Internal semantic search
}


def _make_tool_context(
    tool_registry: Any = None,
    llm_client: Any = None,
    state: Any = None,
    state_manager: Any = None,
    user_input: str = "",
    tool_args: dict[str, Any] | None = None,
) -> ToolContext:
    """Create a ToolContext for a Progenitor system tool."""
    return ToolContext(
        tool_registry=tool_registry,
        llm_client=llm_client,
        state=state,
        state_manager=state_manager,
        user_input=user_input,
        tool_args=tool_args,
    )


def _format_tool_result(result: ToolResult) -> str:
    """Convert ToolResult to a string for DeepAgent tool output."""
    if result.success:
        if result.data:
            return json.dumps(result.data, indent=2, default=str)
        return "Success"
    return f"Error: {result.error or 'Unknown error'}"


def create_deepagent_tools(
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
) -> list:
    """Create DeepAgent-compatible tool wrappers for all Progenitor system tools.

    Args:
        tool_registry: Progenitor's tool/agent registry.
        llm_client: Progenitor's LLM client (for tools that need AI).
        state_manager: Progenitor's StateManager (for persistence).

    Returns:
        List of LangChain tool objects ready for create_deep_agent(tools=...).
    """
    skip_tools = DEEPAGENT_NATIVE_TOOLS | DEPRECATED_TOOLS | INTERNAL_TOOLS
    system_tools = get_all_system_tools()

    wrapped_tools = []
    for sys_tool in system_tools:
        if sys_tool.name in skip_tools:
            logger.debug("Skipping tool %s (native/deprecated/internal)", sys_tool.name)
            continue

        wrapper = _wrap_system_tool(
            sys_tool,
            tool_registry=tool_registry,
            llm_client=llm_client,
            state_manager=state_manager,
        )
        if wrapper is not None:
            wrapped_tools.append(wrapper)

    logger.info(
        "Created %d DeepAgent tool wrappers (skipped %d)",
        len(wrapped_tools),
        len(skip_tools),
    )
    return wrapped_tools


def _wrap_system_tool(
    sys_tool: Any,
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
) -> Any:
    """Wrap a single Progenitor system tool as a LangChain tool.

    Most Progenitor tools accept a single text 'request' parameter.
    The wrapper creates a function with that signature and calls
    the underlying tool's execute() method.
    """
    tool_name = sys_tool.name
    tool_desc = sys_tool.description

    async def _execute(request: str = "") -> str:
        """Execute a Progenitor system tool.

        Args:
            request: The request or command to execute.
        """
        context = _make_tool_context(
            tool_registry=tool_registry,
            llm_client=llm_client,
            state_manager=state_manager,
            user_input=request,
            tool_args={"request": request} if request else None,
        )

        try:
            result = await sys_tool.execute(context)
            return _format_tool_result(result)
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_name, e, exc_info=True)
            return f"Error: {e}"

    return StructuredTool.from_function(
        coroutine=_execute,
        name=tool_name,
        description=tool_desc,
    )
