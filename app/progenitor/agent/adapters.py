"""
LangChain model adapter for Progenitor's direct Azure endpoint.

Routes all LLM calls through the configured Azure endpoint using LangChain's
ChatOpenAI, which is compatible with any OpenAI-compatible endpoint.
This preserves Progenitor's model routing and auth while enabling DeepAgent integration.

Supports optional extended thinking/reasoning for models that support it
(e.g., GPT-5.2-chat with reasoning enabled).
"""

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.types import Command
from pydantic import SecretStr

from ..config import LLMConfig
from ..tools.protocol import ToolContext
from ..tools.system_registry import get_all_system_tools

logger = logging.getLogger(__name__)


def _normalize_langchain_base_url(base_url: str) -> str:
    """Normalize env URL to OpenAI-compatible base URL for ChatOpenAI.

    Accepts either:
    - https://.../openai/v1
    - https://.../openai/v1/responses
    - https://.../openai/v1/responses?api-version=preview
    """
    raw = (base_url or "").strip()
    if not raw:
        return raw

    parsed = urlsplit(raw)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/responses"):
        path = path[: -len("/responses")]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _resolve_reasoning_effort(config: LLMConfig) -> str:
    """Return the configured reasoning effort for the active model."""
    return config.reasoning_effort


def create_langchain_model(config: LLMConfig | None = None) -> ChatOpenAI:
    """Create a LangChain ChatOpenAI instance routed through Azure.

    Args:
        config: LLM configuration. If None, loads from environment.
    Returns:
        ChatOpenAI instance pointing at configured Azure endpoint.
    """
    if config is None:
        config = LLMConfig.from_env()

    model_name = config.model_default

    base_url = _normalize_langchain_base_url(config.base_url)

    # Azure's ChatOpenAI adapter exposes the final reasoning summary reliably
    # when effort is passed via `reasoning_effort` and summary mode rides
    # through `extra_body.reasoning.summary`.
    reasoning_effort: str | None = None
    extra_body: dict[str, dict[str, str]] | None = None
    if config.reasoning_enabled:
        reasoning_effort = _resolve_reasoning_effort(config)
        extra_body = {"reasoning": {"summary": config.reasoning_summary}}
        logger.info(
            "Reasoning enabled for model %s (effort=%s, summary=%s)",
            model_name,
            reasoning_effort,
            config.reasoning_summary,
        )

    model = ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=SecretStr(config.api_key or "not-needed"),
        default_headers={
            "api-key": config.api_key or "",
        },
        use_responses_api=True,
        output_version="responses/v1",
        streaming=True,
        reasoning_effort=reasoning_effort,
        extra_body=extra_body,
    )

    logger.info(
        "Created LangChain model adapter: model=%s, base_url=%s, responses_api=%s, reasoning=%s",
        model_name,
        base_url,
        True,
        config.reasoning_enabled,
    )
    return model


def create_dynamic_mcp_tools(llm_client: Any = None) -> list[BaseTool]:
    """Return the live MCP tools loaded through langchain-mcp-adapters."""
    runtime = getattr(llm_client, "mcp_runtime", None)
    get_tools = getattr(runtime, "get_langchain_tools", None)
    if not callable(get_tools):
        return []
    try:
        raw_tools = get_tools()
    except Exception:
        logger.debug("Failed to load dynamic MCP tools from runtime", exc_info=True)
        return []
    if not isinstance(raw_tools, list):
        return []
    return [tool for tool in raw_tools if isinstance(tool, BaseTool)]


def _request_tool_name(tool: Any) -> str:
    if hasattr(tool, "name"):
        return str(tool.name or "").strip()
    if isinstance(tool, dict):
        function = tool.get("function")
        if isinstance(function, dict):
            return str(function.get("name") or "").strip()
        return str(tool.get("name") or "").strip()
    return ""


