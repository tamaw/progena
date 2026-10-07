"""Tests for active DeepAgent subagent definitions."""

from unittest.mock import MagicMock, patch

import pytest

from app.progenitor.agent.async_subagents import (
    create_hello_world_async_subagent,
    create_progenitor_async_subagents,
)
from app.progenitor.agent.object_agent_registry import LiveSubagentMiddleware
from tests.progenitor.conftest import _FakeRuntimeStore


class TestCreateProgenitorAsyncSubagents:
    def test_creates_async_subagents(self):
        result = create_progenitor_async_subagents("http://127.0.0.1:9501")
        assert [s["name"] for s in result] == ["hello-world-async"]
        assert result[0]["graph_id"] == "hello-world-async"
        assert result[0]["url"] == "http://127.0.0.1:9501/agent-protocol"


class TestHelloWorldAsyncSubagent:
    def test_returns_async_subagent(self):
        result = create_hello_world_async_subagent("http://127.0.0.1:9501")
        assert isinstance(result, dict)
        assert result["name"] == "hello-world-async"
        assert result["graph_id"] == "hello-world-async"


class TestSubagentWiring:
    @pytest.mark.asyncio
    async def test_factory_passes_subagents(self):
        with (
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.agent.create_progenitor_async_subagents") as mock_subagents,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_create.return_value = MagicMock()
            mock_subagents.return_value = [{"name": "researcher"}]
            mock_backend.return_value = MagicMock()
            with patch("app.progenitor.agent.agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()
                from app.progenitor.agent.agent import create_progenitor_agent

                class _FakeStateManager:
                    def __init__(self):
                        self._runtime_store = _FakeRuntimeStore()
                        self._docs = {
                            "prompt": {"progenitor/genesis.md": "Genesis"},
                            "skill": {},
                            "memory": {},
                            "agent": {},
                        }

                    async def get_content_document(self, kind, relative_path):
                        return self._docs[kind].get(relative_path)

                    async def save_content_document(self, kind, relative_path, content):
                        self._docs[kind][relative_path] = content

                    async def delete_content_document(self, kind, relative_path):
                        self._docs[kind].pop(relative_path, None)

                    async def list_content_documents(self, kind):
                        return list(self._docs[kind].items())

                    async def list_routine_graphs(self):
                        return []

                state_manager = _FakeStateManager()
                await create_progenitor_agent(state_manager=state_manager)
                call_kwargs = mock_create.call_args.kwargs
                assert "subagents" in call_kwargs
                assert [agent["name"] for agent in call_kwargs["subagents"]] == [
                    "researcher",
                ]
                live = next(
                    m for m in call_kwargs["middleware"] if isinstance(m, LiveSubagentMiddleware)
                )
                assert [spec["name"] for spec in live._registry.descriptors] == ["repo-manager"]
                assert "native/repo-manager/SYSTEM.md" not in state_manager._docs.get("agent", {})
                mock_subagents.assert_called_once()
