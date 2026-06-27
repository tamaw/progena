"""Deterministic, dependency-free text embeddings for the knowledge store.

Real deployments would call an embeddings API (e.g. Azure OpenAI). For local
development and tests we need embeddings that are:

- deterministic, so the same text always maps to the same vector and the
  ingestion path (backend) and the query path (runtime tool) agree;
- dependency-free, so no embeddings deployment or API key is required;
- cosine-friendly, so token/phrase overlap between a query and a stored
  passage produces a high similarity score.

We use signed feature hashing over unigrams and bigrams into a fixed-size
vector, then L2-normalise. With normalised vectors, cosine similarity reduces
to a dot product, and shared tokens push the vectors together — good enough to
recall a stored fact phrased in similar words.
"""

from __future__ import annotations

import hashlib
import math
import re

EMBED_DIM = 256
EMBED_MODEL_NAME = "progenitor-hash-256"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def embed_text(text: str) -> list[float]:
    """Embed ``text`` into a unit-length ``EMBED_DIM`` vector."""
    vec = [0.0] * EMBED_DIM
    tokens = _tokens(text or "")
    if not tokens:
        return vec

    grams = list(tokens)
    for first, second in zip(tokens, tokens[1:], strict=False):
        grams.append(f"{first}_{second}")

    for gram in grams:
        # Feature hashing only — not security-sensitive.
        digest = hashlib.md5(gram.encode("utf-8"), usedforsecurity=False).digest()
        index = int.from_bytes(digest[:4], "big") % EMBED_DIM
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[index] += sign

    norm = math.sqrt(sum(value * value for value in vec))
    if norm > 0.0:
        vec = [value / norm for value in vec]
    return vec


def to_pgvector(vector: list[float]) -> str:
    """Render a Python vector as a pgvector literal: ``[v1,v2,...]``."""
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"
