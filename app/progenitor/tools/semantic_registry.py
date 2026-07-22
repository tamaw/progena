"""
Semantic Registry - Tool discovery with semantic search.

FEAT-001: Add semantic similarity search for capabilities.

Uses embeddings to find tools/agents by natural language queries
instead of exact string matching.

Usage:
    registry = SemanticRegistry(registry_path)
    await registry.load()

    results = registry.find_by_capability_semantic("handle file uploads")
"""

import importlib
import importlib.util
import logging
from typing import Any

from pydantic import BaseModel, Field

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class SemanticRegistryArgs(BaseModel):
    query: str = Field(
        default="",
        description="Optional semantic capability query to run against the registry.",
    )


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


# Optional dependency surface: keep static analyzers happy by avoiding hard imports.
SentenceTransformer: Any = None
np: Any = None
SEMANTIC_AVAILABLE = _module_available("numpy") and _module_available("sentence_transformers")
_semantic_import_error_logged = False


def _ensure_semantic_imports() -> bool:
    """Import optional deps on-demand; returns True if semantic features are usable."""
    global SentenceTransformer, np, SEMANTIC_AVAILABLE, _semantic_import_error_logged

    if SentenceTransformer is not None and np is not None:
        return True
    if not SEMANTIC_AVAILABLE:
        return False

    try:
        np = importlib.import_module("numpy")
        st_mod = importlib.import_module("sentence_transformers")
        SentenceTransformer = getattr(st_mod, "SentenceTransformer", None)
        if SentenceTransformer is None:
            raise ImportError("sentence_transformers.SentenceTransformer not found")
        return True
    except Exception as e:
        SEMANTIC_AVAILABLE = False
        if not _semantic_import_error_logged:
            logger.info(
                "sentence-transformers not installed/available. "
                "Semantic search disabled. Install with: pip install sentence-transformers. (%s)",
                e,
            )
            _semantic_import_error_logged = True
        return False


def cosine_similarity(a: Any, b: Any) -> float:
    """Compute cosine similarity between two vectors."""
    if not _ensure_semantic_imports():
        return 0.0
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


