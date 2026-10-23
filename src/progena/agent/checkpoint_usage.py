"""Preserve provider attribution and enforce ChatGPT-plan Responses semantics."""

from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.chat_models import agenerate_from_stream, generate_from_stream
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import _convert_responses_chunk_to_generation_chunk
from pydantic import PrivateAttr

from src.progena.settings.providers import (
    refresh_chatgpt_access_token_after_rejection,
    refresh_chatgpt_access_token_after_rejection_sync,
)

_PROVIDER_METADATA_KEY = "progena_provider"
_OPAQUE_RESPONSE_ITEM_TYPES = frozenset({"reasoning", "compaction"})


def _normalized_base_url(value: object) -> str | None:
    """Return a stable, non-secret provider endpoint identity."""
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "")
    )


def _connection_matches(source: dict[str, str], target: dict[str, str] | None) -> bool:
    """Whether an opaque Responses item may be replayed to this connection."""
    if target is None:
        return False
    source_id = source.get("id")
    target_id = target.get("id")
    if source_id and target_id:
        return source_id == target_id
    return (
        bool(source.get("kind") and source.get("base_url"))
        and source.get("kind") == target.get("kind")
        and source.get("base_url") == target.get("base_url")
    )


def _message_connection(message: BaseMessage) -> dict[str, str] | None:
    metadata = message.response_metadata
    raw = metadata.get(_PROVIDER_METADATA_KEY)
    if not isinstance(raw, dict):
        return None
    connection = {
        key: value
        for key in ("id", "kind", "base_url")
        if isinstance((value := raw.get(key)), str) and value
    }
    return connection or None


_CHATGPT_REQUEST_FIELDS = frozenset(
    {
        "include",
        "input",
        "instructions",
        "model",
        "reasoning",
        "store",
        "stream",
        "text",
        "tool_choice",
        "tools",
    }
)
_CHATGPT_TOOL_NAMESPACE = "progena"
_CHATGPT_UNSUPPORTED_TOOL_TYPES = frozenset(
    {
        "code_interpreter",
        "computer",
        "computer_use_preview",
        "file_search",
        "image_generation",
        "mcp",
        "programmatic_tool_calling",
        "tool_search",
    }
)


class ChatGPTResponseStreamError(RuntimeError):
    """A terminal ChatGPT Responses stream event that is not completion."""

    def __init__(
        self,
        event_type: str,
        *,
        code: str | None = None,
        param: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.event_type = event_type
        self.code = code
        self.param = param
        self.detail = detail
        message = f"ChatGPT response stream ended with {event_type}"
        if code:
            message = f"{message}: {code}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)


def _event_value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _stream_error(event: Any, event_type: str) -> ChatGPTResponseStreamError:
    response = _event_value(event, "response")
    error = _event_value(response, "error") or _event_value(event, "error")
    incomplete = _event_value(response, "incomplete_details")
    return ChatGPTResponseStreamError(
        event_type,
        code=_event_value(error, "code"),
        param=_event_value(error, "param"),
        detail=(
            _event_value(error, "message")
            or _event_value(event, "message")
            or _event_value(incomplete, "reason")
        ),
    )


def _is_unauthorized_error(error: BaseException) -> bool:
    """Recognize the OpenAI SDK and HTTPX representations of HTTP 401."""
    if getattr(error, "status_code", None) == 401:
        return True
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) == 401


