"""Embedding clients for the knowledge search index.

Embeddings come from the Azure OpenAI deployment named by the indexer settings
(``/config/indexer.json`` → a ``/config/profile.json`` entry). The endpoint and key
are the ones the chat models already use; only the deployment differs.
"""

from __future__ import annotations

from typing import Protocol

from openai import AsyncOpenAI

from .indexer_config import EmbeddingTarget

EMBED_BATCH_SIZE = 64


class Embedder(Protocol):
    """Turns texts into vectors of the index's dimension, in input order."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def embeddings_base_url(responses_base_url: str) -> str:
    """Derive the OpenAI-compatible root from ``AZURE_OPENAI_BASE_URL``.

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


def embedder_for(target: EmbeddingTarget) -> Embedder:
    """Build the Azure embedder for ``target`` from the process LLM settings."""
    from ..config import LLMConfig

    config = LLMConfig.from_env()
    if not config.base_url:
        raise ValueError("AZURE_OPENAI_BASE_URL is not configured")
    return AzureEmbedder(target, base_url=config.base_url, api_key=config.api_key or "")
