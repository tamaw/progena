"""LangChain Responses API adapters backed by persisted providers."""

import asyncio
import contextlib
import inspect
import logging
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import Annotated, Any, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from langchain_openai import ChatOpenAI
from pydantic import ConfigDict, Field, SecretStr

from src.progena.llm.provider_context import (
    ProviderNotConfiguredError,
    ProviderTurn,
    activate_provider_turn,
    current_provider_turn,
    provider_config_for_call,
)
from src.progena.sessions.context import get_current_thread_id

from .. import __version__
from ..config import LLMConfig
from ..tools.protocol import ToolContext
from ..tools.system_registry import get_all_system_tools
from .checkpoint_usage import CheckpointChatOpenAI

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


def create_langchain_model(
    config: LLMConfig | None = None,
    *,
    session_id: str | None = None,
    profile_name: str | None = None,
) -> ChatOpenAI:
    """Create a Responses-only model from an already resolved provider snapshot.

    ``session_id`` scopes OpenCode Go requests, while ``profile_name`` is retained
    on checkpointed messages for model-usage attribution.
    """
    config = config or LLMConfig()
    if not config.base_url or not config.api_key or not config.model_default:
        raise ProviderNotConfiguredError(
            "No model provider is configured. Add and select a provider in Settings."
        )

    if session_id is None:
        turn = current_provider_turn()
        session_id = turn.session_id if turn is not None else None
    model_name = config.model_default
    if config.provider_kind == "chatgpt":
        # ChatGPT-plan OAuth tokens are accepted only by the public direct
        # route, never an Azure-compatible endpoint.
        base_url = "https://api.openai.com/v1"
        headers = {"Authorization": f"Bearer {config.api_key}"}
        reasoning_effort = None
        extra_body = (
            {
                "reasoning": {
                    "effort": config.reasoning_effort,
                    "summary": config.reasoning_summary,
                }
            }
            if config.reasoning_enabled
            else None
        )
    else:
        base_url = _normalize_langchain_base_url(config.base_url)
        if config.provider_kind == "opencode_go":
            headers = {
                "Authorization": f"Bearer {config.api_key}",
                "User-Agent": f"progena/{__version__}",
            }
            if session_id:
                headers["x-opencode-session"] = session_id
            reasoning_effort = None
            extra_body = None
        else:
            headers = {"api-key": config.api_key}
            reasoning_effort = config.reasoning_effort if config.reasoning_enabled else None
            extra_body = (
                {
                    "reasoning": {
                        "effort": config.reasoning_effort,
                        "summary": config.reasoning_summary,
                    }
                }
                if config.reasoning_enabled
                else None
            )

    model = CheckpointChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=SecretStr(config.api_key),
        default_headers=headers,
        use_responses_api=True,
        use_previous_response_id=False,
        output_version="responses/v1",
        streaming=True,
        reasoning_effort=reasoning_effort,
        extra_body=extra_body or None,
        timeout=config.timeout_seconds,
        max_retries=config.max_retries,
    )
    model.set_provider_connection(
        provider_id=config.provider_id,
        provider_kind=config.provider_kind,
        base_url=config.base_url,
    )
    model.set_provider_kind(config.provider_kind)
    if config.provider_kind == "chatgpt" and config.provider_id:
        model.set_chatgpt_provider(config.provider_id, config.api_key)
    model.set_usage_profile(profile_name)
    logger.info(
        "Created LangChain model adapter: provider=%s model=%s responses_api=%s reasoning=%s",
        config.provider_kind,
        model_name,
        True,
        bool(reasoning_effort),
    )
    return model


def _generation_source(target: Any) -> tuple[BaseChatModel, dict[str, Any]]:
    """Extract a chat model and its bound invocation settings from a runnable.

    ``bind`` and ``bind_tools`` return a runnable binding, while structured-output
    helpers add parser runnables after that binding.  The private chat-model hooks
    must return generation objects, so they call the source chat model rather than
    executing those parsers.
    """
    if isinstance(target, BaseChatModel):
        return target, {}

    bound = getattr(target, "bound", None)
    bound_kwargs = getattr(target, "kwargs", None)
    if isinstance(bound, BaseChatModel) and isinstance(bound_kwargs, dict):
        return bound, bound_kwargs

    first = getattr(target, "first", None)
    if first is not None:
        return _generation_source(first)

    steps = getattr(target, "steps__", None)
    if isinstance(steps, dict) and "raw" in steps:
        return _generation_source(steps["raw"])

    raise TypeError("Configured provider runnable does not expose a chat model for generation")


def _generation_kwargs(
    bound_kwargs: dict[str, Any], invocation_kwargs: dict[str, Any]
) -> dict[str, Any]:
    """Apply runnable binding settings with normal invocation override precedence."""
    if not bound_kwargs:
        return invocation_kwargs
    if not invocation_kwargs:
        return bound_kwargs
    return {**bound_kwargs, **invocation_kwargs}


