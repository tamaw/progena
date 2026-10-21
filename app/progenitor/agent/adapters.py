"""
LangChain model adapter for Progenitor's direct Azure endpoint.

Routes all LLM calls through the configured Azure endpoint using LangChain's
ChatOpenAI, which is compatible with any OpenAI-compatible endpoint.
This preserves Progenitor's model routing and auth while enabling DeepAgent integration.

Supports optional extended thinking/reasoning for models that support it
(e.g., GPT-5.2-chat with reasoning enabled).
"""

import asyncio
import contextlib
import inspect
import logging
from collections.abc import Callable
from typing import Annotated, Any, cast
from urllib.parse import urlsplit, urlunsplit

from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from ..config import LLMConfig
from ..runtime.thread_context import get_current_thread_id
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

    # Azure's Responses-compatible endpoint returns reliable reasoning usage
    # when effort is passed through ChatOpenAI's legacy reasoning_effort option
    # and the summary is supplied in the request body. The OpenAI SDK shallowly
    # merges extra_body into its generated request, so extra_body must repeat
    # effort or it replaces the generated {"effort": ...} reasoning object.
    # Keep this shape because the native reasoning parameter produced summaries
    # with zero usage counts.
    reasoning_effort: str | None = None
    extra_body: dict[str, Any] = {}
    if config.reasoning_enabled:
        reasoning_effort = config.reasoning_effort
        extra_body["reasoning"] = {
            "effort": config.reasoning_effort,
            "summary": config.reasoning_summary,
        }
        logger.info(
            "Reasoning enabled for model %s (effort=%s, summary=%s)",
            model_name,
            config.reasoning_effort,
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
        extra_body=extra_body or None,
        timeout=config.timeout_seconds,
        max_retries=config.max_retries,
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
        except Exception:
            logger.warning("Failed to load current state for tool context", exc_info=True)
    return None


_reload_inflight: asyncio.Future[None] | None = None


async def _maybe_reload(tool_registry: Any) -> None:
    """Best-effort hot-reload so newly-deployed agents/tools show up.

    Deduplicates concurrent reloads: if another task is already reloading,
    await that existing task instead of starting a redundant one.
    """
    global _reload_inflight
    if tool_registry is None:
        return
    load = getattr(tool_registry, "load", None)
    if not callable(load):
        return
    inflight = _reload_inflight
    if inflight is not None and not inflight.done():
        with contextlib.suppress(Exception):
            await inflight
        return
    maybe = load()
    if inspect.isawaitable(maybe):
        inflight = asyncio.ensure_future(cast(Any, maybe))
    else:
        # Synchronous load — wrap in an already-resolved future so the
        # rest of the flow (await + exception handling) is uniform.
        inflight = asyncio.get_running_loop().create_future()
        inflight.set_result(None)
    _reload_inflight = inflight
    try:
        await inflight
    except Exception:
        logger.warning("Failed to reload tool registry before tool execution", exc_info=True)


def wrap_system_tool(
    system_tool: Any,
    *,
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
    live_state_provider: Callable[[], Any] | None = None,
    late_bindings: dict[str, Any] | None = None,
) -> StructuredTool:
    """Wrap one system tool as a DeepAgent-compatible StructuredTool.

    ``live_state_provider`` returns the running loop's mutable state dict so tool
    state updates land in live conversation state; without it, tools fall back to
    a snapshot from ``state_manager``. ``late_bindings`` is a mutable box
    agent.py populates *after* the tools are built (``resume_routine`` and
    ``thread_state`` need the registries / compiled agent that don't exist
    yet) -- read here at call time, not at wrap time, so tools see the real
    functions once construction finishes.
    """

    async def _run(
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
        **kwargs: Any,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        await _maybe_reload(tool_registry)
        state = await _resolve_state(state_manager, live_state_provider)

        ctx = ToolContext(
            tool_registry=tool_registry,
            llm_client=llm_client,
            state=cast(Any, state),
            state_manager=state_manager,
            user_input=str(kwargs.get("request") or ""),
            tool_args=dict(kwargs),
            tool_call_id=tool_call_id,
            resume_routine=(late_bindings or {}).get("resume_routine"),
            thread_state=(late_bindings or {}).get("thread_state"),
        )
        result = await system_tool.execute(ctx)
        if not result.success:
            return (
                {
                    "success": False,
                    "error": result.error or f"{system_tool.name} failed",
                    "should_retry_slow_path": bool(
                        getattr(result, "should_retry_slow_path", False)
                    ),
                },
                None,
            )

        # DeepAgent doesn't merge ToolResult.state_updates automatically; do it here.
        # When a per-thread direct-chat turn is active, persist updates to the
        # thread-specific document via Postgres UPSERT instead of mutating the
        # shared heartbeat system-state dict.
        thread_id = get_current_thread_id()
        if result.state_updates:
            if thread_id is not None:
                update = (
                    getattr(state_manager, "update_current_state", None) if state_manager else None
                )
                if callable(update) and inspect.iscoroutinefunction(update):
                    try:
                        await update(result.state_updates, thread_id=thread_id)
                    except Exception:
                        logger.warning(
                            "Failed to persist state updates from tool %s",
                            getattr(system_tool, "name", "system_tool"),
                            exc_info=True,
                        )
            elif isinstance(state, dict):
                state.update(result.state_updates)
                update = (
                    getattr(state_manager, "update_current_state", None) if state_manager else None
                )
                if callable(update) and inspect.iscoroutinefunction(update):
                    try:
                        await update(result.state_updates)
                    except Exception:
                        logger.warning(
                            "Failed to persist state updates from tool %s",
                            getattr(system_tool, "name", "system_tool"),
                            exc_info=True,
                        )
        return (result.data or {}, result.artifact)

    return StructuredTool.from_function(
        coroutine=_run,
        name=str(getattr(system_tool, "name", "system_tool")),
        description=str(getattr(system_tool, "description", "Progenitor system tool")),
        args_schema=system_tool.args_model,
        response_format="content_and_artifact",
    )


def create_deepagent_tools(
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
    live_state_provider: Callable[[], Any] | None = None,
    late_bindings: dict[str, Any] | None = None,
) -> list[StructuredTool]:
    """Wrap every registered system tool (see ``wrap_system_tool``)."""
    return [
        wrap_system_tool(
            tool,
            tool_registry=tool_registry,
            llm_client=llm_client,
            state_manager=state_manager,
            live_state_provider=live_state_provider,
            late_bindings=late_bindings,
        )
        for tool in get_all_system_tools()
    ]


async def create_deep_agent_for_runtime(
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

    return await create_conversation_graph(
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
