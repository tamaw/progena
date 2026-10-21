"""Foundry-compatible embeddings backed by persisted provider connections."""

from __future__ import annotations

from typing import Protocol

from openai import AsyncOpenAI

from ..config import LLMConfig
from ..provider_runtime import provider_config_for_call
from .indexer_config import EmbeddingTarget

EMBED_BATCH_SIZE = 64


class Embedder(Protocol):
    """Turns texts into vectors of the index's dimension, in input order."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def embeddings_base_url(responses_base_url: str) -> str:
    """Derive the OpenAI-compatible root from a Foundry Responses endpoint.

    The configured URL targets the Responses API (``…/openai/v1/responses?…``);
    embeddings live beside it at ``…/openai/v1/embeddings``.
    """
    url = responses_base_url.split("?", 1)[0].rstrip("/")
    if url.endswith("/responses"):
        url = url[: -len("/responses")]
    return url


class AzureEmbedder:
    """Embeds through an Azure OpenAI embeddings deployment."""

    def __init__(self, target: EmbeddingTarget, *, base_url: str, api_key: str) -> None:
        self._target = target
        self._client = AsyncOpenAI(
            base_url=embeddings_base_url(base_url),
            api_key=api_key or "not-needed",
            default_headers={"api-key": api_key},
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            response = await self._client.embeddings.create(
                model=self._target.deployment,
                input=texts[start : start + EMBED_BATCH_SIZE],
                dimensions=self._target.dimensions,
            )
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors


async def embedder_for(target: EmbeddingTarget) -> Embedder:
    """Build an embeddings client from the profile's persisted Foundry provider."""
    if not target.provider_id:
        raise ValueError("Embedding profile must select a persisted Foundry provider")
    config = await provider_config_for_call(
        LLMConfig(reasoning_enabled=False),
        provider_id=target.provider_id,
    )
    if config.provider_kind != "foundry":
        raise ValueError("Embeddings require a Foundry provider")
    return AzureEmbedder(target, base_url=config.base_url, api_key=config.api_key or "")
