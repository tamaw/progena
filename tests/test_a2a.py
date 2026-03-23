"""Pytest tests for the Gym Agent A2A service.

Uses TestClient with an in-memory SQLite database to keep tests fast and
side-effect free.  The ``GYM_AGENT_DB`` environment variable is patched to
``:memory:`` before the app is imported so storage never touches the filesystem.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Patch the DB path to :memory: before the app module is loaded
# ---------------------------------------------------------------------------

os.environ["GYM_AGENT_DB"] = ":memory:"

from src.gym_agent import storage  # noqa: E402  (must come after env patch)
from src.gym_agent.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-initialise an in-memory database before each test."""
    monkeypatch.setenv("GYM_AGENT_DB", ":memory:")
    storage.init_db()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# /agent/card
# ---------------------------------------------------------------------------


def test_agent_card(client: TestClient) -> None:
    resp = client.get("/agent/card")
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"] == "gym_agent"
    names = [c["name"] for c in card["capabilities"]]
    assert "log_workout" in names
    assert "get_history" in names
    assert "summarize_progress" in names
    assert "habit_streak" in names


# ---------------------------------------------------------------------------
# log_workout
# ---------------------------------------------------------------------------


def test_log_workout(client: TestClient) -> None:
    resp = client.post(
        "/a2a",
        json={
            "capability": "log_workout",
            "payload": {
                "user_id": "alice",
                "date": "2024-01-15",
                "workout": {"type": "running", "duration_min": 30, "distance_km": 5.0},
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["result"]["user_id"] == "alice"
    assert data["result"]["date"] == "2024-01-15"
    assert isinstance(data["result"]["id"], int)


def test_log_workout_minimal(client: TestClient) -> None:
    """Only the required workout.type field is provided."""
    resp = client.post(
        "/a2a",
        json={
            "capability": "log_workout",
            "payload": {
                "user_id": "bob",
                "date": "2024-02-01",
                "workout": {"type": "yoga"},
            },
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_log_workout_invalid_payload_returns_400(client: TestClient) -> None:
    """Missing required 'date' field should return HTTP 400."""
    resp = client.post(
        "/a2a",
        json={
            "capability": "log_workout",
            "payload": {
                "user_id": "alice",
                # date intentionally omitted
                "workout": {"type": "cycling"},
            },
        },
    )
    assert resp.status_code == 400


def test_log_workout_invalid_capability_returns_422(client: TestClient) -> None:
    resp = client.post(
        "/a2a",
        json={"capability": "unknown_cap", "payload": {}},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# get_history
# ---------------------------------------------------------------------------


def _seed(client: TestClient, user_id: str, entries: list[dict]) -> None:
    for entry in entries:
        client.post("/a2a", json={"capability": "log_workout", "payload": {"user_id": user_id, **entry}})


def test_get_history(client: TestClient) -> None:
    _seed(
        client,
        "alice",
        [
            {"date": "2024-01-10", "workout": {"type": "running", "duration_min": 40}},
            {"date": "2024-01-11", "workout": {"type": "cycling", "duration_min": 60}},
        ],
    )
    resp = client.post(
        "/a2a",
        json={"capability": "get_history", "payload": {"user_id": "alice"}},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert len(result) == 2
    # Most recent first
    assert result[0]["date"] >= result[1]["date"]


def test_get_history_date_filter(client: TestClient) -> None:
    _seed(
        client,
        "charlie",
        [
            {"date": "2024-01-01", "workout": {"type": "running"}},
            {"date": "2024-02-01", "workout": {"type": "running"}},
            {"date": "2024-03-01", "workout": {"type": "running"}},
        ],
    )
    resp = client.post(
        "/a2a",
        json={
            "capability": "get_history",
            "payload": {
                "user_id": "charlie",
                "start_date": "2024-02-01",
                "end_date": "2024-02-28",
            },
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert len(result) == 1
    assert result[0]["date"] == "2024-02-01"


# ---------------------------------------------------------------------------
# summarize_progress
# ---------------------------------------------------------------------------


def test_summarize_progress(client: TestClient) -> None:
    from datetime import date, timedelta

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    _seed(
        client,
        "diana",
        [
            {"date": today, "workout": {"type": "running", "duration_min": 30, "distance_km": 5.0}},
            {"date": yesterday, "workout": {"type": "cycling", "duration_min": 45, "distance_km": 15.0}},
        ],
    )
    resp = client.post(
        "/a2a",
        json={"capability": "summarize_progress", "payload": {"user_id": "diana", "days": 7}},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["total_workouts"] == 2
    assert result["total_duration_min"] == 75
    assert result["total_distance_km"] == 20.0


# ---------------------------------------------------------------------------
# habit_streak
# ---------------------------------------------------------------------------


def test_habit_streak(client: TestClient) -> None:
    from datetime import date, timedelta

    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(5)]

    for d in dates:
        client.post(
            "/a2a",
            json={
                "capability": "log_workout",
                "payload": {"user_id": "eve", "date": d, "workout": {"type": "running"}},
            },
        )

    resp = client.post(
        "/a2a",
        json={"capability": "habit_streak", "payload": {"user_id": "eve", "habit": "running"}},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["current_streak"] == 5
    assert result["longest_streak"] == 5


def test_habit_streak_broken(client: TestClient) -> None:
    """A gap breaks the streak; longest_streak should still be recorded."""
    _seed(
        client,
        "frank",
        [
            {"date": "2024-01-01", "workout": {"type": "running"}},
            {"date": "2024-01-02", "workout": {"type": "running"}},
            # gap on 2024-01-03
            {"date": "2024-01-10", "workout": {"type": "running"}},
        ],
    )
    resp = client.post(
        "/a2a",
        json={"capability": "habit_streak", "payload": {"user_id": "frank", "habit": "running"}},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["longest_streak"] == 2
    assert result["current_streak"] == 0  # last workout is in the past, far from today


# ---------------------------------------------------------------------------
# Context-ID echo
# ---------------------------------------------------------------------------


def test_context_id_echoed(client: TestClient) -> None:
    resp = client.post(
        "/a2a",
        json={"capability": "get_history", "payload": {"user_id": "test_user"}},
        headers={"X-A2A-Context-ID": "ctx-abc-123"},
    )
    assert resp.status_code == 200
    assert resp.json().get("context_id") == "ctx-abc-123"
