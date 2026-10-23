"""Runtime memory requires a durable content-document owner."""

import pytest

from src.progena.agent.factory import create_progena_agent
from tests.progena.conftest import _FakeRuntimeStore


class FakeContentStateManager:
    """Minimal async state_manager stub for get/save/list content documents."""

    def __init__(self, *, prompt: str = "GENESIS") -> None:
        self._runtime_store = _FakeRuntimeStore()
        self._docs: dict[str, dict[str, str]] = {
            "instruction": {
                "progena/genesis.md": f"---\ntitle: Genesis\ndescription: Test\n---\n{prompt}"
            },
            "skill": {},
            "agent": {},
            "memory": {},
        }

    async def get_content_document(self, kind: str, relative_path: str) -> str | None:
        return self._docs[kind].get(relative_path)

    async def save_content_document(self, kind: str, relative_path: str, content: str) -> None:
        self._docs[kind][relative_path] = content

    async def delete_content_document(self, kind: str, relative_path: str) -> None:
        self._docs[kind].pop(relative_path, None)

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        return list(self._docs[kind].items())

    async def list_routine_graphs(self) -> list[dict]:
        return []

    async def get_preferences_config(self) -> dict:
        return {}


async def test_factory_requires_state_manager():
    with pytest.raises(ValueError):
        await create_progena_agent()
