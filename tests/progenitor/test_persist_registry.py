"""Unit test: the heartbeat registry persist keeps the enriched agent card.

Progenitor is the source of truth for the live agent list, so the document it
writes to the Postgres store (read by the backend) must carry the full
``agent_card`` and live ``status`` — unlike the file-backed ``ToolRegistry.save()``,
which deliberately drops the card.
"""

from __future__ import annotations

from typing import Any

from app.progenitor.nodes.registry import _persist_registry


class _FakeRegistry:
    def __init__(self, agents: list[dict[str, Any]]) -> None:
        self._agents = agents

    def get_agents(self) -> list[dict[str, Any]]:
        return self._agents

    def get_tools(self) -> list[dict[str, Any]]:
        return []


class _FakeStateManager:
    def __init__(self) -> None:
        self.saved: dict[str, Any] | None = None

    async def save_registry(self, payload: dict[str, Any]) -> None:
        self.saved = payload


class _Ctx:
    def __init__(self, state_manager: Any, tool_registry: Any) -> None:
        self.state_manager = state_manager
        self.tool_registry = tool_registry


async def test_persist_registry_keeps_agent_card_and_status():
    agents = [
        {
            "name": "observe_agent",
            "status": "running",
            "transport": "http",
            "url": "http://localhost:8080",
            "agent_card": {"name": "Observe Agent", "skills": [{"id": "s1"}]},
        }
    ]
    state_manager = _FakeStateManager()
    await _persist_registry(_Ctx(state_manager, _FakeRegistry(agents)))

    assert state_manager.saved is not None
    saved = state_manager.saved["agents"][0]
    assert saved["agent_card"] == {"name": "Observe Agent", "skills": [{"id": "s1"}]}
    assert saved["status"] == "running"
    assert saved["url"] == "http://localhost:8080"
