"""Preserve call attribution on model messages saved by LangGraph checkpoints."""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr


class CheckpointChatOpenAI(ChatOpenAI):
    """Attach non-secret attribution once per result, including streaming results.

    A profile is model configuration; call time belongs to each invocation, not
    model construction. Streaming metadata is attached to the first chunk only:
    LangChain concatenates strings when merging repeated chunk metadata.
    """

    _insights_profile_name: str | None = PrivateAttr(default=None)

    def set_usage_profile(self, name: str | None) -> None:
        self._insights_profile_name = name

    def _usage_context(self) -> dict[str, Any]:
        return {
            "profile_name": self._insights_profile_name,
            "provider": "azure_openai",
            "deployment": self.model_name,
            "occurred_at": datetime.now(UTC).isoformat(),
        }

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        context = self._usage_context()
        result = super()._generate(*args, **kwargs)
        for generation in result.generations:
            generation.message.response_metadata["insights"] = context
        return result

    async def _agenerate(self, *args: Any, **kwargs: Any) -> ChatResult:
        context = self._usage_context()
        result = await super()._agenerate(*args, **kwargs)
        for generation in result.generations:
            generation.message.response_metadata["insights"] = context
        return result

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        context = self._usage_context()
        first = True
        for chunk in super()._stream(*args, **kwargs):
            if first:
                chunk.message.response_metadata["insights"] = context
                first = False
            yield chunk

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        context = self._usage_context()
        first = True
        async for chunk in super()._astream(*args, **kwargs):
            if first:
                chunk.message.response_metadata["insights"] = context
                first = False
            yield chunk
