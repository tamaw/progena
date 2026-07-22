from fastapi.testclient import TestClient

from app.backend.document_store import (
    BACKEND_STATE_PREFIX,
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
)
from app.backend.main import app
from app.backend.session import SessionConfig, SessionManager
from app.backend.state_reader import StateReader


def test_legacy_session_route_is_deleted_without_creating_a_cookie(monkeypatch):
    import app.backend.main as backend_main

    session_manager = SessionManager(SessionConfig(persist_dir=None))
    monkeypatch.setattr(backend_main, "session_manager", session_manager)

    with TestClient(app) as client:
        response = client.get("/v1/session")

    assert response.status_code == 404
    assert "set-cookie" not in response.headers


def test_deleted_session_route_does_not_restore_stale_conversation_state(monkeypatch):
    import app.backend.main as backend_main

    session_manager = SessionManager(SessionConfig(persist_dir=None))
    monkeypatch.setattr(backend_main, "session_manager", session_manager)

    with TestClient(app) as client:
        client.cookies.set("progenitor_session", "stale-session")
        response = client.get("/v1/session")

    assert response.status_code == 404
    assert "set-cookie" not in response.headers


def test_state_reader_uses_current_state_for_thread_and_messages():
    store = InMemoryDocumentStore()

    # Health
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "health",
        {
            "status": "running",
            "last_active": "2026-04-20T00:00:00Z",
            "thread_id": "thread_live",
            "cycle_count": 12,
            "pid": 4242,
        },
    )

    # Current state with messages
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "current_state",
        {
            "thread_id": "thread_live",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "again"},
            ],
            "cycle_count": 12,
        },
    )

    # Build state (empty)
    store.save_document(PROGENITOR_STATE_PREFIX, "build_state", {})

    # Schedule (empty)
    store.save_document(PROGENITOR_STATE_PREFIX, "schedule", {})

    # Sessions as a list
    store.save_document(
        BACKEND_STATE_PREFIX,
        "sessions",
        [
            {"session_id": "sess_old", "last_activity": "2026-04-20T10:00:00Z"},
            {"session_id": "sess_new", "last_activity": "2026-04-21T09:15:00Z"},
        ],
    )

    reader = StateReader(document_store=store)

    health = reader.get_health()
    snapshot = reader.get_snapshot()
    workspace = reader.get_workspace_payload()

    assert health.thread_id == "thread_live"
    assert health.last_active == "2026-04-20T00:00:00Z"
    assert snapshot.thread_id == "thread_live"
    assert snapshot.messages_count == 3
    assert snapshot.sessions is not None
    assert snapshot.sessions.total == 2
    assert snapshot.sessions.latest_session_id == "sess_new"
    assert workspace["health"]["thread_id"] == "thread_live"