class ProviderChatModel(BaseChatModel):
    """Lazily bind every model invocation to the active provider-turn snapshot."""

    template: LLMConfig = Field(default_factory=LLMConfig, exclude=True)
    provider_id: str | None = None
    profile_name: str | None = None
    model_override: str | None = None
    bound_tools: tuple[Any, ...] = Field(default_factory=tuple, exclude=True)
    bound_tool_choice: Any = Field(default=None, exclude=True)
    bound_settings: dict[str, Any] = Field(default_factory=dict, exclude=True)
    structured_schema: Any = Field(default=None, exclude=True)
    structured_include_raw: bool = Field(default=False, exclude=True)
    structured_settings: dict[str, Any] = Field(default_factory=dict, exclude=True)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "progena_provider"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "profile_name": self.profile_name,
            "model_override": self.model_override,
        }

    def with_model_override(self, model_override: str) -> "ProviderChatModel":
        """Return an immutable manifest model override on the same connection."""
        return self.model_copy(update={"model_override": model_override})

    async def _configured_model(self) -> Any:
        config = await provider_config_for_call(
            self.template,
            provider_id=self.provider_id,
            model_override=self.model_override,
        )
        target: Any = create_langchain_model(config, profile_name=self.profile_name)
        if self.structured_schema is not None:
            return target.with_structured_output(
                self.structured_schema,
                include_raw=self.structured_include_raw,
                **self.structured_settings,
            )
        if self.bound_tools:
            return target.bind_tools(
                list(self.bound_tools),
                tool_choice=self.bound_tool_choice,
                **self.bound_settings,
            )
        if self.bound_settings:
            return target.bind(**self.bound_settings)
        return target

    async def _model_for_invocation(self) -> Any:
        """Resolve a direct invocation with an isolated immutable turn if needed."""
        if current_provider_turn() is not None:
            return await self._configured_model()
        turn = await ProviderTurn.create(
            self.template,
            session_id=f"direct-model:{uuid4().hex}",
            provider_id=self.provider_id,
        )
        with activate_provider_turn(turn):
            return await self._configured_model()

    def _configured_model_sync(self) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._model_for_invocation())
        raise RuntimeError("ProviderChatModel must be invoked asynchronously")

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> "ProviderChatModel":
        return self.model_copy(
            update={
                "bound_tools": tuple(tools),
                "bound_tool_choice": tool_choice,
                "bound_settings": dict(kwargs),
            }
        )

    def with_structured_output(
        self, schema: dict[str, Any] | type, *, include_raw: bool = False, **kwargs: Any
    ) -> "ProviderChatModel":
        return self.model_copy(
            update={
                "structured_schema": schema,
                "structured_include_raw": include_raw,
                "structured_settings": dict(kwargs),
            }
        )

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return self._configured_model_sync().invoke(input, config=config, **kwargs)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return await (await self._model_for_invocation()).ainvoke(input, config=config, **kwargs)

    def stream(self, input: Any, config: Any = None, **kwargs: Any) -> Iterator[Any]:
        yield from self._configured_model_sync().stream(input, config=config, **kwargs)

    async def astream(self, input: Any, config: Any = None, **kwargs: Any) -> AsyncIterator[Any]:
        async for chunk in (await self._model_for_invocation()).astream(
            input, config=config, **kwargs
        ):
            yield chunk

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        target, bound_kwargs = _generation_source(self._configured_model_sync())
        return target._generate(
            messages,
            stop=stop,
            run_manager=run_manager,
            **{**_generation_kwargs(bound_kwargs, kwargs), "stream": False},
        )

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        target, bound_kwargs = _generation_source(await self._model_for_invocation())
        return await target._agenerate(
            messages,
            stop=stop,
            run_manager=run_manager,
            **{**_generation_kwargs(bound_kwargs, kwargs), "stream": False},
        )

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        target, bound_kwargs = _generation_source(self._configured_model_sync())
        yield from target._stream(
            messages,
            stop=stop,
            run_manager=run_manager,
            **_generation_kwargs(bound_kwargs, kwargs),
        )

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        target, bound_kwargs = _generation_source(await self._model_for_invocation())
        async for chunk in target._astream(
            messages,
            stop=stop,
            run_manager=run_manager,
            **_generation_kwargs(bound_kwargs, kwargs),
        ):
            yield chunk


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
        description=str(getattr(system_tool, "description", "Progena system tool")),
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
    """Create the runtime conversation graph backed by Progena's agent setup."""
    from src.progena.orchestration.graphs.progena_graph import create_conversation_graph

    from ..config import get_config

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
