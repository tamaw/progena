"""Tests for active DeepAgent subagent definitions."""

from app.progenitor.agent.async_subagents import (
    create_hello_world_async_subagent,
    create_progenitor_async_subagents,
)


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
