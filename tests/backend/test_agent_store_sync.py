"""Unit tests for read-through A2A mapping in the backend agent store.

A2A agents are no longer stored in the backend DB; they are mapped on the fly from
Progenitor's registry document (each ``AgentInfo`` already carries the live
``agent_card`` and health-derived ``status``). These tests avoid Postgres and the
network by constructing ``AgentInfo``-like objects and monkeypatching the registry
read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.backend import agent_store


@dataclass
class _Info:
    """Stand-in for ``state_reader.AgentInfo`` (only the attributes used here)."""

    name: str = "Observe Agent"
    description: str = "Monitors the runtime."
    version: str = "9.9.9"
    kind: str = "a2a_agent"
    transport: str = "http"
    url: str = "http://localhost:8080"
    endpoint: str = "http://localhost:8080"
    enabled: bool = True
    status: str = "running"
    updated_at: str = "2026-06-08T00:00:00Z"
    agent_card: dict[str, Any] | None = field(default=None)
    acp: dict[str, Any] | None = field(default=None)


def test_skills_from_card_normalizes():
    card = {
        "skills": [
            {
                "id": "a",
                "name": "A",
                "description": "desc",
                "tags": ["t1", "t2"],
                "examples": ["do a thing"],
            },
            {"name": "no-id-uses-name"},
            "not-a-dict",
            {"description": "skipped, no id or name"},
        ]
    }
    skills = agent_store._skills_from_card(card)
    assert [s["id"] for s in skills] == ["a", "no-id-uses-name"]
    assert skills[0]["tags"] == ["t1", "t2"]
    assert skills[0]["examples"] == ["do a thing"]
    assert skills[1]["examples"] == []


def test_a2a_card_details_extracts_metadata():
    card = {
        "protocolVersion": "0.3.0",
        "preferredTransport": "JSONRPC",
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "capabilities": {"streaming": False, "stateTransitionHistory": True},
        "provider": {"organization": "Progenitor", "url": "https://example.test"},
        "documentationUrl": "https://docs.example.test",
    }
    details = agent_store._a2a_card_details(card)
    assert details["protocolVersion"] == "0.3.0"
    assert details["preferredTransport"] == "JSONRPC"
    assert details["inputModes"] == ["text/plain"]
    assert details["outputModes"] == ["text/plain", "application/json"]
    assert details["capabilities"] == {
        "streaming": False,
        "pushNotifications": False,
        "stateTransitionHistory": True,
    }
    assert details["provider"] == {"organization": "Progenitor", "url": "https://example.test"}
    assert details["documentationUrl"] == "https://docs.example.test"


def test_a2a_card_details_tolerates_snake_case_and_empty():
    assert agent_store._a2a_card_details(None) == {}
    assert agent_store._a2a_card_details({}) == {}
    details = agent_store._a2a_card_details(
        {"protocol_version": "0.3.0", "default_input_modes": ["text/plain"]}
    )
    assert details["protocolVersion"] == "0.3.0"
    assert details["inputModes"] == ["text/plain"]


def test_acp_profile_from_info_maps_config():
    info = _Info(
        name="github_copilot",
        kind="acp_agent",
        description="Copilot over ACP.",
        transport="acp",
        url=None,
        status="running",
        acp={"command": "copilot", "profile": "copilot", "model": "gpt-5-mini"},
    )
    profile = agent_store.acp_profile_from_info(info, {})
    assert profile["id"] == "acp-github-copilot"
    assert profile["kind"] == "acp"
    assert profile["group"] == "ACP agents"
    assert profile["transport"] == "stdio"
    assert profile["status"] == "running"
    card = profile["card"]
    assert "--acp" in card["command"]
    assert card["model"] == "gpt-5-mini"
    # copilot profile runs with --yolo -> auto-approve capability on.
    assert card["capabilities"]["autoApprove"] is True
    # Exposed tools mirror the ACP client callbacks (terminal + file access).
    assert {t["name"] for t in profile["tools"]} == {
        "terminal",
        "read_text_file",
        "write_text_file",
    }
    # ACP agents are read-only: no editable graph.
    assert profile["graph"] is None


def test_acp_profiles_from_registry_filters_by_transport(monkeypatch):
    acp_info = _Info(name="github_copilot", transport="acp", acp={"command": "copilot"})
    http_info = _Info(name="Observe Agent", transport="http")
    fake_reader = type(
        "R",
        (),
        {
            "get_registry": lambda self: ([acp_info, http_info], []),
            "get_acp_tasks": lambda self: [],
        },
    )()
    import app.backend.state_reader as state_reader

    monkeypatch.setattr(state_reader, "get_state_reader", lambda: fake_reader)

    # ACP read-through returns only the acp-transport entry...
    acp_profiles = agent_store.acp_profiles_from_registry()
    assert [p["id"] for p in acp_profiles] == ["acp-github-copilot"]
    # ...and A2A read-through excludes it.
    a2a_profiles = agent_store.a2a_profiles_from_registry()
    assert all(p["kind"] == "a2a" for p in a2a_profiles)
    assert "acp-github-copilot" not in {p["id"] for p in a2a_profiles}


def test_acp_profiles_from_registry_detects_acp_kind_without_transport(monkeypatch):
    acp_info = _Info(
        name="cronix_copilot",
        kind="acp_agent",
        transport="http",
        acp={"command": "copilot", "port": 4173},
    )
    http_info = _Info(name="Observe Agent", transport="http")
    fake_reader = type(
        "R",
        (),
        {
            "get_registry": lambda self: ([acp_info, http_info], []),
            "get_acp_tasks": lambda self: [],
            "get_a2a_tasks": lambda self: [],
        },
    )()
    import app.backend.state_reader as state_reader

    monkeypatch.setattr(state_reader, "get_state_reader", lambda: fake_reader)

    acp_profiles = agent_store.acp_profiles_from_registry()
    assert [p["id"] for p in acp_profiles] == ["acp-cronix-copilot"]
    assert acp_profiles[0]["transport"] == "tcp"

    a2a_profiles = agent_store.a2a_profiles_from_registry()
    assert [p["id"] for p in a2a_profiles] == ["a2a-observe-agent"]


def test_a2a_profile_from_info_maps_card():
    info = _Info(
        agent_card={
            "name": "Observe Agent",
            "description": "Live description from card.",
            "version": "0.2.0",
            "preferredTransport": "JSONRPC",
            "protocolVersion": "0.3.0",
            "capabilities": {"streaming": True},
            "skills": [{"id": "progenitor_health_report", "name": "Health"}],
        }
    )
    profile = agent_store.a2a_profile_from_info(info)
    assert profile["id"] == "a2a-observe-agent"
    assert profile["kind"] == "a2a"
    assert profile["group"] == "A2A agents"
    assert profile["version"] == "0.2.0"  # card wins over registry "9.9.9"
    assert profile["summary"] == "Live description from card."
    # preferredTransport from the card wins over the registry "http".
    assert profile["transport"] == "JSONRPC"
    assert profile["card"]["protocolVersion"] == "0.3.0"
    assert profile["card"]["capabilities"]["streaming"] is True
    assert [s["id"] for s in profile["skills"]] == ["progenitor_health_report"]
    # A2A agents are read-only: no editable graph attached.
    assert profile["graph"] is None


def test_a2a_profile_status_is_verbatim():
    assert agent_store.a2a_profile_from_info(_Info(status="failed"))["status"] == "failed"
    assert agent_store.a2a_profile_from_info(_Info(status="stopped"))["status"] == "stopped"
    # falls back to the registry transport/url when there is no card
    no_card = agent_store.a2a_profile_from_info(_Info(agent_card=None))
    assert no_card["transport"] == "http"
    assert no_card["url"] == "http://localhost:8080"
    assert no_card["skills"] == []


def test_a2a_profiles_from_registry(monkeypatch):
    fake_reader = type(
        "R",
        (),
        {"get_registry": lambda self: ([_Info(status="running"), _Info(name="Other")], [])},
    )()
    import app.backend.state_reader as state_reader

    monkeypatch.setattr(state_reader, "get_state_reader", lambda: fake_reader)

    profiles = agent_store.a2a_profiles_from_registry()
    assert [p["id"] for p in profiles] == ["a2a-observe-agent", "a2a-other"]
    assert all(p["kind"] == "a2a" for p in profiles)


def test_a2a_profiles_from_registry_handles_failure(monkeypatch):
    import app.backend.state_reader as state_reader

    def _boom():
        raise RuntimeError("no reader")

    monkeypatch.setattr(state_reader, "get_state_reader", _boom)
    assert agent_store.a2a_profiles_from_registry() == []


def test_a2a_profile_attaches_tasks_and_checkpoint_count():
    tasks_by_agent = {
        "Observe Agent": [
            {"id": "t1", "status": "completed", "context_id": "ctx-1", "title": "do x"},
            {"id": "t2", "status": "failed", "context_id": "ctx-1", "title": "do y"},
            {"id": "t3", "status": "working", "context_id": "ctx-2", "title": "do z"},
        ]
    }
    profile = agent_store.a2a_profile_from_info(_Info(), tasks_by_agent)
    assert [t["id"] for t in profile["tasks"]] == ["t1", "t2", "t3"]
    assert profile["metrics"]["tasks"] == "3"
    # distinct context_ids -> conversation/checkpoint threads
    assert profile["metrics"]["checkpoints"] == "2"


def test_a2a_tasks_by_agent_groups_sorts_and_shapes(monkeypatch):
    records = [
        {
            "task_id": "a",
            "agent_name": "Observe Agent",
            "status": "completed",
            "request_text": "older request",
            "context_id": "c1",
            "updated_at": "2026-06-01T00:00:00Z",
        },
        {
            "task_id": "b",
            "agent_name": "Observe Agent",
            "status": "working",
            "request_text": "newer request",
            "context_id": "c2",
            "updated_at": "2026-06-08T00:00:00Z",
        },
        {"task_id": "c", "agent_name": "Other", "status": "failed", "updated_at": "2026-06-05Z"},
        "not-a-dict",
        {"task_id": "d", "status": "working"},  # no agent_name -> skipped
    ]
    fake_reader = type("R", (), {"get_a2a_tasks": lambda self: records})()
    import app.backend.state_reader as state_reader

    monkeypatch.setattr(state_reader, "get_state_reader", lambda: fake_reader)

    grouped = agent_store._a2a_tasks_by_agent()
    assert set(grouped) == {"Observe Agent", "Other"}
    # newest-updated first, and request_text becomes the task title
    assert [t["id"] for t in grouped["Observe Agent"]] == ["b", "a"]
    assert grouped["Observe Agent"][0]["title"] == "newer request"
