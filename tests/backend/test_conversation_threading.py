import json
import sqlite3

import msgpack
from fastapi.testclient import TestClient

from app.backend.main import app
from app.backend.session import SessionConfig, SessionManager
from app.backend.state_reader import StateReader


def test_get_session_sets_cookie_and_default_conversation_id(monkeypatch):
    import app.backend.main as backend_main

    session_manager = SessionManager(SessionConfig(persist_dir=None))
    monkeypatch.setattr(backend_main, "session_manager", session_manager)

    with TestClient(app) as client:
        response = client.get("/v1/session")

    assert response.status_code == 200
    data = response.json()
    assert response.cookies.get("progenitor_session") == data["session_id"]
    assert data["conversation_id"] == f"thread_{data['session_id']}"
    assert data["message_count"] == 0


def test_state_reader_prefers_sqlite_conversation_state(tmp_path):
    data_dir = tmp_path / "data"
    state_dir = data_dir / "state"
    config_dir = data_dir / "config"
    sessions_dir = data_dir / "sessions"

    state_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    sessions_dir.mkdir(parents=True)

    (state_dir / "health.json").write_text(
        json.dumps(
            {
                "status": "running",
                "last_active": "2026-04-20T00:00:00Z",
                "thread_id": "thread_stale",
                "cycle_count": 12,
                "pid": 4242,
            }
        ),
        encoding="utf-8",
    )
    (state_dir / "current_state.json").write_text(
        json.dumps(
            {
                "thread_id": "thread_stale",
                "messages": [{"role": "assistant", "content": "stale"}],
                "cycle_count": 12,
            }
        ),
        encoding="utf-8",
    )
    (state_dir / "build_state.json").write_text(json.dumps({}), encoding="utf-8")
    (config_dir / "schedule.json").write_text("[]", encoding="utf-8")
    (sessions_dir / "sessions.json").write_text(
        json.dumps(
            {
                "sess_old": {
                    "session_id": "sess_old",
                    "last_activity": "2026-04-20T10:00:00Z",
                },
                "sess_new": {
                    "session_id": "sess_new",
                    "last_activity": "2026-04-21T09:15:00Z",
                },
            }
        ),
        encoding="utf-8",
    )

    db_path = state_dir / "conversations.db"
    checkpoint_payload = {
        "v": 4,
        "ts": "2026-04-21T09:16:00Z",
        "id": "chk_live",
        "channel_values": {
            "messages": ["user-1", "assistant-1", "user-2"],
        },
        "channel_versions": {},
        "versions_seen": {},
        "updated_channels": ["messages"],
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE checkpoints (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                parent_checkpoint_id TEXT,
                type TEXT,
                checkpoint BLOB,
                metadata BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE writes (
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                channel TEXT NOT NULL,
                type TEXT,
                value BLOB,
                PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO checkpoints (
                thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata
            ) VALUES (?, '', ?, NULL, 'msgpack', ?, ?)
            """,
            (
                "thread_live",
                "chk_live",
                msgpack.packb(checkpoint_payload, use_bin_type=True),
                json.dumps({"source": "input"}),
            ),
        )
        conn.execute(
            """
            INSERT INTO writes (
                thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value
            ) VALUES (?, '', ?, 'task-1', 0, 'messages', 'msgpack', ?)
            """,
            (
                "thread_live",
                "chk_live",
                msgpack.packb(["user-1", "assistant-1", "user-2"], use_bin_type=True),
            ),
        )
        conn.commit()

    reader = StateReader(
        state_dir=state_dir,
        config_dir=config_dir,
        sessions_dir=sessions_dir,
    )

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
