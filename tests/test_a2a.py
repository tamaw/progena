"""Pytest tests for the lifting-first Gym Agent A2A service."""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ["GYM_AGENT_DB"] = ":memory:"

from src.gym_agent import storage  # noqa: E402  (must come after env patch)
from src.gym_agent.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-initialize an in-memory database before each test."""
    monkeypatch.setenv("GYM_AGENT_DB", ":memory:")
    storage.init_db()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


def _log_lifting_session(client: TestClient, user_id: str, payload: dict[str, Any]) -> None:
    response = client.post(
        "/a2a",
        json={"capability": "log_lifting_session", "payload": {"user_id": user_id, **payload}},
    )
    assert response.status_code == 200


def _log_bodyweight(client: TestClient, user_id: str, entry_date: str, weight_kg: float) -> None:
    response = client.post(
        "/a2a",
        json={
            "capability": "log_bodyweight",
            "payload": {"user_id": user_id, "date": entry_date, "weight_kg": weight_kg},
        },
    )
    assert response.status_code == 200


def test_agent_card(client: TestClient) -> None:
    response = client.get("/agent/card")
    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "gym_agent"
    capabilities = {capability["name"] for capability in card["capabilities"]}
    assert capabilities == {
        "log_lifting_session",
        "get_lifting_history",
        "summarize_strength_progress",
        "get_personal_records",
        "log_bodyweight",
        "get_bodyweight_history",
        "summarize_bodyweight_progress",
    }


def test_log_lifting_session(client: TestClient) -> None:
    response = client.post(
        "/a2a",
        json={
            "capability": "log_lifting_session",
            "payload": {
                "user_id": "alice",
                "date": "2024-01-15",
                "workout_name": "Upper A",
                "notes": "Strong pressing day",
                "exercises": [
                    {
                        "name": "Bench Press",
                        "sets": [
                            {"reps": 5, "weight_kg": 80, "set_type": "working"},
                            {"reps": 5, "weight_kg": 82.5, "set_type": "working"},
                        ],
                    },
                    {
                        "name": "Barbell Row",
                        "sets": [{"reps": 8, "weight_kg": 70, "set_type": "working"}],
                    },
                ],
            },
        },
    )
    assert response.status_code == 200
    data = response.json()["result"]
    assert isinstance(data["session_id"], int)
    assert data["exercise_count"] == 2
    assert data["total_sets"] == 3
    assert data["total_reps"] == 18
    assert data["total_volume_kg"] == 1372.5


def test_log_lifting_session_invalid_payload_returns_400(client: TestClient) -> None:
    response = client.post(
        "/a2a",
        json={
            "capability": "log_lifting_session",
            "payload": {
                "user_id": "alice",
                "date": "2024-01-15",
                "workout_name": "Upper A",
                "exercises": [],
            },
        },
    )
    assert response.status_code == 400


def test_legacy_capability_returns_422(client: TestClient) -> None:
    response = client.post(
        "/a2a",
        json={"capability": "log_workout", "payload": {}},
    )
    assert response.status_code == 422


def test_get_lifting_history(client: TestClient) -> None:
    _log_lifting_session(
        client,
        "alice",
        {
            "date": "2024-01-10",
            "workout_name": "Upper A",
            "exercises": [
                {"name": "Bench Press", "sets": [{"reps": 5, "weight_kg": 80}]},
                {"name": "Barbell Row", "sets": [{"reps": 8, "weight_kg": 70}]},
            ],
        },
    )
    _log_lifting_session(
        client,
        "alice",
        {
            "date": "2024-01-12",
            "workout_name": "Lower A",
            "exercises": [{"name": "Squat", "sets": [{"reps": 5, "weight_kg": 120}]}],
        },
    )

    response = client.post(
        "/a2a",
        json={"capability": "get_lifting_history", "payload": {"user_id": "alice"}},
    )
    assert response.status_code == 200
    sessions = response.json()["result"]
    assert len(sessions) == 2
    assert sessions[0]["date"] == "2024-01-12"
    assert sessions[0]["exercises"][0]["name"] == "Squat"
    assert sessions[1]["total_volume_kg"] == 960.0


def test_get_lifting_history_exercise_filter(client: TestClient) -> None:
    _log_lifting_session(
        client,
        "alice",
        {
            "date": "2024-01-10",
            "workout_name": "Upper A",
            "exercises": [{"name": "Bench Press", "sets": [{"reps": 5, "weight_kg": 80}]}],
        },
    )
    _log_lifting_session(
        client,
        "alice",
        {
            "date": "2024-01-12",
            "workout_name": "Lower A",
            "exercises": [{"name": "Squat", "sets": [{"reps": 5, "weight_kg": 120}]}],
        },
    )

    response = client.post(
        "/a2a",
        json={
            "capability": "get_lifting_history",
            "payload": {"user_id": "alice", "exercise_name": "bench press"},
        },
    )
    assert response.status_code == 200
    sessions = response.json()["result"]
    assert len(sessions) == 1
    assert sessions[0]["workout_name"] == "Upper A"


def test_summarize_strength_progress(client: TestClient) -> None:
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    _log_lifting_session(
        client,
        "diana",
        {
            "date": today,
            "workout_name": "Push",
            "exercises": [
                {"name": "Bench Press", "sets": [{"reps": 5, "weight_kg": 80}]},
                {"name": "Overhead Press", "sets": [{"reps": 6, "weight_kg": 50}]},
            ],
        },
    )
    _log_lifting_session(
        client,
        "diana",
        {
            "date": yesterday,
            "workout_name": "Push",
            "exercises": [{"name": "Bench Press", "sets": [{"reps": 4, "weight_kg": 85}]}],
        },
    )

    response = client.post(
        "/a2a",
        json={
            "capability": "summarize_strength_progress",
            "payload": {"user_id": "diana", "days": 7},
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["total_sessions"] == 2
    assert result["distinct_exercises"] == 2
    assert result["total_sets"] == 3
    assert result["total_reps"] == 15
    assert result["total_volume_kg"] == 1040.0
    assert result["top_set_weight_kg"] == 85.0
    assert len(result["exercise_summaries"]) == 2


def test_get_personal_records(client: TestClient) -> None:
    _log_lifting_session(
        client,
        "eve",
        {
            "date": "2024-01-10",
            "workout_name": "Upper A",
            "exercises": [{"name": "Bench Press", "sets": [{"reps": 8, "weight_kg": 70}]}],
        },
    )
    _log_lifting_session(
        client,
        "eve",
        {
            "date": "2024-01-12",
            "workout_name": "Upper B",
            "exercises": [{"name": "Bench Press", "sets": [{"reps": 3, "weight_kg": 90}]}],
        },
    )

    response = client.post(
        "/a2a",
        json={
            "capability": "get_personal_records",
            "payload": {"user_id": "eve", "exercise_name": "Bench Press"},
        },
    )
    assert response.status_code == 200
    records = response.json()["result"]["records"]
    assert len(records) == 1
    assert records[0]["heaviest_set"]["weight_kg"] == 90.0
    assert records[0]["heaviest_set"]["reps"] == 3
    assert records[0]["highest_volume_set"]["volume_kg"] == 560.0


def test_log_bodyweight_and_history(client: TestClient) -> None:
    _log_bodyweight(client, "frank", "2024-01-01", 90.2)
    _log_bodyweight(client, "frank", "2024-01-05", 89.6)

    response = client.post(
        "/a2a",
        json={
            "capability": "get_bodyweight_history",
            "payload": {"user_id": "frank"},
        },
    )
    assert response.status_code == 200
    history = response.json()["result"]
    assert len(history) == 2
    assert history[0]["date"] == "2024-01-05"
    assert history[0]["weight_kg"] == 89.6


def test_summarize_bodyweight_progress(client: TestClient) -> None:
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    _log_bodyweight(client, "gina", yesterday, 80.4)
    _log_bodyweight(client, "gina", today, 79.8)

    response = client.post(
        "/a2a",
        json={
            "capability": "summarize_bodyweight_progress",
            "payload": {"user_id": "gina", "days": 7},
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["entry_count"] == 2
    assert result["starting_weight_kg"] == 80.4
    assert result["latest_weight_kg"] == 79.8
    assert result["change_kg"] == -0.6


def test_context_id_echoed(client: TestClient) -> None:
    response = client.post(
        "/a2a",
        json={
            "capability": "get_bodyweight_history",
            "payload": {"user_id": "test_user"},
        },
        headers={"X-A2A-Context-ID": "ctx-abc-123"},
    )
    assert response.status_code == 200
    assert response.json().get("context_id") == "ctx-abc-123"
