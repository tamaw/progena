"""Tests for backend-managed agent registry routes."""

import json

from app.backend.state_reader import StateReader


def _make_state_reader(tmp_path):
    state_dir = tmp_path / "data" / "state"
    config_dir = tmp_path / "data" / "config"
    sessions_dir = tmp_path / "data" / "sessions"
    logs_dir = tmp_path / "logs"

    state_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    sessions_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)

    return StateReader(
        state_dir=state_dir,
        config_dir=config_dir,
        sessions_dir=sessions_dir,
        logs_dir=logs_dir,
    )


def test_register_agent_endpoint_writes_registry(client, tmp_path, monkeypatch):
    state_reader = _make_state_reader(tmp_path)
    monkeypatch.setattr("app.backend.agent_routes.get_state_reader", lambda: state_reader)

    response = client.post(
        "/agents/register",
        json={
            "name": "observe_agent",
            "url": "http://progenitor-agent-observe_agent:8080",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "name": "observe_agent",
        "url": "http://progenitor-agent-observe_agent:8080",
        "total": 1,
    }

    registry_path = state_reader.config_dir / "agents.json"
    assert json.loads(registry_path.read_text(encoding="utf-8")) == {
        "observe_agent": {"url": "http://progenitor-agent-observe_agent:8080"}
    }


def test_register_agent_endpoint_preserves_existing_headers(client, tmp_path, monkeypatch):
    state_reader = _make_state_reader(tmp_path)
    registry_path = state_reader.config_dir / "agents.json"
    registry_path.write_text(
        json.dumps(
            {
                "builder_agent": {
                    "url": "http://old-host:8107",
                    "headers": {"Authorization": "Bearer token"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.backend.agent_routes.get_state_reader", lambda: state_reader)

    response = client.post(
        "/agents/register",
        json={
            "name": "builder_agent",
            "url": "http://progenitor-agent-builder_agent:8107",
        },
    )

    assert response.status_code == 200
    assert json.loads(registry_path.read_text(encoding="utf-8")) == {
        "builder_agent": {
            "url": "http://progenitor-agent-builder_agent:8107",
            "headers": {"Authorization": "Bearer token"},
        }
    }