class DynamicMCPToolMiddleware(AgentMiddleware):
    """Inject and bind the current live external MCP tools for each agent turn."""

    def __init__(self, mcp_tools_provider: Callable[[], list[BaseTool]] | None = None):
        self._mcp_tools_provider = mcp_tools_provider or (lambda: [])
        self._warned_name_collisions: set[str] = set()

    def _dynamic_tools_by_name(self) -> dict[str, BaseTool]:
        tools_by_name: dict[str, BaseTool] = {}
        for tool in self._mcp_tools_provider():
            name = _request_tool_name(tool)
            if name:
                tools_by_name[name] = tool
        return tools_by_name

    def _merge_request_tools(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        dynamic_tools = self._dynamic_tools_by_name()
        if not dynamic_tools:
            return request

        existing_tools = list(request.tools or [])
        merged_tools = list(existing_tools)
        seen_names: set[str] = set()
        for tool in existing_tools:
            name = _request_tool_name(tool)
            if name:
                seen_names.add(name)

        for tool in dynamic_tools.values():
            if tool.name in seen_names:
                if tool.name not in self._warned_name_collisions:
                    logger.warning(
                        "Skipping dynamic MCP tool '%s' because a tool with that name already exists",
                        tool.name,
                    )
                    self._warned_name_collisions.add(tool.name)
                continue
            seen_names.add(tool.name)
            merged_tools.append(tool)

        return request.override(tools=merged_tools)

    def _bind_tool_request(self, request: ToolCallRequest) -> ToolCallRequest:
        if request.tool is not None:
            return request

        tool_name = _request_tool_name(request.tool_call)
        if not tool_name:
            return request

        dynamic_tool = self._dynamic_tools_by_name().get(tool_name)
        if dynamic_tool is None:
            return request

        return replace(request, tool=dynamic_tool)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Any],
    ) -> Any:
        return handler(self._merge_request_tools(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        return await handler(self._merge_request_tools(request))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return handler(self._bind_tool_request(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        return await handler(self._bind_tool_request(request))


async def _resolve_state(
    state_manager: Any,
    live_state_provider: Callable[[], Any] | None = None,
) -> Any:
    """Return the live runtime state, falling back to the state manager."""
    if live_state_provider is not None:
        state = live_state_provider()
        if isinstance(state, dict):
            return state
    if state_manager is None:
        return None
    get_state = getattr(state_manager, "get_current_state", None)
    if callable(get_state) and inspect.iscoroutinefunction(get_state):
        try:
            return await get_state()
        except Exception as exc:
            logger.warning("Failed to load current state for tool context: %s", exc)
    return None


async def _maybe_reload(tool_registry: Any) -> None:
    """Best-effort hot-reload so newly-deployed agents/tools show up."""
    if tool_registry is None:
        return
    load = getattr(tool_registry, "load", None)
    if not callable(load):
        return
    try:
        maybe = load()
        if inspect.isawaitable(maybe):
            await cast(Any, maybe)
    except Exception as exc:
        logger.warning("Failed to reload tool registry before tool execution: %s", exc)


def create_deepagent_tools(
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
    live_state_provider: Callable[[], Any] | None = None,
) -> list[StructuredTool]:
    """Wrap every registered system tool as a DeepAgent-compatible StructuredTool.

    ``live_state_provider`` returns the running loop's mutable state dict so tool
    state updates land in live conversation state; without it, tools fall back to
    a snapshot from ``state_manager``.
    """

    def _wrap(system_tool: Any) -> StructuredTool:
        async def _run(**kwargs: Any) -> dict[str, Any]:
            await _maybe_reload(tool_registry)
            state = await _resolve_state(state_manager, live_state_provider)

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
                    try:
                        await update(result.state_updates)
                    except Exception as exc:
                        logger.warning(
                            "Failed to persist state updates from tool %s: %s",
                            getattr(system_tool, "name", "system_tool"),
                            exc,
                        )
            return result.data or {}

        return StructuredTool.from_function(
            coroutine=_run,
            name=str(getattr(system_tool, "name", "system_tool")),
            description=str(getattr(system_tool, "description", "Progenitor system tool")),
            args_schema=system_tool.args_model,
        )

    return [_wrap(tool) for tool in get_all_system_tools()]


def create_deep_agent_for_runtime(
    config: Any | None = None,
    checkpointer: Any | None = None,
    store: Any | None = None,
    tool_registry: Any | None = None,
    llm_client: Any | None = None,
    state_manager: Any | None = None,
    project_root: str | None = None,
    log_sink: Any | None = None,
    live_state_provider: Callable[[], Any] | None = None,
) -> Any:
    """Create the runtime conversation graph backed by Progenitor's agent setup."""
    from ..config import get_config
    from ..graph.progenitor_graph import create_conversation_graph

    if config is None:
        config = get_config(validate=False)

    return create_conversation_graph(
        config=config,
        checkpointer=checkpointer,
        store=store,
        tool_registry=tool_registry,
        llm_client=llm_client,
        state_manager=state_manager,
        project_root=project_root,
        log_sink=log_sink,
        live_state_provider=live_state_provider,
    )