class SemanticRegistry:
    """Tool and Agent Registry with semantic search capabilities.

    Extends basic registry with:
    - Semantic similarity search for capabilities
    - Embedding-based tool discovery
    - Fuzzy matching for natural language queries
    """

    DEFAULT_MODEL = "all-MiniLM-L6-v2"  # Fast, lightweight model

    def __init__(
        self,
        registry_data: dict[str, Any] | None = None,
        model_name: str | None = None,
    ):
        """Initialize semantic registry.

        Args:
            registry_data: Registry dict with ``agents`` and ``tools`` lists.
            model_name: Sentence transformer model name.
        """
        self._registry: dict[str, Any] = (
            registry_data if registry_data is not None else {"agents": [], "tools": []}
        )
        self._embeddings_cache: dict[str, Any] = {}  # capability -> embedding
        self._model_name = model_name or self.DEFAULT_MODEL
        self._encoder = None

    def _load_encoder(self) -> None:
        """Lazily load the sentence transformer model."""
        if (
            self._encoder is not None
            or not _ensure_semantic_imports()
            or SentenceTransformer is None
        ):
            return

        try:
            logger.info("Loading sentence transformer model: %s", self._model_name)
            self._encoder = SentenceTransformer(self._model_name)
            logger.info("Sentence transformer loaded successfully")
        except Exception as e:
            logger.warning("Failed to load sentence transformer: %s", e)
            self._encoder = None

    async def load(self) -> None:
        """Build embeddings for the current registry data."""
        if _ensure_semantic_imports():
            await self._build_embeddings()

    async def _build_embeddings(self) -> None:
        """Build embeddings for all registered capabilities."""
        self._load_encoder()
        if self._encoder is None:
            return

        capabilities = self.get_all_capabilities()
        if not capabilities:
            return

        logger.info("Building embeddings for %d capabilities", len(capabilities))

        try:
            embeddings = self._encoder.encode(capabilities)
            for cap, emb in zip(capabilities, embeddings, strict=False):
                self._embeddings_cache[cap] = emb
            logger.info("Built embeddings successfully")
        except Exception as e:
            logger.warning("Failed to build embeddings: %s", e)

    def get_all_capabilities(self) -> list[str]:
        """Get all registered capabilities."""
        capabilities = []

        for agent in self._registry.get("agents", []):
            capabilities.extend(agent.get("capabilities", []))

        for tool in self._registry.get("tools", []):
            capabilities.extend(tool.get("capabilities", []))

        return list(set(capabilities))

    def find_by_capability_semantic(
        self,
        query: str,
        threshold: float = 0.5,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Find tools/agents by semantic similarity.

        Args:
            query: Natural language capability query.
            threshold: Minimum similarity score (0-1).
            limit: Maximum results to return.

        Returns:
            List of matching tools/agents with similarity scores.
        """
        self._load_encoder()

        if self._encoder is None or not self._embeddings_cache:
            # Fall back to exact matching
            logger.debug("Semantic search unavailable, falling back to exact match")
            return self._fallback_search(query)

        # Encode query
        try:
            query_embedding = self._encoder.encode(query)
        except Exception as e:
            logger.warning("Failed to encode query: %s", e)
            return self._fallback_search(query)

        # Find similar capabilities
        matches = []
        for capability, cap_embedding in self._embeddings_cache.items():
            similarity = cosine_similarity(query_embedding, cap_embedding)

            if similarity >= threshold:
                matches.append((capability, similarity))

        # Sort by similarity
        matches.sort(key=lambda x: x[1], reverse=True)

        # Map to tools/agents
        results = []
        seen = set()

        for capability, similarity in matches[: limit * 2]:  # Get more to dedupe
            # Find tool or agent with this capability
            for agent in self._registry.get("agents", []):
                if capability in agent.get("capabilities", []) and agent.get("name") not in seen:
                    results.append(
                        {
                            **agent,
                            "matched_capability": capability,
                            "similarity": similarity,
                        }
                    )
                    seen.add(agent.get("name"))

            for tool in self._registry.get("tools", []):
                if capability in tool.get("capabilities", []) and tool.get("name") not in seen:
                    results.append(
                        {
                            **tool,
                            "matched_capability": capability,
                            "similarity": similarity,
                        }
                    )
                    seen.add(tool.get("name"))

            if len(results) >= limit:
                break

        return results[:limit]

    def _fallback_search(self, query: str) -> list[dict[str, Any]]:
        """Simple substring-based fallback search.

        Args:
            query: Search query.

        Returns:
            Matching tools/agents.
        """
        query_lower = query.lower()
        results = []

        for agent in self._registry.get("agents", []):
            for cap in agent.get("capabilities", []):
                if query_lower in cap.lower() or cap.lower() in query_lower:
                    results.append({**agent, "matched_capability": cap})
                    break

        for tool in self._registry.get("tools", []):
            for cap in tool.get("capabilities", []):
                if query_lower in cap.lower() or cap.lower() in query_lower:
                    results.append({**tool, "matched_capability": cap})
                    break

        return results


class SemanticRegistryTool(BaseSystemTool):
    """Expose semantic tool discovery status and optional query."""

    @property
    def name(self) -> str:
        return "semantic_registry"

    @property
    def description(self) -> str:
        return "Semantic discovery over registry capabilities (if sentence-transformers installed)"

    @property
    def args_model(self) -> type[BaseModel]:
        return SemanticRegistryArgs

    async def execute(self, context: ToolContext) -> ToolResult:
        tool_registry = context.tool_registry
        if tool_registry is None:
            return ToolResult.fail("Tool registry is not available")

        # Read the live registry data directly (no file path needed)
        raw = await tool_registry.load() if hasattr(tool_registry, "load") else None
        if raw is None and hasattr(tool_registry, "_registry"):
            registry_data = dict(tool_registry._registry)
        elif isinstance(raw, dict):
            registry_data = raw
        else:
            registry_data = {"agents": [], "tools": []}

        semantic = SemanticRegistry(registry_data)
        await semantic.load()

        query = str((context.tool_args or {}).get("query") or "").strip()
        if not query:
            # Legacy fallback: "semantic_registry: <query>" in user_input.
            raw = (context.user_input or "").strip()
            if ":" in raw:
                _prefix, tail = raw.split(":", 1)
                query = tail.strip()

        payload: dict[str, Any] = {
            "semantic_available": SEMANTIC_AVAILABLE,
            "model": semantic._model_name,
            "capability_count": len(semantic.get_all_capabilities()),
        }

        if query:
            payload["query"] = query
            payload["results"] = semantic.find_by_capability_semantic(query)

        return ToolResult.ok(payload)
