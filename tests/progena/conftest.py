"""Shared fixtures for progena unit tests."""

from typing import Any

import pytest


class _FakeRuntimeStore:
    """In-memory runtime store for tests.

    Mirrors the LangGraphRuntimeStore interface without persistence.
    """

    def __init__(self) -> None:
        self._documents: dict[str, Any] = {}

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def get_document(self, name: str, default: Any = None) -> Any:
        if name not in self._documents:
            self._documents[name] = default
        return self._documents[name]

    async def peek_document(self, name: str) -> Any | None:
        return self._documents.get(name)

    async def save_document(self, name: str, payload: Any) -> None:
        self._documents[name] = payload

    async def delete_document(self, name: str) -> None:
        self._documents.pop(name, None)

    async def list_documents(self, key_prefix: str) -> list[tuple[str, Any]]:
        return [(k, v) for k, v in self._documents.items() if k.startswith(key_prefix)]


@pytest.fixture(autouse=True)
def patch_checkpointer(monkeypatch):
    """Replace _build_checkpointer with an in-memory saver for all unit tests.

    Unit tests do not need SQLite persistence and must not attempt to create
    filesystem paths like /data/state that are inaccessible in CI.
    """
    from langgraph.checkpoint.memory import MemorySaver

    monkeypatch.setattr(
        "src.progena.agent.factory._build_checkpointer",
        lambda db_path: MemorySaver(),
    )


@pytest.fixture
def model_provider(monkeypatch):
    """Provide an isolated connection for graph tests with fake model graphs."""
    from types import SimpleNamespace

    import src.progena.llm.provider_context as provider_runtime

    repository = SimpleNamespace(
        resolve=lambda provider_id=None, model_override=None: {
            "provider_id": provider_id or "test-provider",
            "provider_kind": "foundry",
            "base_url": "https://example.openai.azure.com/openai/v1",
            "api_key": "test-key",
            "model_default": model_override or "test-model",
            "revision": 1,
        }
    )
    monkeypatch.setattr(provider_runtime, "get_provider_repository", lambda: repository)