class CheckpointChatOpenAI(ChatOpenAI):
    """Attach checkpoint metadata and support stateless ChatGPT-plan Responses calls.

    The ChatGPT direct route requires SSE for every request, including callers
    that LangChain normally treats as non-streaming. Its terminal stream events
    are handled here because LangChain's generic Responses adapter treats
    ``response.incomplete`` as a final result.
    """

    _insights_profile_name: str | None = PrivateAttr(default=None)
    _provider_connection: dict[str, str] | None = PrivateAttr(default=None)
    _provider_kind: str = PrivateAttr(default="foundry")
    _chatgpt_provider_id: str | None = PrivateAttr(default=None)
    _chatgpt_access_token: str | None = PrivateAttr(default=None)

    def set_usage_profile(self, name: str | None) -> None:
        self._insights_profile_name = name

    def set_provider_connection(
        self,
        *,
        provider_id: str | None,
        provider_kind: str,
        base_url: str,
    ) -> None:
        """Record the non-secret connection that may replay opaque Responses items."""
        connection = {
            key: value
            for key, value in {
                "id": provider_id,
                "kind": provider_kind,
                "base_url": _normalized_base_url(base_url),
            }.items()
            if isinstance(value, str) and value
        }
        self._provider_connection = connection or None

    def set_provider_kind(self, provider_kind: str) -> None:
        self._provider_kind = provider_kind

    def set_chatgpt_provider(self, provider_id: str, access_token: str) -> None:
        """Attach the persisted registration needed to recover a rejected token."""
        self._chatgpt_provider_id = provider_id
        self._chatgpt_access_token = access_token

    def _replace_chatgpt_clients(self, access_token: str) -> None:
        """Swap both SDK clients before retrying an unauthorized request."""
        self._chatgpt_access_token = access_token
        if self.root_client is not None:
            self.root_client = self.root_client.with_options(api_key=access_token)
            self.client = self.root_client.chat.completions
        if self.root_async_client is not None:
            self.root_async_client = self.root_async_client.with_options(api_key=access_token)
            self.async_client = self.root_async_client.chat.completions

    def _refresh_chatgpt_after_unauthorized(self) -> bool:
        provider_id = self._chatgpt_provider_id
        rejected_token = self._chatgpt_access_token
        if not provider_id or not rejected_token:
            return False
        self._replace_chatgpt_clients(
            refresh_chatgpt_access_token_after_rejection_sync(provider_id, rejected_token)
        )
        return True

    async def _arefresh_chatgpt_after_unauthorized(self) -> bool:
        provider_id = self._chatgpt_provider_id
        rejected_token = self._chatgpt_access_token
        if not provider_id or not rejected_token:
            return False
        self._replace_chatgpt_clients(
            await refresh_chatgpt_access_token_after_rejection(provider_id, rejected_token)
        )
        return True

    @property
    def _is_chatgpt_provider(self) -> bool:
        return self._provider_kind == "chatgpt"

    def _usage_context(self) -> dict[str, Any]:
        return {
            "profile_name": self._insights_profile_name,
            "provider": (
                self._provider_connection.get("kind")
                if self._provider_connection is not None
                else ("chatgpt" if self._is_chatgpt_provider else "azure_openai")
            ),
            "deployment": self.model_name,
            "occurred_at": datetime.now(UTC).isoformat(),
        }

    def _message_can_replay_opaque_items(self, message: BaseMessage) -> bool:
        source = _message_connection(message)
        return source is not None and _connection_matches(source, self._provider_connection)

    def _sanitize_messages_for_connection(
        self, input_: LanguageModelInput
    ) -> tuple[list[BaseMessage], list[BaseMessage]]:
        """Drop opaque history items when their checkpoint attribution cannot match."""
        messages = self._convert_input(input_).to_messages()
        sanitized: list[BaseMessage] = []
        for message in messages:
            content = message.content
            can_replay = self._message_can_replay_opaque_items(message)
            has_opaque_content = isinstance(content, list) and any(
                isinstance(item, dict) and item.get("type") in _OPAQUE_RESPONSE_ITEM_TYPES
                for item in content
            )
            drop_opaque_content = has_opaque_content and not can_replay
            response_id = message.response_metadata.get("id")
            drop_response_id = (
                self.use_previous_response_id
                and isinstance(response_id, str)
                and response_id.startswith("resp_")
                and not can_replay
            )
            if not drop_opaque_content and not drop_response_id:
                sanitized.append(message)
                continue
            replayable = message.model_copy(deep=True)
            if drop_opaque_content:
                replayable.content = [
                    item
                    for item in content
                    if not (
                        isinstance(item, dict) and item.get("type") in _OPAQUE_RESPONSE_ITEM_TYPES
                    )
                ]
            if drop_response_id:
                replayable.response_metadata = dict(replayable.response_metadata)
                replayable.response_metadata.pop("id", None)
            sanitized.append(replayable)
        return sanitized, messages

    def _last_response_id_can_replay(self, messages: Sequence[BaseMessage]) -> bool:
        """Only forward a server-side response reference from the same connection."""
        for message in reversed(messages):
            response_id = message.response_metadata.get("id")
            if isinstance(response_id, str) and response_id.startswith("resp_"):
                return self._message_can_replay_opaque_items(message)
        return False

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        messages, original_messages = self._sanitize_messages_for_connection(input_)
        if "previous_response_id" in kwargs:
            # A caller-provided response ID has no checkpoint provenance to verify.
            kwargs = {key: value for key, value in kwargs.items() if key != "previous_response_id"}
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        if "previous_response_id" in payload and not self._last_response_id_can_replay(
            original_messages
        ):
            payload.pop("previous_response_id")
        if not self._is_chatgpt_provider:
            return payload

        extra_body = payload.get("extra_body")
        if isinstance(extra_body, dict) and isinstance(extra_body.get("reasoning"), dict):
            payload["reasoning"] = dict(extra_body["reasoning"])
        payload = {
            field: value for field, value in payload.items() if field in _CHATGPT_REQUEST_FIELDS
        }
        payload["store"] = False
        payload["stream"] = True

        include = list(payload.get("include") or [])
        if "reasoning.encrypted_content" not in include:
            include.append("reasoning.encrypted_content")
        payload["include"] = include

        input_items = payload.get("input")
        if isinstance(input_items, list):
            payload["input"] = [
                {**item, "role": "developer"}
                if isinstance(item, dict) and item.get("role") == "system"
                else item
                for item in input_items
            ]

        tools = payload.get("tools")
        if isinstance(tools, list):
            payload["tools"] = self._namespace_chatgpt_tools(tools)
        return payload

    def _attach_usage_context(self, message: BaseMessage, context: dict[str, Any]) -> None:
        message.response_metadata["insights"] = context
        if self._provider_connection is not None:
            message.response_metadata[_PROVIDER_METADATA_KEY] = dict(self._provider_connection)

    @staticmethod
    def _namespace_chatgpt_tools(tools: list[Any]) -> list[dict[str, Any]]:
        """Group locally executed functions/custom tools for the direct route."""
        local_tools: list[dict[str, Any]] = []
        namespaced_tools: list[dict[str, Any]] = []
        for raw_tool in tools:
            if not isinstance(raw_tool, dict):
                raise TypeError("ChatGPT Responses tools must be dictionaries")
            tool = dict(raw_tool)
            tool_type = tool.get("type")
            if tool_type in {"function", "custom"}:
                # LangChain normally flattens function tools for Responses;
                # tolerate the chat-completions shape too.
                if tool_type == "function" and isinstance(tool.get("function"), dict):
                    tool = {"type": "function", **tool["function"]}
                local_tools.append(tool)
            elif tool_type in {"web_search", "web_search_preview"}:
                namespaced_tools.append(tool)
            elif tool_type in _CHATGPT_UNSUPPORTED_TOOL_TYPES:
                raise ValueError(f"ChatGPT plan usage does not support tool type {tool_type!r}")
            else:
                raise ValueError(
                    "ChatGPT plan usage accepts only function/custom tools and web search"
                )

        if local_tools:
            namespaced_tools.append(
                {
                    "type": "namespace",
                    "name": _CHATGPT_TOOL_NAMESPACE,
                    "description": "Progena local system and MCP tools.",
                    "tools": local_tools,
                }
            )
        return namespaced_tools

    @staticmethod
    def _preserve_chatgpt_tool_namespace(
        event: Any,
        generation_chunk: ChatGenerationChunk,
    ) -> None:
        """Carry the direct-route namespace into LangChain's replayable content.

        The Responses wire shape keeps ``name`` local to its namespace and
        supplies the namespace separately. OpenAI 2.24 retains that unknown
        field on its model, but langchain-openai 1.1.10 omits it while adapting
        a function-call event.
        """
        namespace = _event_value(_event_value(event, "item"), "namespace")
        if not isinstance(namespace, str):
            return
        content = generation_chunk.message.content
        if not isinstance(content, list):
            return
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"function_call", "custom_tool_call"}:
                item.setdefault("namespace", namespace)

    @staticmethod
    def _strip_streaming_reasoning_status(
        event: Any,
        generation_chunk: ChatGenerationChunk,
    ) -> None:
        """Defer a reasoning item's terminal status until ``item.done``.

        LangChain concatenates string fields when content blocks share an index.
        The added item therefore cannot retain ``in_progress`` when the done
        event later contributes ``completed``.
        """
        if (
            _event_value(event, "type") != "response.output_item.added"
            or _event_value(_event_value(event, "item"), "type") != "reasoning"
        ):
            return
        content = generation_chunk.message.content
        if not isinstance(content, list):
            return
        for item in content:
            if isinstance(item, dict) and item.get("type") == "reasoning":
                item.pop("status", None)

    @staticmethod
    def _finalized_chatgpt_reasoning_chunk(
        event: Any,
        index: int,
    ) -> ChatGenerationChunk | None:
        """Merge terminal reasoning fields into the streamed reasoning item."""
        if _event_value(event, "type") != "response.output_item.done":
            return None
        item = _event_value(event, "item")
        if _event_value(item, "type") != "reasoning":
            return None
        encrypted_content = _event_value(item, "encrypted_content")
        if not isinstance(encrypted_content, str):
            return None
        content: dict[str, Any] = {"index": index, "encrypted_content": encrypted_content}
        status = _event_value(item, "status")
        if isinstance(status, str):
            content["status"] = status
        return ChatGenerationChunk(message=AIMessageChunk(content=[content]))

    def _stream_responses(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        if not self._is_chatgpt_provider:
            yield from super()._stream_responses(
                messages,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            )
            return

        self._ensure_sync_client_available()
        kwargs["stream"] = True
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        original_schema_obj = kwargs.get("response_format")
        completed = False
        current_index = -1
        current_output_index = -1
        current_sub_index = -1
        has_reasoning = False

        emitted = False
        retried_unauthorized = False
        while True:
            try:
                with self.root_client.responses.create(**payload) as response:
                    for event in response:
                        event_type = str(_event_value(event, "type") or "")
                        if event_type in {"error", "response.failed", "response.incomplete"}:
                            raise _stream_error(event, event_type)
                        if event_type == "response.completed":
                            completed = True
                        finalized_reasoning = self._finalized_chatgpt_reasoning_chunk(
                            event,
                            current_index,
                        )
                        if finalized_reasoning is not None:
                            self._attach_usage_context(
                                finalized_reasoning.message,
                                self._usage_context(),
                            )
                            if run_manager:
                                run_manager.on_llm_new_token(
                                    finalized_reasoning.text,
                                    chunk=finalized_reasoning,
                                )
                            emitted = True
                            yield finalized_reasoning
                            continue
                        (
                            current_index,
                            current_output_index,
                            current_sub_index,
                            generation_chunk,
                        ) = _convert_responses_chunk_to_generation_chunk(
                            event,
                            current_index,
                            current_output_index,
                            current_sub_index,
                            schema=original_schema_obj,
                            metadata={},
                            has_reasoning=has_reasoning,
                            output_version=self.output_version,
                        )
                        if generation_chunk:
                            self._strip_streaming_reasoning_status(event, generation_chunk)
                            self._preserve_chatgpt_tool_namespace(event, generation_chunk)
                            if run_manager:
                                run_manager.on_llm_new_token(
                                    generation_chunk.text,
                                    chunk=generation_chunk,
                                )
                            if "reasoning" in generation_chunk.message.additional_kwargs:
                                has_reasoning = True
                            emitted = True
                            yield generation_chunk
                break
            except Exception as exc:
                if (
                    retried_unauthorized
                    or emitted
                    or not _is_unauthorized_error(exc)
                    or not self._refresh_chatgpt_after_unauthorized()
                ):
                    raise
                retried_unauthorized = True
        if not completed:
            raise ChatGPTResponseStreamError("stream.interrupted")

    async def _astream_responses(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if not self._is_chatgpt_provider:
            async for chunk in super()._astream_responses(
                messages,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            ):
                yield chunk
            return

        kwargs["stream"] = True
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        original_schema_obj = kwargs.get("response_format")
        completed = False
        current_index = -1
        current_output_index = -1
        current_sub_index = -1
        has_reasoning = False

        emitted = False
        retried_unauthorized = False
        while True:
            try:
                context_manager = await self.root_async_client.responses.create(**payload)
                async with context_manager as response:
                    async for event in response:
                        event_type = str(_event_value(event, "type") or "")
                        if event_type in {"error", "response.failed", "response.incomplete"}:
                            raise _stream_error(event, event_type)
                        if event_type == "response.completed":
                            completed = True
                        finalized_reasoning = self._finalized_chatgpt_reasoning_chunk(
                            event,
                            current_index,
                        )
                        if finalized_reasoning is not None:
                            self._attach_usage_context(
                                finalized_reasoning.message,
                                self._usage_context(),
                            )
                            if run_manager:
                                await run_manager.on_llm_new_token(
                                    finalized_reasoning.text,
                                    chunk=finalized_reasoning,
                                )
                            emitted = True
                            yield finalized_reasoning
                            continue
                        (
                            current_index,
                            current_output_index,
                            current_sub_index,
                            generation_chunk,
                        ) = _convert_responses_chunk_to_generation_chunk(
                            event,
                            current_index,
                            current_output_index,
                            current_sub_index,
                            schema=original_schema_obj,
                            metadata={},
                            has_reasoning=has_reasoning,
                            output_version=self.output_version,
                        )
                        if generation_chunk:
                            self._strip_streaming_reasoning_status(event, generation_chunk)
                            self._preserve_chatgpt_tool_namespace(event, generation_chunk)
                            if run_manager:
                                await run_manager.on_llm_new_token(
                                    generation_chunk.text,
                                    chunk=generation_chunk,
                                )
                            if "reasoning" in generation_chunk.message.additional_kwargs:
                                has_reasoning = True
                            emitted = True
                            yield generation_chunk
                break
            except Exception as exc:
                if (
                    retried_unauthorized
                    or emitted
                    or not _is_unauthorized_error(exc)
                    or not (await self._arefresh_chatgpt_after_unauthorized())
                ):
                    raise
                retried_unauthorized = True
        if not completed:
            raise ChatGPTResponseStreamError("stream.interrupted")

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        context = self._usage_context()
        if self._is_chatgpt_provider:
            result = generate_from_stream(self._stream_responses(*args, **kwargs))
        else:
            result = super()._generate(*args, **kwargs)
        for generation in result.generations:
            self._attach_usage_context(generation.message, context)
        return result

    async def _agenerate(self, *args: Any, **kwargs: Any) -> ChatResult:
        context = self._usage_context()
        if self._is_chatgpt_provider:
            result = await agenerate_from_stream(self._astream_responses(*args, **kwargs))
        else:
            result = await super()._agenerate(*args, **kwargs)
        for generation in result.generations:
            self._attach_usage_context(generation.message, context)
        return result

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        context = self._usage_context()
        first = True
        stream = (
            self._stream_responses(*args, **kwargs)
            if self._is_chatgpt_provider
            else super()._stream(*args, **kwargs)
        )
        for chunk in stream:
            if first:
                self._attach_usage_context(chunk.message, context)
                first = False
            yield chunk

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        context = self._usage_context()
        first = True
        stream = (
            self._astream_responses(*args, **kwargs)
            if self._is_chatgpt_provider
            else super()._astream(*args, **kwargs)
        )
        async for chunk in stream:
            if first:
                self._attach_usage_context(chunk.message, context)
                first = False
            yield chunk
