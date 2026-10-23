"""Preserve call attribution on model messages saved by LangGraph checkpoints."""

from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr

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


class CheckpointChatOpenAI(ChatOpenAI):
    """Attach non-secret attribution once per result, including streaming results.

    A profile is model configuration; call time belongs to each invocation, not
    model construction. Streaming metadata is attached to the first chunk only:
    LangChain concatenates strings when merging repeated chunk metadata.
    """

    _insights_profile_name: str | None = PrivateAttr(default=None)
    _provider_connection: dict[str, str] | None = PrivateAttr(default=None)

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

    def _usage_context(self) -> dict[str, Any]:
        return {
            "profile_name": self._insights_profile_name,
            "provider": (
                self._provider_connection.get("kind")
                if self._provider_connection is not None
                else "azure_openai"
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
    ) -> dict:
        messages, original_messages = self._sanitize_messages_for_connection(input_)
        if "previous_response_id" in kwargs:
            # A caller-provided response ID has no checkpoint provenance to verify.
            kwargs = {key: value for key, value in kwargs.items() if key != "previous_response_id"}
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        if "previous_response_id" in payload and not self._last_response_id_can_replay(
            original_messages
        ):
            payload.pop("previous_response_id")
        return payload

    def _attach_usage_context(self, message: BaseMessage, context: dict[str, Any]) -> None:
        message.response_metadata["insights"] = context
        if self._provider_connection is not None:
            message.response_metadata[_PROVIDER_METADATA_KEY] = dict(self._provider_connection)

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        context = self._usage_context()
        result = super()._generate(*args, **kwargs)
        for generation in result.generations:
            self._attach_usage_context(generation.message, context)
        return result

    async def _agenerate(self, *args: Any, **kwargs: Any) -> ChatResult:
        context = self._usage_context()
        result = await super()._agenerate(*args, **kwargs)
        for generation in result.generations:
            self._attach_usage_context(generation.message, context)
        return result

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        context = self._usage_context()
        first = True
        for chunk in super()._stream(*args, **kwargs):
            if first:
                self._attach_usage_context(chunk.message, context)
                first = False
            yield chunk

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        context = self._usage_context()
        first = True
        async for chunk in super()._astream(*args, **kwargs):
            if first:
                self._attach_usage_context(chunk.message, context)
                first = False
            yield chunk
