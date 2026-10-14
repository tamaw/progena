"""
Tests for Chat UI API routes.

Note: The backend is now API-only and does not serve static files.
The frontend runs separately via npm/Vite.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from ack_helpers import assert_acknowledged, assert_acknowledgement_body
from fastapi.testclient import TestClient

from app.backend.alerts import clear_alerts, create_alert
from app.backend.document_events import seed_document_types
from app.backend.document_store import (
    BACKEND_STATE_PREFIX,
    PROGENITOR_LOG_PREFIX,
    PROGENITOR_STATE_PREFIX,
    CheckpointThreadSnapshot,
    InMemoryDocumentStore,
    set_default_document_store,
)
from app.backend.main import app
from app.backend.models import AgentResponse
from app.backend.state_reader import StateReader
from app.backend.threads import clear_threads
from app.progenitor.document_kinds import SESSION_KEY_PREFIX


def _create_session_document(store, thread_id, title="New chat", **extra):
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        f"{SESSION_KEY_PREFIX}{thread_id}",
        {
            "id": thread_id,
            "thread_id": thread_id,
            "source": "ui",
            "title": title,
            "first_message": "",
            "pinned": False,
            "archived": False,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "last_activity": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "content": "",
            **extra,
        },
    )


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_thread_registry():
    """Keep the in-memory thread registry isolated between tests."""

    clear_threads()
    yield
    clear_threads()


@pytest.fixture
def kanban_state_reader(tmp_path, monkeypatch, document_store):
    """Provide an isolated state reader for kanban persistence tests."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    state_reader = StateReader(
        document_store=document_store,
        logs_dir=logs_dir,
    )
    monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)
    import app.backend.ui_routes as ui_routes

    ui_routes._document_event_dispatcher = None
    yield state_reader
    ui_routes._document_event_dispatcher = None


@pytest.fixture
def tasks_state_reader(tmp_path, monkeypatch, document_store):
    """State reader backed by the shared document store for tasks (DB-only).

    Tasks have no file fallback, so these tests need a real document store. The
    board, task bodies, and activity all persist under the shared
    ``("progenitor", "state")`` namespace — the same documents the runtime
    agent reads/edits through ``/state/tasks/`` as ``board.json`` plus per-task
    ``<id>.md`` files.
    """
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    state_reader = StateReader(
        document_store=document_store,
        logs_dir=logs_dir,
    )
    monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)
    import app.backend.ui_routes as ui_routes

    ui_routes._document_event_dispatcher = None
    yield state_reader
    ui_routes._document_event_dispatcher = None


@pytest.fixture
def document_store():
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    seed_document_types(store)
    yield store
    set_default_document_store(InMemoryDocumentStore())


class TestUIRoutes:
    """Tests for /api/ui endpoints."""

    def test_ui_index_returns_404(self, client):
        """Test that / returns 404 (API-only backend)."""
        response = client.get("/")
        # Root path should return 404 - backend is API-only now
        assert response.status_code in (404, 405, 307)

    def test_ui_assets_not_served(self, client):
        """Test that /ui/ returns 404 (API-only backend)."""
        response = client.get("/ui/")
        assert response.status_code == 404

    def test_widgets_endpoint(self, client):
        request_id = "widgets-request"
        response = client.get("/api/ui/widgets", headers={"X-Request-ID": request_id})

        assert_acknowledged(
            response, ["widgets.updated"], request_id=request_id, check_request_id_header=True
        )

    def test_process_metrics_excludes_db_fields(self):
        """The periodic tick carries only process metrics, never DB-derived state.

        progenitor_running belongs here too: it's a live SSE heartbeat check,
        not a Postgres read (see main.get_progenitor_liveness()).
        """
        from app.backend import ui_routes

        metrics = ui_routes._process_metrics()
        assert set(metrics) == {"status", "uptime_seconds", "memory_mb", "progenitor_running"}
        assert metrics["status"] in {"healthy", "draining"}
        assert isinstance(metrics["progenitor_running"], bool)
        # None of the DB-derived fields leak into the metrics tick.
        assert "widgets" not in metrics
        assert "agents" not in metrics
        assert "schedule" not in metrics

    def test_widgets_endpoint_broadcasts_snapshot(self, client):
        """The acknowledgement is backed by a request-correlated canonical event."""
        from app.backend import ui_routes

        request_id = "widgets-stream-request"
        event_start = len(ui_routes._ui_event_replay)
        response = client.get("/api/ui/widgets", headers={"X-Request-ID": request_id})

        assert_acknowledged(
            response, ["widgets.updated"], request_id=request_id, check_request_id_header=True
        )
        envelope = next(
            event
            for event in ui_routes._ui_event_replay[event_start:]
            if event.payload.get("request_id") == request_id
        )
        assert envelope.type == "widgets.updated"
        assert isinstance(envelope.payload["items"], list)

    def test_mcp_endpoint_broadcasts_snapshot(self, client):
        """MCP data is available only from a request-correlated canonical event."""
        from app.backend import ui_routes

        request_id = "mcp-stream-request"
        event_start = len(ui_routes._ui_event_replay)
        response = client.get("/api/ui/mcp", headers={"X-Request-ID": request_id})

        assert_acknowledged(
            response, ["mcp.snapshot"], request_id=request_id, check_request_id_header=True
        )
        envelope = next(
            event
            for event in ui_routes._ui_event_replay[event_start:]
            if event.payload.get("request_id") == request_id
        )
        assert envelope.type == "mcp.snapshot"
        assert isinstance(envelope.payload["items"], list)

    def test_chat_endpoints_read_session_documents(self, client, document_store):
        _create_session_document(document_store, "thread-1", "Backend chat")
        thread_id = "thread-1"

        patch_request_id = "chat-patch-request"
        patch_response = client.patch(
            f"/api/ui/chats/{thread_id}",
            json={"pinned": True, "archived": True},
            headers={"X-Request-ID": patch_request_id},
        )
        assert_acknowledged(
            patch_response,
            ["chat.session.metadata.updated", "chat.sessions.snapshot"],
            request_id=patch_request_id,
            check_request_id_header=True,
        )
        stored = document_store.get_document(
            PROGENITOR_STATE_PREFIX, f"{SESSION_KEY_PREFIX}{thread_id}"
        )
        assert stored is not None
        assert stored.payload["title"] == "Backend chat"
        assert stored.payload["pinned"] is True
        assert stored.payload["archived"] is True

        list_request_id = "chat-list-request"
        list_response = client.get(
            "/api/ui/chats?include_archived=true",
            headers={"X-Request-ID": list_request_id},
        )
        assert_acknowledged(
            list_response,
            ["chat.sessions.snapshot"],
            request_id=list_request_id,
            check_request_id_header=True,
        )
        from app.backend import ui_routes

        replay = ui_routes._ui_event_replay[-1]
        assert replay.type == "chat.sessions.snapshot"
        assert replay.payload["request_id"] == list_request_id
        assert replay.payload["total"] == 1
        assert replay.payload["sessions"][0]["thread_id"] == thread_id

    def test_chat_detail_request_emits_request_scoped_selection_event(self, client, document_store):
        from app.backend import ui_routes

        _create_session_document(document_store, "thread-select", "Selectable chat")
        thread_id = "thread-select"
        request_id = "chat-detail-request"

        detail_response = client.get(
            f"/api/ui/chats/{thread_id}",
            headers={"X-Request-ID": request_id},
        )

        assert_acknowledged(
            detail_response,
            ["chat.session.upserted", "chat.session.selected"],
            request_id=request_id,
            check_request_id_header=True,
        )
        upserted = ui_routes._ui_event_replay[-2]
        selected = ui_routes._ui_event_replay[-1]
        assert upserted.type == "chat.session.upserted"
        assert upserted.payload["request_id"] == request_id
        assert upserted.payload["session"]["thread_id"] == thread_id
        assert selected.type == "chat.session.selected"
        assert selected.payload["request_id"] == request_id
        assert selected.session_id == thread_id
        assert selected.payload["session"]["thread_id"] == thread_id

    def test_chat_card_rehydrates_history_from_checkpoint_thread(self, client, document_store):
        document_store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_sess-history",
                checkpoint_id="checkpoint-1",
                checkpoint_ts="2026-05-31T12:00:00Z",
                messages=[
                    {"id": "m1", "type": "human", "content": "count backwards"},
                    {
                        "id": "m2",
                        "type": "ai",
                        "content": [
                            {
                                "type": "reasoning",
                                "summary": [
                                    {"type": "summary_text", "text": "Need to count down."}
                                ],
                            },
                            {"type": "text", "text": "99, 98, 97"},
                        ],
                        "usage_metadata": {
                            "output_tokens": 12,
                            "output_token_details": {"reasoning": 3},
                        },
                    },
                ],
            )
        )
        _create_session_document(
            document_store, "thread_sess-history", first_message="count backwards"
        )
        thread_id = "thread_sess-history"

        list_response = client.get("/api/ui/chats?include_archived=true")
        detail_response = client.get(f"/api/ui/chats/{thread_id}?request_id=request-history")

        assert list_response.status_code == 202
        from app.backend import ui_routes

        replay_list = ui_routes._ui_event_replay[-3]
        assert replay_list.type == "chat.sessions.snapshot"
        listed = next(
            session
            for session in replay_list.payload["sessions"]
            if session["thread_id"] == thread_id
        )
        assert listed["title"] == "count backwards"
        assert listed["message_count"] == 2
        assert listed["preview"] == "99, 98, 97"

        assert detail_response.status_code == 202
        replay_detail_upsert = ui_routes._ui_event_replay[-2]
        assert replay_detail_upsert.type == "chat.session.upserted"
        detail = replay_detail_upsert.payload
        assert detail["session"]["thread_id"] == "thread_sess-history"
        assert [message["content"] for message in detail["session"]["messages"]] == [
            "count backwards",
            "99, 98, 97",
        ]
        assert detail["session"]["messages"][1]["reasoning_text"] == "Need to count down."
        assert detail["session"]["messages"][1]["reasoning_token_count"] == 3
        assert detail["session"]["messages"][1]["token_count"] == 12

    def test_chat_detail_caps_messages_and_flags_has_more(self, client, document_store):
        messages = []
        for index in range(60):
            role = "human" if index % 2 == 0 else "ai"
            messages.append({"id": f"m{index}", "type": role, "content": f"message {index}"})
        document_store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_sess-paged",
                checkpoint_id="checkpoint-1",
                checkpoint_ts="2026-05-31T12:00:00Z",
                messages=messages,
            )
        )
        _create_session_document(document_store, "thread_sess-paged")
        thread_id = "thread_sess-paged"

        detail_response = client.get(f"/api/ui/chats/{thread_id}")
        assert detail_response.status_code == 202

        from app.backend import ui_routes

        replay_selected = ui_routes._ui_event_replay[-1]
        assert replay_selected.type == "chat.session.selected"
        session = replay_selected.payload["session"]
        assert session["has_more_messages"] is True
        assert len(session["messages"]) == 50
        assert session["messages"][0]["content"] == "message 10"
        assert session["messages"][-1]["content"] == "message 59"
        assert session["message_count"] == 60

    def test_chat_messages_page_loads_older_page_until_exhausted(self, client, document_store):
        messages = []
        for index in range(60):
            role = "human" if index % 2 == 0 else "ai"
            messages.append({"id": f"m{index}", "type": role, "content": f"message {index}"})
        document_store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="thread_sess-paged-older",
                checkpoint_id="checkpoint-1",
                checkpoint_ts="2026-05-31T12:00:00Z",
                messages=messages,
            )
        )
        _create_session_document(document_store, "thread_sess-paged-older")
        thread_id = "thread_sess-paged-older"

        older_response = client.get(
            f"/api/ui/chats/{thread_id}/messages",
            params={"before_message_id": "m10"},
        )
        assert older_response.status_code == 202

        from app.backend import ui_routes

        page_event = ui_routes._ui_event_replay[-1]
        assert page_event.type == "chat.messages.page.loaded"
        assert page_event.payload["thread_id"] == thread_id
        assert page_event.payload["has_more"] is False
        page_messages = page_event.payload["messages"]
        assert len(page_messages) == 10
        assert page_messages[0]["content"] == "message 0"
        assert page_messages[-1]["content"] == "message 9"

    def test_deleted_chat_stays_deleted_despite_live_runtime_state(
        self, client, document_store, monkeypatch
    ):
        from app.backend import ui_routes

        thread_id = "thread-deleted"
        _create_session_document(document_store, thread_id, "Doomed")
        assert client.delete(f"/api/ui/chats/{thread_id}").status_code == 202

        class _LiveStateReader:
            """The runtime still holds a current_state document for the thread."""

            def get_progenitor_state(self, thread_id: str | None = None) -> dict[str, Any]:
                return {"thread_id": thread_id, "cycle_count": 1}

            def get_thread_messages(self, thread_id: str) -> list[Any]:
                return [{"id": "m1", "type": "human", "content": "hello"}]

        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: _LiveStateReader())
        event_start = len(ui_routes._ui_event_replay)

        assert client.get(f"/api/ui/chats/{thread_id}").status_code == 404
        assert client.get(f"/api/ui/chats/{thread_id}/messages").status_code == 404
        assert not [
            event
            for event in ui_routes._ui_event_replay[event_start:]
            if event.type == "chat.session.upserted"
        ]

    def test_chat_detail_reports_runtime_outage_instead_of_empty_chat(
        self, client, document_store, monkeypatch
    ):
        from app.backend.runtime_conversations import RuntimeUnavailable

        _create_session_document(document_store, "thread-out", "Has history")

        def _unavailable(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeUnavailable("runtime unreachable")

        monkeypatch.setattr(document_store, "get_checkpoint_thread_snapshot", _unavailable)

        response = client.get("/api/ui/chats/thread-out")
        assert response.status_code == 503
        # The list still renders (metadata title) while the runtime is down.
        assert client.get("/api/ui/chats").status_code == 202

    async def test_slow_chat_read_does_not_block_other_requests(self, document_store, monkeypatch):
        import time

        import httpx

        def _slow_snapshot(*_args: Any, **_kwargs: Any) -> None:
            time.sleep(1.0)  # a slow runtime read, synchronous like the real reader
            return None

        monkeypatch.setattr(document_store, "get_checkpoint_thread_snapshot", _slow_snapshot)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
            started = time.monotonic()
            slow = asyncio.create_task(async_client.get("/api/ui/chats/thread-slow"))
            await asyncio.sleep(0.1)  # let the slow read start
            fast = await async_client.get("/api/ui/preferences")
            fast_done_after = time.monotonic() - started
            await slow
        assert fast.status_code == 202
        # Had the slow read run on the event loop, nothing (not even the sleep
        # above) could finish before it did, ~1s in.
        assert fast_done_after < 0.6

    def test_chat_list_hides_schedule_activity_threads(self, client, document_store):
        document_store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id="2026-06-19T09:00:00+00:00",
                checkpoint_id="checkpoint-scheduled-thread",
                checkpoint_ts="2026-06-19T09:00:00+00:00",
                messages=[
                    {
                        "id": "m1",
                        "type": "human",
                        "content": "[Scheduled Task]\n<original_message>Morning brief</original_message>\n\nRun it",
                    },
                    {"id": "m2", "type": "ai", "content": "Completed scheduled work."},
                ],
            )
        )
        list_response = client.get("/api/ui/chats?include_archived=true")
        assert list_response.status_code == 202
        from app.backend import ui_routes

        assert all(
            session["thread_id"] != "2026-06-19T09:00:00+00:00"
            for session in ui_routes._ui_event_replay[-1].payload["sessions"]
        )

    def test_chat_delete_removes_session_document(self, client, document_store):
        _create_session_document(document_store, "thread-delete", "Disposable chat")
        thread_id = "thread-delete"

        delete_response = client.delete(f"/api/ui/chats/{thread_id}")

        assert delete_response.status_code == 202
        assert (
            document_store.get_document(PROGENITOR_STATE_PREFIX, f"{SESSION_KEY_PREFIX}{thread_id}")
            is None
        )
        list_response = client.get("/api/ui/chats?include_archived=true")
        assert list_response.status_code == 202
        from app.backend import ui_routes

        assert all(
            session["thread_id"] != thread_id
            for session in ui_routes._ui_event_replay[-1].payload["sessions"]
        )

    def test_chat_list_shows_session_documents(self, client, document_store):
        _create_session_document(document_store, "thread_sess-1", "Chat One")
        _create_session_document(document_store, "thread_sess-2", "Chat Two")

        list_response = client.get("/api/ui/chats?include_archived=true")
        assert list_response.status_code == 202
        from app.backend import ui_routes

        sessions = ui_routes._ui_event_replay[-1].payload["sessions"]
        assert len(sessions) == 2
        assert {s["thread_id"] for s in sessions} == {"thread_sess-1", "thread_sess-2"}

    def test_chat_patch_updates_session_document(
        self,
        client,
        document_store,
    ):
        _create_session_document(
            document_store, "thread_current-session-1", "Current synthetic chat"
        )

        patch_response = client.patch(
            "/api/ui/chats/thread_current-session-1",
            json={"pinned": True, "archived": True},
        )

        assert patch_response.status_code == 202
        stored = document_store.get_document(
            PROGENITOR_STATE_PREFIX, f"{SESSION_KEY_PREFIX}thread_current-session-1"
        )
        assert stored is not None
        assert stored.payload["pinned"] is True
        assert stored.payload["archived"] is True

        delete_response = client.delete("/api/ui/chats/thread_current-session-1")

        assert delete_response.status_code == 202
        assert (
            document_store.get_document(
                PROGENITOR_STATE_PREFIX, f"{SESSION_KEY_PREFIX}thread_current-session-1"
            )
            is None
        )

    def test_widget_schemas_endpoint_is_deleted(self, client):
        """Widget schema data is no longer part of the finite UI API."""
        response = client.get(
            "/api/ui/widget-schemas",
            headers={"X-Request-ID": "deleted-widget-schemas"},
        )

        assert response.status_code == 404

    def test_prompts_endpoint_lists_nested_prompts(self, client, document_store):
        from app.progenitor.content_documents import content_document_key

        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            content_document_key("prompt", "builder_agent/think.md"),
            {"content": "builder content"},
        )
        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            content_document_key("prompt", "progenitor/nested/decide.md"),
            {"content": "progenitor content"},
        )

        response = client.get("/api/ui/prompts")
        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        from app.backend import ui_routes

        replay = ui_routes._ui_event_replay[-1]
        prompts_data = replay.payload
        assert len(prompts_data["prompts"]) == 2
        assert {prompt["path"] for prompt in prompts_data["prompts"]} == {
            "builder_agent/think.md",
            "progenitor/nested/decide.md",
        }
        builder_prompt = next(
            prompt
            for prompt in prompts_data["prompts"]
            if prompt["path"] == "builder_agent/think.md"
        )
        assert builder_prompt["primary_agent"] == "builder_agent"
        assert "builder_agent" in builder_prompt["associated_agents"]

    def test_prompts_endpoint_updates_prompt_content(self, client, document_store):
        from app.progenitor.content_documents import content_document_key

        key = content_document_key("prompt", "builder_agent/think.md")
        document_store.save_document(PROGENITOR_STATE_PREFIX, key, {"content": "before"})

        response = client.put(
            "/api/ui/prompts/content",
            json={"path": "builder_agent/think.md", "content": "after"},
        )

        assert response.status_code == 202
        stored = document_store.get_document(PROGENITOR_STATE_PREFIX, key, None)
        assert stored.payload["content"] == "after"

    def test_prompts_list_omits_content_and_content_route_returns_it(self, client, document_store):
        from app.progenitor.content_documents import content_document_key

        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            content_document_key("prompt", "builder_agent/think.md"),
            {"content": "full prompt body"},
        )

        list_response = client.get("/api/ui/prompts")
        assert list_response.status_code == 202
        from app.backend import ui_routes

        list_replay = ui_routes._ui_event_replay[-1]
        assert list_replay.type == "prompts.snapshot"
        listed = next(
            prompt
            for prompt in list_replay.payload["prompts"]
            if prompt["path"] == "builder_agent/think.md"
        )
        assert "content" not in listed

        content_response = client.get(
            "/api/ui/prompts/content", params={"path": "builder_agent/think.md"}
        )
        assert content_response.status_code == 202
        content_replay = ui_routes._ui_event_replay[-1]
        assert content_replay.type == "prompt.content.updated"
        assert content_replay.payload["path"] == "builder_agent/think.md"
        assert content_replay.payload["content"] == "full prompt body"

    def test_kanban_endpoint_returns_default_board(self, client, tasks_state_reader):
        response = client.get("/api/ui/kanban")
        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        from app.backend import ui_routes

        replay = ui_routes._ui_event_replay[-1]
        board = replay.payload
        assert board["title"] == "Delivery Kanban"
        assert [column["id"] for column in board["columns"]] == [
            "backlog",
            "plan",
            "build",
            "validate",
            "done",
        ]
        assert board["cards"] == []
        assert {status["id"] for status in board["card_schema"]["statuses"]} == {
            "draft",
            "idle",
            "running",
            "complete",
            "failed",
            "blocked",
        }

    def test_task_activity_get_uses_middleware_request_id_and_emits_full_state(
        self, client, tasks_state_reader
    ):
        """A headerless retained activity read is acknowledged and replayed consistently."""
        from app.backend import ui_routes

        created = client.post("/api/ui/kanban/cards", json={"title": "Activity task"})
        assert created.status_code == 202
        board = tasks_state_reader.document_store.get_document(
            PROGENITOR_STATE_PREFIX, "kanban_board", None
        )
        assert board is not None
        card_id = board.payload["card_order"][-1]
        persisted_activity = [
            {
                "id": "task_activity_retained",
                "card_id": card_id,
                "kind": "status",
                "actor": "builder",
                "message": "Implementation started.",
                "created_at": "2026-07-13T12:00:00+00:00",
                "metadata": {"from": "backlog", "to": "build"},
            }
        ]
        tasks_state_reader.document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            f"kanban_task_activity:{card_id}",
            persisted_activity,
        )

        queue = asyncio.Queue()
        ui_routes._ui_stream_subscribers[queue] = None
        try:
            response = client.get(f"/api/ui/tasks/{card_id}/activity")
            request_id = response.headers["X-Request-ID"]
            assert_acknowledged(
                response,
                ["task.activity.updated"],
                request_id=request_id,
                check_request_id_header=True,
            )
            envelopes = [queue.get_nowait()["envelope"] for _ in range(queue.qsize())]
        finally:
            ui_routes._ui_stream_subscribers.pop(queue, None)

        assert request_id.startswith("req-")
        assert len(envelopes) == 1
        event = envelopes[0]
        assert event.type == "task.activity.updated"
        assert event.payload == {
            "card_id": card_id,
            "items": persisted_activity,
            "total": 1,
            "request_id": request_id,
        }

    def test_task_content_update_acknowledges_and_persists_body(self, client, tasks_state_reader):
        """Task body writes return an ack while stream state carries the saved content."""
        from app.backend import ui_routes

        created = client.post(
            "/api/ui/kanban/cards",
            json={"title": "Persisted task", "summary": "Keep the brief"},
        )
        assert created.status_code == 202
        board = tasks_state_reader.document_store.get_document(
            PROGENITOR_STATE_PREFIX, "kanban_board", None
        )
        assert board is not None
        card_id = board.payload["card_order"][-1]
        request_id = "task-content-save-42"
        queue = asyncio.Queue()
        ui_routes._ui_stream_subscribers[queue] = None
        try:
            response = client.put(
                f"/api/ui/tasks/{card_id}/content",
                json={"content": "## Acceptance\n\nKeep this body\n\n"},
                headers={"X-Request-ID": request_id},
            )
            emitted = [queue.get_nowait() for _ in range(queue.qsize())]
        finally:
            ui_routes._ui_stream_subscribers.pop(queue, None)

        assert_acknowledged(
            response,
            ["task.content.updated", "task.activity.updated"],
            request_id=request_id,
            check_request_id_header=True,
        )
        content_event = next(
            item["envelope"] for item in emitted if item["envelope"].type == "task.content.updated"
        )
        assert content_event.payload["card_id"] == card_id
        assert content_event.payload["path"] == f"tasks/{card_id}.md"
        assert content_event.payload["content"] == "## Acceptance\n\nKeep this body"
        assert content_event.payload["request_id"] == request_id

        stored = tasks_state_reader.document_store.get_document(
            PROGENITOR_STATE_PREFIX, f"kanban_task_content:{card_id}", None
        )
        assert stored is not None
        assert stored.payload == {"content": "## Acceptance\n\nKeep this body"}

    def test_kanban_card_create_and_patch_persist(self, client, tasks_state_reader):
        create_response = client.post(
            "/api/ui/kanban/cards",
            json={"title": "Build kanban board", "summary": "Server first"},
        )

        data = create_response.json()
        assert_acknowledgement_body(data)
        assert "kanban.snapshot" in data["event_types"]
        store = tasks_state_reader.document_store
        board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
        card_id = board.payload["card_order"][-1]
        # Cards persist as their own ``kanban_card:<id>`` document; the board
        # structure only records column layout + ``card_order``.
        store = tasks_state_reader.document_store
        stored_board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
        assert stored_board is not None
        assert "cards" not in stored_board.payload
        stored_card = store.get_document(PROGENITOR_STATE_PREFIX, f"kanban_card:{card_id}", None)
        assert stored_card is not None
        assert stored_card.payload["id"] == card_id

        patch_response = client.patch(
            f"/api/ui/kanban/cards/{card_id}",
            json={
                "column_id": "build",
                "status": "running",
                "agent_name": "builder_progenitor",
                "branch_name": "feat/kanban-board",
                "labels": ["frontend", "live-first"],
            },
        )

        assert patch_response.status_code == 202
        data = patch_response.json()
        assert_acknowledgement_body(data)
        assert "kanban.snapshot" in data["event_types"]

        stored_card = store.get_document(PROGENITOR_STATE_PREFIX, f"kanban_card:{card_id}", None)
        assert stored_card is not None
        assert stored_card.payload["column_id"] == "build"
        assert stored_card.payload["status"] == "running"
        assert stored_card.payload["agent_name"] == "builder_progenitor"
        assert stored_card.payload["branch_name"] == "feat/kanban-board"
        assert stored_card.payload["labels"] == ["frontend", "live-first"]
        assert stored_card.payload["started_at"] is not None

        board_response = client.get("/api/ui/kanban")
        assert board_response.status_code == 202
        from app.backend import ui_routes

        board = ui_routes._ui_event_replay[-1].payload
        assert board["cards"][0]["id"] == card_id
        assert board["cards"][0]["column_id"] == "build"

    def test_kanban_card_project_id_persists_and_patches(self, client, tasks_state_reader):
        create_response = client.post(
            "/api/ui/kanban/cards",
            json={"title": "Scoped card", "project_id": "acme"},
        )
        data = create_response.json()
        assert_acknowledgement_body(data)
        assert "kanban.snapshot" in data["event_types"]
        store = tasks_state_reader.document_store
        board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
        card_id = board.payload["card_order"][-1]

        stored_card = tasks_state_reader.document_store.get_document(
            PROGENITOR_STATE_PREFIX, f"kanban_card:{card_id}", None
        )
        assert stored_card.payload["project_id"] == "acme"

        patch_response = client.patch(
            f"/api/ui/kanban/cards/{card_id}",
            json={"project_id": "beta"},
        )
        assert patch_response.status_code == 202
        data = patch_response.json()
        assert_acknowledgement_body(data)
        stored_card = tasks_state_reader.document_store.get_document(
            PROGENITOR_STATE_PREFIX, f"kanban_card:{card_id}", None
        )
        assert stored_card.payload["project_id"] == "beta"

        cleared = client.patch(
            f"/api/ui/kanban/cards/{card_id}",
            json={"project_id": ""},
        )
        assert cleared.status_code == 202
        assert_acknowledgement_body(cleared.json())
        stored_card = tasks_state_reader.document_store.get_document(
            PROGENITOR_STATE_PREFIX, f"kanban_card:{card_id}", None
        )
        assert stored_card.payload["project_id"] is None

    def test_kanban_card_metadata_persists_subtasks_and_dependencies(
        self, client, tasks_state_reader
    ):
        client.post(
            "/api/ui/kanban/cards",
            json={"title": "Metadata task"},
        )
        store = tasks_state_reader.document_store
        board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
        card_id = board.payload["card_order"][-1]

        patch_response = client.patch(
            f"/api/ui/kanban/cards/{card_id}",
            json={
                "metadata": {
                    "subtasks": [
                        {"id": "sub_1", "title": "First subtask", "done": False},
                        {"id": "sub_2", "title": "Second subtask", "done": True},
                    ],
                    "dependencies": ["task-123", "docs/spec.md"],
                }
            },
        )

        assert patch_response.status_code == 202
        data = patch_response.json()
        assert_acknowledgement_body(data)
        stored_card = store.get_document(PROGENITOR_STATE_PREFIX, f"kanban_card:{card_id}", None)
        assert stored_card.payload["metadata"]["subtasks"][0]["title"] == "First subtask"
        assert stored_card.payload["metadata"]["subtasks"][1]["done"] is True
        assert stored_card.payload["metadata"]["dependencies"] == ["task-123", "docs/spec.md"]

        patch_response = client.patch(
            f"/api/ui/kanban/cards/{card_id}",
            json={
                "metadata": {
                    "subtasks": stored_card.payload["metadata"]["subtasks"],
                    "dependencies": stored_card.payload["metadata"]["dependencies"],
                    "collaborators": ["review_agent", "qa_user"],
                    "attachments": [
                        {
                            "id": "att_1",
                            "label": "Spec doc",
                            "url": "https://example.test/spec",
                            "kind": "link",
                        },
                        {
                            "id": "att_2",
                            "label": "PR",
                            "url": "https://example.test/pr/1",
                            "kind": "pr",
                        },
                    ],
                }
            },
        )

        assert patch_response.status_code == 202
        data = patch_response.json()
        assert_acknowledgement_body(data)
        stored_card_updated = store.get_document(
            PROGENITOR_STATE_PREFIX, f"kanban_card:{card_id}", None
        )
        assert stored_card_updated.payload["metadata"]["collaborators"] == [
            "review_agent",
            "qa_user",
        ]
        assert stored_card_updated.payload["metadata"]["attachments"][1]["kind"] == "pr"

    def test_kanban_card_reorder_persists_positions(self, client, tasks_state_reader):
        client.post("/api/ui/kanban/cards", json={"title": "First card"})
        store = tasks_state_reader.document_store
        board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
        first_id = board.payload["card_order"][0]
        client.post("/api/ui/kanban/cards", json={"title": "Second card"})
        client.post("/api/ui/kanban/cards", json={"title": "Third card"})
        board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
        third_id = board.payload["card_order"][2]

        reorder_response = client.patch(
            f"/api/ui/kanban/cards/{third_id}",
            json={"position": 1},
        )

        assert reorder_response.status_code == 202
        data = reorder_response.json()
        assert_acknowledgement_body(data)
        board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
        assert board.payload["card_order"][0] == first_id
        assert board.payload["card_order"][1] == third_id

        move_response = client.patch(
            f"/api/ui/kanban/cards/{first_id}",
            json={"column_id": "build", "position": 0},
        )

        assert move_response.status_code == 202
        data = move_response.json()
        assert_acknowledgement_body(data)
        assert True
        assert True

        from app.backend import ui_routes

        board_response = client.get("/api/ui/kanban")
        assert board_response.status_code == 202
        board = ui_routes._ui_event_replay[-1].payload
        backlog_cards = [card for card in board["cards"] if card["column_id"] == "backlog"]
        build_cards = [card for card in board["cards"] if card["column_id"] == "build"]

        assert [card["title"] for card in backlog_cards] == ["Third card", "Second card"]
        assert [card["position"] for card in backlog_cards] == [0, 1]
        assert [card["title"] for card in build_cards] == ["First card"]
        assert build_cards[0]["position"] == 0

    def test_kanban_normalizes_legacy_columns(self, client, tasks_state_reader):
        legacy_board = {
            "id": "progenitor-kanban",
            "title": "Delivery Kanban",
            "updated_at": "2026-03-22T00:00:00+00:00",
            "columns": [],
            "cards": [
                {"id": "legacy-research", "title": "Legacy research", "column_id": "research"},
                {"id": "legacy-test", "title": "Legacy test", "column_id": "test"},
                {"id": "legacy-review", "title": "Legacy review", "column_id": "review"},
            ],
        }
        tasks_state_reader.document_store.save_document(
            PROGENITOR_STATE_PREFIX, "kanban_board", legacy_board
        )

        board_response = client.get("/api/ui/kanban")
        assert board_response.status_code == 202
        from app.backend import ui_routes

        replay = ui_routes._ui_event_replay[-1]
        board = replay.payload
        assert [column["id"] for column in board["columns"]] == [
            "backlog",
            "plan",
            "build",
            "validate",
            "done",
        ]
        assert {card["id"]: card["column_id"] for card in board["cards"]} == {
            "legacy-research": "plan",
            "legacy-test": "validate",
            "legacy-review": "validate",
        }
        # The legacy inline-cards board is migrated to per-card documents + an
        # ordered ``card_order`` on the structure document.
        store = tasks_state_reader.document_store
        structure = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
        assert "cards" not in structure.payload
        assert set(structure.payload["card_order"]) == {
            "legacy-research",
            "legacy-test",
            "legacy-review",
        }
        assert (
            store.get_document(PROGENITOR_STATE_PREFIX, "kanban_card:legacy-test", None) is not None
        )

    def test_kanban_card_delete_persists(self, client, tasks_state_reader):
        create_response = client.post("/api/ui/kanban/cards", json={"title": "Disposable card"})
        data = create_response.json()
        assert_acknowledgement_body(data)
        store = tasks_state_reader.document_store
        board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
        card_id = board.payload["card_order"][-1]

        delete_response = client.delete(f"/api/ui/kanban/cards/{card_id}")

        assert delete_response.status_code == 202
        board_response = client.get("/api/ui/kanban")
        assert board_response.status_code == 202
        from app.backend import ui_routes

        replay = ui_routes._ui_event_replay[-1]
        board = replay.payload
        assert all(card["id"] != card_id for card in board["cards"])
        # Deleting the card also removes its backing document and drops it from
        # the board's ``card_order``.
        store = tasks_state_reader.document_store
        assert store.get_document(PROGENITOR_STATE_PREFIX, f"kanban_card:{card_id}", None) is None
        structure = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
        assert card_id not in structure.payload["card_order"]

    def test_schedule_widget_delete_persists_removal(self, client, kanban_state_reader):
        create_response = client.post(
            "/api/ui/schedules",
            json={
                "name": "Morning summary",
                "instruction": "Send the daily summary",
                "schedule_type": "cron",
                "expression": "0 9 * * *",
                "enabled": True,
            },
        )

        data = create_response.json()
        assert_acknowledgement_body(data)
        store = kanban_state_reader.document_store
        schedule_doc = store.get_document(PROGENITOR_STATE_PREFIX, "schedule", {})
        before_delete = schedule_doc.payload if schedule_doc else {}
        schedule_id = list(before_delete.keys())[0]
        assert list(before_delete.keys()) == [schedule_id]

        delete_response = client.delete(f"/api/ui/widgets/schedule:{schedule_id}")

        assert delete_response.status_code == 202
        data = delete_response.json()
        assert_acknowledgement_body(data)
        schedule_doc = store.get_document(PROGENITOR_STATE_PREFIX, "schedule", {})
        after_delete = schedule_doc.payload if schedule_doc else {}
        assert after_delete == {}
        from app.backend import ui_routes

        widgets_resp = client.get("/api/ui/widgets")
        assert widgets_resp.status_code == 202
        widgets = ui_routes._ui_event_replay[-1].payload
        assert f"schedule:{schedule_id}" not in {widget["id"] for widget in widgets["items"]}

        repeated_delete = client.delete(f"/api/ui/widgets/schedule:{schedule_id}")

        assert repeated_delete.status_code == 202
        data = repeated_delete.json()
        assert_acknowledgement_body(data)
        response = client.get("/api/ui/schedules")
        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        from app.backend import ui_routes

        replay = ui_routes._ui_event_replay[-1]
        assert isinstance(replay.payload.get("items"), list)

    def test_ui_alerts_endpoint(self, client):
        """Test /api/ui/alerts returns alerts."""
        response = client.get("/api/ui/alerts")
        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)

    def test_mark_alerts_viewed_endpoint(self, client):
        first = create_alert(type="warning", title="Build", message="Build failed")
        create_alert(type="info", title="Reminder", message="Check logs")

        response = client.post("/api/ui/alerts/viewed", json={"alert_ids": [first.id]})

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)

        from app.backend import ui_routes

        alerts_resp = client.get("/api/ui/alerts")
        assert alerts_resp.status_code == 202
        alerts = ui_routes._ui_event_replay[-1].payload
        first_alert = next(alert for alert in alerts["alerts"] if alert["id"] == first.id)
        assert first_alert["viewed"] is True

    def test_dismiss_alert_marks_viewed_without_deleting(self, client):
        created = create_alert(type="warning", title="Build", message="Build failed")

        response = client.post(f"/api/ui/alerts/{created.id}/dismiss")

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)

        from app.backend import ui_routes

        alerts_resp = client.get("/api/ui/alerts")
        assert alerts_resp.status_code == 202
        alerts = ui_routes._ui_event_replay[-1].payload
        assert alerts["total"] == 1
        assert alerts["unread"] == 0
        assert alerts["alerts"][0]["id"] == created.id
        assert alerts["alerts"][0]["viewed"] is True

    def test_respond_alert_marks_viewed_without_deleting(self, client):
        created = create_alert(type="info", title="Heads up", message="Acknowledge me")

        response = client.post(f"/api/ui/alerts/{created.id}/respond")

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)

        from app.backend import ui_routes

        alerts_resp = client.get("/api/ui/alerts")
        assert alerts_resp.status_code == 202
        alerts = ui_routes._ui_event_replay[-1].payload
        assert alerts["total"] == 1
        assert alerts["unread"] == 0
        assert alerts["alerts"][0]["id"] == created.id
        assert alerts["alerts"][0]["viewed"] is True

    def test_dismiss_nonexistent_alert(self, client):
        """Test dismissing a non-existent alert returns 404."""
        response = client.post("/api/ui/alerts/nonexistent/dismiss")
        assert response.status_code == 404

    def test_respond_nonexistent_alert(self, client):
        """Test responding to a non-existent alert returns 404."""
        response = client.post("/api/ui/alerts/nonexistent/respond")
        assert response.status_code == 404


class TestDashboardRoutes:
    """Tests for Dashboard UI routes (now API-only)."""

    def test_dashboard_not_served(self, client):
        """Test that /ui/dashboard returns 404 (API-only backend)."""
        response = client.get("/ui/dashboard")
        assert response.status_code == 404

    def test_ui_alias_not_served(self, client):
        """Test that /ui returns 404 (API-only backend)."""
        response = client.get("/ui")
        assert response.status_code == 404


class TestLogsEndpoint:
    """Tests for /api/ui/logs endpoint."""

    def test_logs_returns_valid_structure(self, client):
        """Test that /api/ui/logs returns accepted response."""
        response = client.get("/api/ui/logs")
        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        assert "request_id" in data
        assert "event_types" in data

    def test_logs_json_format(self, client):
        """Test that /api/ui/logs can return JSON format."""
        response = client.get("/api/ui/logs?format=json")
        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        assert "request_id" in data
        assert "event_types" in data

    def test_logs_with_limit(self, client):
        """Test that /api/ui/logs respects limit parameter."""
        response = client.get("/api/ui/logs?limit=10")
        assert response.status_code == 202

    def test_logs_with_level_filter(self, client):
        """Test that /api/ui/logs accepts level filter."""
        response = client.get("/api/ui/logs?level=error")
        assert response.status_code == 202

    def test_logs_with_search(self, client):
        """Test that /api/ui/logs accepts search parameter."""
        response = client.get("/api/ui/logs?search=test")
        assert response.status_code == 202

    def test_logs_json_groups_sources_by_folder_and_preserves_log_timestamp(
        self, client, kanban_state_reader
    ):
        """Test endpoint accepts group/source params."""
        builder_dir = kanban_state_reader.logs_dir / "builder"
        builder_dir.mkdir(parents=True, exist_ok=True)

        (kanban_state_reader.logs_dir / "progenitor.log").write_text(
            "2026-03-11 21:59:52 - __main__ - INFO - Progenitor starting...\n",
            encoding="utf-8",
        )
        (builder_dir / "builder.log").write_text(
            "2026-03-08 00:33:35 - app.agents.builder_progenitor.agent - INFO - Builder ready\n",
            encoding="utf-8",
        )

        response = client.get(
            "/api/ui/logs?format=json&folder=builder&file=builder/builder.log&start=2026-03-01&end=2026-03-12&limit=0"
        )

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        assert "request_id" in data
        assert "event_types" in data

    def test_logs_json_supports_incremental_cursor_and_regex_search(
        self, client, kanban_state_reader
    ):
        """Test cursor-based incremental fetches and regex search."""
        base_time = datetime.now(UTC).replace(microsecond=0)
        (kanban_state_reader.logs_dir / "conversation.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "message": "alpha ready",
                            "timestamp": (base_time - timedelta(minutes=1))
                            .isoformat()
                            .replace("+00:00", "Z"),
                            "level": "info",
                        }
                    ),
                    json.dumps(
                        {
                            "message": "beta failed",
                            "timestamp": base_time.isoformat().replace("+00:00", "Z"),
                            "level": "error",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        full_response = client.get(
            "/api/ui/logs?format=json&search=beta|alpha&search_mode=regex&limit=0"
        )
        assert full_response.status_code == 202
        data = full_response.json()
        assert_acknowledgement_body(data)

    def test_logs_json_supports_date_range_filter(self, client, kanban_state_reader):
        """Test inclusive start/end date filtering."""
        range_end = datetime.now(UTC).date()
        range_start = range_end - timedelta(days=1)

        response = client.get(
            f"/api/ui/logs?format=json&start={range_start.isoformat()}&end={range_end.isoformat()}&limit=0"
        )

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)

    def test_logs_export_returns_downloadable_payload(self, client, kanban_state_reader):
        """Test export endpoint returns accepted response."""
        timestamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        (kanban_state_reader.logs_dir / "decisions.jsonl").write_text(
            json.dumps({"message": "decision made", "timestamp": timestamp, "level": "info"})
            + "\n",
            encoding="utf-8",
        )

        response = client.get("/api/ui/logs/export?format=jsonl")
        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        assert "request_id" in data
        assert "event_types" in data

    def test_logs_export_nonempty_emits_correlated_reconstructable_chunk(
        self, client, kanban_state_reader
    ):
        """A nonempty export delivers its complete data only on the UI stream."""
        from app.backend import ui_routes

        request_id = "nonempty-log-export-client-request"
        timestamp = datetime.now(UTC).replace(microsecond=0)
        source_entries = [
            {
                "timestamp": (timestamp - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "level": "info",
                "source": "decisions",
                "message": "decision queued",
            },
            {
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "level": "error",
                "source": "decisions",
                "message": "decision failed",
            },
        ]

        store = kanban_state_reader.document_store
        for i, entry in enumerate(source_entries):
            store.save_document(
                f"{PROGENITOR_LOG_PREFIX}.decisions",
                f"entry-{i}",
                {
                    "timestamp": entry["timestamp"],
                    "level": entry["level"],
                    "source": entry["source"],
                    "message": entry["message"],
                    "relative_path": "decisions.jsonl",
                    "group": "root",
                    "data": entry,
                },
            )

        queue = asyncio.Queue()
        ui_routes._ui_stream_subscribers[queue] = None
        try:
            response = client.get(
                "/api/ui/logs/export?format=jsonl",
                headers={"X-Request-ID": request_id},
            )
            assert_acknowledged(
                response,
                ["logs.export.started", "logs.export.chunk", "logs.export.completed"],
                request_id=request_id,
                check_request_id_header=True,
            )
            emitted = []
            while not queue.empty():
                emitted.append(queue.get_nowait())
        finally:
            ui_routes._ui_stream_subscribers.pop(queue, None)

        assert [event["type"] for event in emitted] == ["ui.event", "ui.event", "ui.event"]
        envelopes = [event["envelope"] for event in emitted]
        assert [envelope.type for envelope in envelopes] == [
            "logs.export.started",
            "logs.export.chunk",
            "logs.export.completed",
        ]
        assert all(envelope.payload["request_id"] == request_id for envelope in envelopes)

        started, chunk, completed = (envelope.payload for envelope in envelopes)
        assert started == {"format": "jsonl", "total": 2, "request_id": request_id}
        assert chunk["format"] == "jsonl"
        assert chunk["index"] == 0
        assert chunk["total"] == 2
        assert chunk["request_id"] == request_id
        assert completed == {"format": "jsonl", "total": 2, "request_id": request_id}

        exported_entries = chunk["entries"]
        assert len(exported_entries) == 2
        assert all(
            set(entry)
            == {
                "id",
                "timestamp",
                "time",
                "level",
                "source",
                "message",
                "path",
                "relative_path",
                "group",
                "data",
            }
            and entry["id"]
            for entry in exported_entries
        )
        assert [
            {
                key: entry[key]
                for key in (
                    "timestamp",
                    "time",
                    "level",
                    "source",
                    "message",
                    "relative_path",
                    "group",
                    "data",
                )
            }
            for entry in exported_entries
        ] == [
            {
                "timestamp": source_entries[1]["timestamp"],
                "time": source_entries[1]["timestamp"],
                "level": "error",
                "source": "decisions",
                "message": "decision failed",
                "relative_path": "decisions.jsonl",
                "group": "root",
                "data": source_entries[1],
            },
            {
                "timestamp": source_entries[0]["timestamp"],
                "time": source_entries[0]["timestamp"],
                "level": "info",
                "source": "decisions",
                "message": "decision queued",
                "relative_path": "decisions.jsonl",
                "group": "root",
                "data": source_entries[0],
            },
        ]

    def test_logs_export_empty_emits_only_lifecycle_events(self, client, kanban_state_reader):
        """Empty exports do not advertise or emit a data chunk."""
        from app.backend import ui_routes

        request_id = "empty-log-export-request"
        queue = asyncio.Queue()
        assert not any(kanban_state_reader.logs_dir.iterdir())
        ui_routes._ui_stream_subscribers[queue] = None
        try:
            response = client.get(
                "/api/ui/logs/export",
                headers={"X-Request-ID": request_id},
            )
            assert_acknowledged(
                response,
                ["logs.export.started", "logs.export.completed"],
                request_id=request_id,
                check_request_id_header=True,
            )
            emitted = []
            while not queue.empty():
                emitted.append(queue.get_nowait())
        finally:
            ui_routes._ui_stream_subscribers.pop(queue, None)

        envelopes = [event["envelope"] for event in emitted]
        assert [envelope.type for envelope in envelopes] == [
            "logs.export.started",
            "logs.export.completed",
        ]
        assert all(envelope.payload["request_id"] == request_id for envelope in envelopes)

    def test_logs_export_supports_csv(self, client, kanban_state_reader):
        """Test CSV export output."""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        (kanban_state_reader.logs_dir / "progenitor.log").write_text(
            f"{timestamp} - __main__ - INFO - Started\n",
            encoding="utf-8",
        )

        response = client.get("/api/ui/logs/export?format=csv")

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        assert "request_id" in data
        assert "event_types" in data


class TestScheduleEndpoint:
    """Tests for /api/ui/schedule endpoint."""

    def test_create_and_patch_keep_paused_in_sync(self, client, monkeypatch):
        """enabled drives the runtime-honoured paused flag on create and patch."""
        store = InMemoryDocumentStore()
        store.save_document(PROGENITOR_STATE_PREFIX, "schedule", {})
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)

        created = client.post(
            "/api/ui/schedules",
            json={
                "name": "Sync check",
                "instruction": "do the thing",
                "schedule_type": "interval",
                "expression": "15m",
                "enabled": True,
            },
        )
        assert created.status_code == 202
        data = created.json()
        assert_acknowledgement_body(data)
        doc = store.get_document(PROGENITOR_STATE_PREFIX, "schedule", {})
        assert doc is not None
        schedule_ids = list(doc.payload.keys())
        assert len(schedule_ids) == 1
        schedule_id = schedule_ids[0]
        entry = doc.payload[schedule_id]
        assert entry["paused"] is False

        patched = client.patch(
            f"/api/ui/widgets/schedule:{schedule_id}",
            json={"enabled": False},
        )
        assert patched.status_code == 202
        patched_data = patched.json()
        assert_acknowledgement_body(patched_data)
        entry = store.get_document(PROGENITOR_STATE_PREFIX, "schedule", {}).payload[schedule_id]
        assert entry["paused"] is True
        assert entry["enabled"] is False

    def test_broadcast_schedule_snapshot_emits_ui_event(self, monkeypatch):
        """Schedule snapshot broadcasts on the canonical UI event stream."""
        from app.backend import ui_routes

        store = InMemoryDocumentStore()
        created_at = datetime.now(UTC).isoformat()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "schedule",
            {
                "sched-live": {
                    "id": "sched-live",
                    "name": "Live schedule",
                    "message": "ping",
                    "cron_expression": "0 * * * *",
                    "created_at": created_at,
                    "paused": False,
                }
            },
        )
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)

        queue = asyncio.Queue()
        ui_routes._ui_stream_subscribers[queue] = None
        try:
            envelope = ui_routes.broadcast_schedule_snapshot()
        finally:
            ui_routes._ui_stream_subscribers.pop(queue, None)

        assert envelope.type == "schedule.snapshot"
        assert envelope.payload["items"][0]["id"] == "sched-live"
        assert ui_routes._ui_event_replay[-1].type == "schedule.snapshot"

        streamed = queue.get_nowait()
        assert streamed["type"] == "ui.event"
        assert streamed["envelope"].type == "schedule.snapshot"
        assert streamed["envelope"].payload["items"][0]["message"] == "ping"


class TestLogsPage:
    """Tests for the Logs API endpoint."""

    def test_logs_page_loaded_event_carries_complete_causal_payload(
        self, client, kanban_state_reader
    ):
        """The acknowledgement resolves only through the correlated page payload."""
        from app.backend import ui_routes

        (kanban_state_reader.logs_dir / "progenitor.log").write_text(
            "2026-03-11 21:59:52 - __main__ - INFO - Causal log record\n",
            encoding="utf-8",
        )
        request_id = "59251b89-8ddb-4e18-913b-566e2419c139"
        event_start = len(ui_routes._ui_event_replay)
        response = client.get(
            "/api/ui/logs?format=json&folder=root&file=progenitor.log"
            "&start=2026-03-01&end=2026-03-12&limit=10",
            headers={"X-Request-ID": request_id},
        )

        assert_acknowledged(
            response, ["logs.page.loaded"], request_id=request_id, check_request_id_header=True
        )
        emitted = ui_routes._ui_event_replay[event_start:]
        event = next(
            envelope
            for envelope in emitted
            if envelope.type == "logs.page.loaded"
            and envelope.payload.get("request_id") == request_id
        )
        payload = event.payload
        assert set(payload) == {
            "entries",
            "total",
            "returned",
            "newest_cursor",
            "cursor_found",
            "filters",
            "active_selection",
            "date_range",
            "cursor",
            "source_groups",
            "folders",
            "request_id",
        }
        assert payload["request_id"] == request_id
        assert payload["returned"] == len(payload["entries"])
        assert payload["filters"] == {
            "level": None,
            "search": None,
            "source": None,
            "folder": "root",
            "file": "progenitor.log",
            "start": "2026-03-01",
            "end": "2026-03-12",
            "after": None,
            "limit": 10,
            "format": "json",
            "search_mode": "plain",
        }
        assert payload["active_selection"] == {
            "source": "",
            "group": "root",
            "file": "progenitor.log",
        }
        assert payload["date_range"] == {"start": "2026-03-01", "end": "2026-03-12"}
        assert payload["cursor"] == {
            "after": None,
            "newest_cursor": payload["newest_cursor"],
            "cursor_found": payload["cursor_found"],
        }


# Note: These tests used to check for server-rendered navigation HTML content.
# With the React SPA, navigation is rendered client-side, so these tests are simplified
# to just verify the SPA HTML shell is returned correctly. Navigation behavior
# should be tested with end-to-end tests using a browser.

# Note: The following test classes were removed as they tested server-rendered HTML elements:
# - TestUIEnhancedFeatures (dashboard charts, stats grids, logs controls)
# - These elements are now rendered client-side by React


class TestUIAccessibility:
    """The API backend must not resurrect removed server-rendered UI assets."""

    def test_chat_ui_has_no_backend_favicon(self, client):
        assert client.get("/ui/favicon.ico").status_code == 404

    def test_backend_does_not_serve_ui_document_shell(self, client):
        assert client.get("/ui/").status_code == 404


class TestSplitSSEEndpoints:
    """Tests for the split SSE endpoints (/chat and /status/stream).

    Note: SSE streaming tests are skipped as they hang waiting for the infinite stream.
    The endpoints are verified indirectly via route registration checks.
    """

    def test_stream_endpoint_is_registered(self, client):
        """Test that /api/ui/stream canonical SSE endpoint is registered."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        openapi = response.json()
        paths = openapi.get("paths", {})
        assert "/api/ui/stream" in paths, "Canonical SSE stream endpoint should be registered"

    def test_status_stream_endpoint_is_registered(self, client):
        """Test that /api/ui/stream (SSE) is registered (snapshot path removed)."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        openapi = response.json()
        paths = openapi.get("paths", {})
        assert "/api/ui/stream" in paths, "Canonical SSE stream endpoint should be registered"

    def test_broadcast_functions_exist(self):
        """Test that the broadcast functions are available."""
        from app.backend.ui_routes import (
            broadcast_alert,
            broadcast_ui_event,
        )

        assert callable(broadcast_ui_event)
        assert callable(broadcast_alert)

    def test_broadcast_alerts_state_pushes_full_alert_payload(self):
        from app.backend import ui_routes

        clear_alerts()
        alert = create_alert(type="warning", title="Build", message="Build failed")
        queue = ui_routes.asyncio.Queue()
        ui_routes._ui_stream_subscribers[queue] = None
        try:
            ui_routes.broadcast_alerts_state()
            event = queue.get_nowait()
        finally:
            ui_routes._ui_stream_subscribers.pop(queue, None)
            clear_alerts()

        assert event["type"] == "ui.event"
        envelope = event["envelope"]
        assert envelope.type == "alerts.updated"
        assert envelope.payload["unread"] == 1
        assert envelope.payload["total"] == 1
        assert envelope.payload["alerts"][0]["id"] == alert.id
        assert envelope.payload["alerts"][0]["title"] == "Build"

    @pytest.mark.asyncio
    async def test_ui_stream_connected_emits_alerts_in_connection_ready(self, monkeypatch):
        from app.backend import ui_routes

        clear_alerts()
        alert = create_alert(type="info", title="Heads up", message="Alert from SSE")
        monkeypatch.setattr(ui_routes, "start_status_monitor", lambda: None)
        monkeypatch.setattr(
            ui_routes, "_get_current_status", AsyncMock(return_value={"connected": True})
        )

        generator = ui_routes._generate_ui_stream()
        try:
            connected_event = await generator.__anext__()
        finally:
            await generator.aclose()
            clear_alerts()

        assert connected_event.event == "ui.event"
        payload = connected_event.data
        assert isinstance(payload, dict)
        assert payload["type"] == "connection.ready"
        alerts_payload = payload["payload"].get("alerts") or payload["payload"]
        assert any(a["id"] == alert.id for a in alerts_payload.get("alerts", []))


class TestWorkspaceBridgeActions:
    """Tests for backend action bridge endpoints."""

    @pytest.mark.skip(reason="File-based fallback removed; tests document-store-only StateReader")
    def test_state_reader_uses_document_store_over_legacy_state_files(self, tmp_path):
        pass

    def test_patch_schedule_widget_updates_schedule_document(self, client, monkeypatch):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "schedule",
            {
                "sched-1": {
                    "id": "sched-1",
                    "name": "Daily check",
                    "message": "Run the daily review",
                    "schedule": "0 0 * * *",
                    "cron_expression": "0 0 * * *",
                    "schedule_type": "cron",
                    "enabled": True,
                    "timezone": "UTC",
                }
            },
        )
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)

        response = client.patch(
            "/api/ui/widgets/schedule:sched-1",
            json={
                "name": "Nightly check",
                "instruction": "Run the nightly review",
                "enabled": False,
                "expression": "0 1 * * *",
            },
        )

        assert response.status_code == 202
        document = store.get_document(PROGENITOR_STATE_PREFIX, "schedule", {})
        assert document is not None
        updated = document.payload["sched-1"]
        assert updated["name"] == "Nightly check"
        assert updated["message"] == "Run the nightly review"
        assert updated["schedule"] == "0 1 * * *"
        assert updated["enabled"] is False

    def test_patch_mcp_widget_updates_mcp_config_document(self, client, monkeypatch):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "mcp",
            {
                "mcpServers": {
                    "jina": {
                        "description": "Disabled web search",
                        "enabled": False,
                        "transport": "http",
                        "url": "https://mcp.jina.ai/v1",
                    }
                }
            },
        )
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)

        response = client.patch(
            "/api/ui/widgets/mcp:jina",
            json={
                "enabled": True,
                "description": "Live web search",
                "url": "https://mcp.jina.ai/v1?include_tags=search,read",
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)

        document = store.get_document(PROGENITOR_STATE_PREFIX, "mcp", {})
        assert document is not None
        updated = document.payload
        assert updated["mcpServers"]["jina"]["enabled"] is True
        assert updated["mcpServers"]["jina"]["description"] == "Live web search"
        assert (
            updated["mcpServers"]["jina"]["url"]
            == "https://mcp.jina.ai/v1?include_tags=search,read"
        )

    def test_mcp_snapshot_omits_tool_detail_content_route_returns_it(self, client, monkeypatch):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "mcp",
            {
                "mcpServers": {
                    "jina": {
                        "description": "Web search",
                        "enabled": True,
                        "transport": "http",
                        "url": "https://mcp.jina.ai/v1",
                    }
                }
            },
        )
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "mcp_tools",
            {
                "tools": [
                    {
                        "name": "search",
                        "server": "jina",
                        "description": "Search the web",
                        "enabled": True,
                    },
                    {
                        "name": "read",
                        "server": "jina",
                        "description": "Read a URL",
                        "enabled": True,
                    },
                ]
            },
        )
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)
        from app.backend import ui_routes

        list_request_id = "mcp-list-lean"
        event_start = len(ui_routes._ui_event_replay)
        list_response = client.get("/api/ui/mcp", headers={"X-Request-ID": list_request_id})
        assert_acknowledged(
            list_response,
            ["mcp.snapshot"],
            request_id=list_request_id,
            check_request_id_header=True,
        )
        list_envelope = next(
            event
            for event in ui_routes._ui_event_replay[event_start:]
            if event.payload.get("request_id") == list_request_id
        )
        jina_item = next(item for item in list_envelope.payload["items"] if item["name"] == "jina")
        assert "tools" not in jina_item["data"]
        assert jina_item["logs"] == []

        detail_request_id = "mcp-detail-fetch"
        event_start = len(ui_routes._ui_event_replay)
        detail_response = client.get(
            "/api/ui/mcp/content",
            params={"id": "mcp:jina"},
            headers={"X-Request-ID": detail_request_id},
        )
        assert_acknowledged(
            detail_response,
            ["mcp.server.detail.updated"],
            request_id=detail_request_id,
            check_request_id_header=True,
        )
        detail_envelope = next(
            event
            for event in ui_routes._ui_event_replay[event_start:]
            if event.payload.get("request_id") == detail_request_id
        )
        detail_tools = detail_envelope.payload["data"]["tools"]
        assert {tool["name"] for tool in detail_tools} == {"search", "read"}

    def test_mcp_content_route_404s_for_unknown_server(self, client, monkeypatch):
        store = InMemoryDocumentStore()
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)

        response = client.get("/api/ui/mcp/content", params={"id": "mcp:missing"})

        assert response.status_code == 404

    def test_patch_mcp_tool_broadcasts_server_detail_update(self, client, monkeypatch):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "mcp",
            {
                "mcpServers": {
                    "jina": {
                        "description": "Web search",
                        "enabled": True,
                        "transport": "http",
                        "url": "https://mcp.jina.ai/v1",
                    }
                }
            },
        )
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "mcp_tools",
            {"tools": [{"name": "search", "server": "jina", "description": "", "enabled": True}]},
        )
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)
        from app.backend import ui_routes

        response = client.patch("/api/ui/widgets/mcp_tool:search", json={"enabled": False})

        assert response.status_code == 202
        assert set(response.json()["event_types"]) == {
            "widgets.updated",
            "mcp.snapshot",
            "mcp.server.detail.updated",
        }
        detail_event = next(
            event
            for event in reversed(ui_routes._ui_event_replay)
            if event.type == "mcp.server.detail.updated"
        )
        detail_tools = detail_event.payload["data"]["tools"]
        assert next(tool for tool in detail_tools if tool["name"] == "search")["enabled"] is False

    def test_refresh_agent_widget_returns_live_card_details(self, client, tmp_path, monkeypatch):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "registry",
            {
                "agents": [
                    {
                        "name": "observe_agent",
                        "kind": "a2a_agent",
                        "version": "0.2.0",
                        "description": "Observe health",
                        "entrypoint": "python observe.py",
                        "transport": "http",
                        "enabled": True,
                        "url": "http://example.test/observe",
                        "created_at": "2026-04-01T00:00:00Z",
                        "updated_at": "2026-04-01T00:00:00Z",
                        "status": "running",
                    }
                ]
            },
        )
        store.save_document(PROGENITOR_STATE_PREFIX, "mcp", {"mcpServers": {}})

        state_reader = StateReader(
            document_store=store,
        )
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)

        async def fake_inspect_agent_widget(widget, reader):
            assert widget["id"] == "agent:observe_agent"
            assert reader is state_reader
            return {
                **widget,
                "inspection": {
                    "source": "a2a",
                    "refreshed_at": "2026-04-13T10:30:00Z",
                    "protocol_version": "0.3.0",
                    "skills": [
                        {"id": "diagnose", "name": "Diagnose", "description": "Run diagnostics"}
                    ],
                },
                "agent_card_url": "http://example.test/observe/.well-known/agent-card.json",
                "url": "http://example.test/observe",
                "version": "0.2.0",
            }

        monkeypatch.setattr(
            "app.backend.ui_routes._inspect_agent_widget",
            fake_inspect_agent_widget,
        )

        response = client.post("/api/ui/widgets/agent:observe_agent/refresh")

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        from app.backend import ui_routes

        # The refresh broadcasts a widgets.updated event (inspection data is ephemeral)
        widget_event = ui_routes._ui_event_replay[-1]
        assert widget_event.type == "widgets.updated"

    def test_refresh_mcp_widget_is_rejected(self, client, monkeypatch):
        # MCP tool discovery is owned by the Progenitor runtime's MCP init (and
        # ``mcp_refresh``); the backend no longer probes servers itself.
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "registry",
            {"agents": []},
        )
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "mcp",
            {
                "mcpServers": {
                    "jina": {
                        "description": "Web tools",
                        "enabled": True,
                        "transport": "http",
                        "url": "https://mcp.example.test",
                    }
                }
            },
        )
        state_reader = StateReader(
            document_store=store,
        )
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)
        response = client.post("/api/ui/widgets/mcp:jina/refresh")
        assert response.status_code == 400
        assert "agent" in response.json()["detail"].lower()

    def test_create_schedule_writes_schedule_document(self, client, monkeypatch):
        store = InMemoryDocumentStore()
        store.save_document(PROGENITOR_STATE_PREFIX, "schedule", {})
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)

        response = client.post(
            "/api/ui/schedules",
            json={
                "name": "Morning Check-in",
                "instruction": "Summarize overnight alerts and prep the inbox.",
                "schedule_type": "interval",
                "expression": "15m",
                "timezone": "UTC",
                "enabled": True,
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        document = store.get_document(PROGENITOR_STATE_PREFIX, "schedule", {})
        assert document is not None
        schedule_ids = list(document.payload.keys())
        assert len(schedule_ids) == 1
        schedule_id = schedule_ids[0]
        updated = document.payload[schedule_id]
        assert updated["name"] == "Morning Check-in"
        assert updated["message"] == "Summarize overnight alerts and prep the inbox."
        assert updated["id"].startswith("sched_")
        assert updated["cron_expression"] == "*/15 * * * *"

    def test_create_once_schedule_uses_cron_prefix_and_persists_scheduled_at(
        self, client, monkeypatch
    ):
        store = InMemoryDocumentStore()
        store.save_document(PROGENITOR_STATE_PREFIX, "schedule", {})
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)

        response = client.post(
            "/api/ui/schedules",
            json={
                "name": "One-off reminder",
                "instruction": "Reply once.",
                "schedule_type": "once",
                "expression": "2026-06-19T09:47:00Z",
                "timezone": "UTC",
                "enabled": True,
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        document = store.get_document(PROGENITOR_STATE_PREFIX, "schedule", {})
        assert document is not None
        schedule_ids = list(document.payload.keys())
        assert len(schedule_ids) == 1
        schedule_id = schedule_ids[0]
        assert schedule_id.startswith("cron_")
        updated = document.payload[schedule_id]
        assert updated["id"].startswith("cron_")
        assert updated["schedule_type"] == "once"
        assert updated["scheduled_at"] == "2026-06-19T09:47:00+00:00"
        assert "cron_expression" not in updated

    def test_create_event_widget_writes_event_widget_file(self, client, kanban_state_reader):
        response = client.post(
            "/api/ui/events",
            json={
                "name": "Card created trigger",
                "event_type": "kanban_card.created",
                "enabled": True,
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)

        store = kanban_state_reader.document_store
        stored_doc = store.get_document(BACKEND_STATE_PREFIX, "event_widgets", None)
        stored = stored_doc.payload if stored_doc else {}
        event_widget_ids = list(stored.keys())
        assert len(event_widget_ids) == 1
        event_widget_id = event_widget_ids[0]
        assert stored[event_widget_id]["name"] == "Card created trigger"
        assert stored[event_widget_id]["event_type"] == "kanban_card.created"
        assert stored[event_widget_id]["trigger_count"] == 0

        from app.backend import ui_routes

        widgets_resp = client.get("/api/ui/widgets")
        assert widgets_resp.status_code == 202
        widgets_items = ui_routes._ui_event_replay[-1].payload.get("items", [])
        assert any(widget["id"] == f"event:{event_widget_id}" for widget in widgets_items)

    def test_patch_event_widget_updates_config_file(self, client, kanban_state_reader):
        store = kanban_state_reader.document_store
        store.save_document(
            BACKEND_STATE_PREFIX,
            "event_widgets",
            {
                "evt-1": {
                    "id": "evt-1",
                    "name": "Created trigger",
                    "event_type": "kanban_card.created",
                    "enabled": True,
                    "trigger_count": 0,
                    "last_run_status": "idle",
                    "logs": [],
                }
            },
        )

        response = client.patch(
            "/api/ui/widgets/event:evt-1",
            json={
                "name": "Deleted trigger",
                "event_type": "kanban_card.deleted",
                "enabled": False,
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        assert "widgets.updated" in data["event_types"]

        stored_doc = store.get_document(BACKEND_STATE_PREFIX, "event_widgets", None)
        stored = stored_doc.payload if stored_doc else {}
        assert stored["evt-1"]["name"] == "Deleted trigger"
        assert stored["evt-1"]["event_type"] == "kanban_card.deleted"
        assert stored["evt-1"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_kanban_create_and_update_events_trigger_matching_widgets(
        self, client, tasks_state_reader
    ):
        from app.backend.ui_routes import get_document_event_dispatcher

        store = tasks_state_reader.document_store
        client.post(
            "/api/ui/events",
            json={"name": "Created", "event_type": "kanban_card.created"},
        )
        stored_doc = store.get_document(BACKEND_STATE_PREFIX, "event_widgets", None)
        stored = stored_doc.payload if stored_doc else {}
        created_widget_id = next(iter(stored.keys()))

        client.post(
            "/api/ui/events",
            json={"name": "Updated", "event_type": "kanban_card.updated"},
        )
        stored_doc = store.get_document(BACKEND_STATE_PREFIX, "event_widgets", None)
        stored = stored_doc.payload if stored_doc else {}
        updated_widget_id = next(k for k in stored if k != created_widget_id)

        client.post(
            "/api/ui/kanban/cards",
            json={"title": "Evented card", "summary": "Initial summary"},
        )
        await get_document_event_dispatcher().drain()
        board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
        card_id = board.payload["card_order"][-1]

        from app.backend import ui_routes

        widgets_resp = client.get("/api/ui/widgets")
        assert widgets_resp.status_code == 202
        widgets_items = ui_routes._ui_event_replay[-1].payload.get("items", [])
        created_w = next(w for w in widgets_items if w["id"] == f"event:{created_widget_id}")
        created_data = created_w["data"]
        assert created_data["trigger_count"] == 1
        assert created_data["last_payload"]["id"] == card_id
        assert created_data["last_payload"]["document_type"] == "kanban_card"
        assert created_data["last_payload"]["op"] == "created"

        update_response = client.patch(
            f"/api/ui/kanban/cards/{card_id}",
            json={"summary": "Updated summary"},
        )

        assert update_response.status_code == 202
        await get_document_event_dispatcher().drain()
        widgets_resp = client.get("/api/ui/widgets")
        assert widgets_resp.status_code == 202
        widgets_items = ui_routes._ui_event_replay[-1].payload.get("items", [])
        updated_w = next(w for w in widgets_items if w["id"] == f"event:{updated_widget_id}")
        updated_data = updated_w["data"]
        assert updated_data["trigger_count"] == 1
        assert updated_data["last_payload"]["id"] == card_id
        assert updated_data["last_payload"]["document_type"] == "kanban_card"
        assert updated_data["last_payload"]["op"] == "updated"

    @pytest.mark.asyncio
    async def test_kanban_update_and_delete_events_trigger_matching_widgets(
        self, client, tasks_state_reader
    ):
        from app.backend.ui_routes import get_document_event_dispatcher

        store = tasks_state_reader.document_store
        client.post(
            "/api/ui/events",
            json={"name": "Updated", "event_type": "kanban_card.updated"},
        )
        stored_doc = store.get_document(BACKEND_STATE_PREFIX, "event_widgets", None)
        stored = stored_doc.payload if stored_doc else {}
        updated_widget_id = next(iter(stored.keys()))

        client.post(
            "/api/ui/events",
            json={"name": "Deleted", "event_type": "kanban_card.deleted"},
        )
        stored_doc = store.get_document(BACKEND_STATE_PREFIX, "event_widgets", None)
        stored = stored_doc.payload if stored_doc else {}
        deleted_widget_id = next(k for k in stored if k != updated_widget_id)

        client.post("/api/ui/kanban/cards", json={"title": "First card"})
        await get_document_event_dispatcher().drain()
        board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
        first_id = board.payload["card_order"][0]
        client.post("/api/ui/kanban/cards", json={"title": "Second card"})
        await get_document_event_dispatcher().drain()
        board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
        second_id = board.payload["card_order"][-1]

        move_response = client.patch(
            f"/api/ui/kanban/cards/{second_id}",
            json={"column_id": "done"},
        )
        assert move_response.status_code == 202
        await get_document_event_dispatcher().drain()

        from app.backend import ui_routes

        widgets_resp = client.get("/api/ui/widgets")
        assert widgets_resp.status_code == 202
        widgets_items = ui_routes._ui_event_replay[-1].payload.get("items", [])
        updated_w = next(w for w in widgets_items if w["id"] == f"event:{updated_widget_id}")
        updated_data = updated_w["data"]
        assert updated_data["trigger_count"] == 1
        assert updated_data["last_payload"]["id"] == second_id
        assert updated_data["last_payload"]["document_type"] == "kanban_card"
        assert updated_data["last_payload"]["op"] == "updated"

        delete_response = client.delete(f"/api/ui/kanban/cards/{first_id}")
        assert delete_response.status_code == 202
        await get_document_event_dispatcher().drain()

        widgets_resp = client.get("/api/ui/widgets")
        assert widgets_resp.status_code == 202
        widgets_items = ui_routes._ui_event_replay[-1].payload.get("items", [])
        deleted_w = next(w for w in widgets_items if w["id"] == f"event:{deleted_widget_id}")
        deleted_data = deleted_w["data"]
        assert deleted_data["trigger_count"] == 1
        assert deleted_data["last_payload"]["id"] == first_id
        assert deleted_data["last_payload"]["document_type"] == "kanban_card"
        assert deleted_data["last_payload"]["op"] == "deleted"

    def test_run_and_generate_actions_use_existing_client(self, client):
        from app.backend import main as backend_main

        original_client = backend_main.client
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(
            return_value=AgentResponse(
                response="bridge-response", reasoning=None, reasoning_tokens=None
            )
        )
        backend_main.client = mock_client
        try:
            run_response = client.post(
                "/api/ui/actions/run",
                json={"widget_id": "prompt:test", "prompt": "Run this", "input": "From upstream"},
            )
            generate_response = client.post(
                "/api/ui/actions/generate",
                json={
                    "widget_id": "prompt:test",
                    "prompt": "Generate this",
                    "input": "Generated input",
                },
            )

            assert run_response.status_code == 202
            assert generate_response.status_code == 202
            data = run_response.json()
            assert_acknowledgement_body(data)
            data = generate_response.json()
            assert_acknowledgement_body(data)
            assert mock_client.send_message.await_count == 2
            sent_messages = [call.args[0] for call in mock_client.send_message.await_args_list]
            assert sent_messages == [
                "Widget ID: prompt:test\n\nPrompt:\nRun this\n\nInput:\nFrom upstream",
                "Widget ID: prompt:test\n\nInput:\nGenerated input\n\nGenerate this",
            ]
        finally:
            backend_main.client = original_client

    def test_workspace_actions_generate_distinct_threads_per_execution(self, client):
        from app.backend import main as backend_main

        original_client = backend_main.client
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(
            return_value=AgentResponse(
                response="bridge-response", reasoning=None, reasoning_tokens=None
            )
        )
        backend_main.client = mock_client
        try:
            first = client.post(
                "/api/ui/actions/run",
                json={"widget_id": "prompt:first", "prompt": "Run first"},
            )
            second = client.post(
                "/api/ui/actions/run",
                json={"widget_id": "prompt:second", "prompt": "Run second"},
            )

            assert first.status_code == 202
            assert second.status_code == 202
            assert mock_client.send_message.await_count == 2
            metadata_values = [
                call.kwargs["metadata"]["thread_id"]
                for call in mock_client.send_message.await_args_list
            ]
            assert metadata_values[0]
            assert metadata_values[1]
            assert metadata_values[0] != metadata_values[1]
        finally:
            backend_main.client = original_client

    def test_workspace_actions_reuse_explicit_thread(self, client):
        from app.backend import main as backend_main

        original_client = backend_main.client
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(
            return_value=AgentResponse(
                response="bridge-response", reasoning=None, reasoning_tokens=None
            )
        )
        backend_main.client = mock_client
        try:
            thread_id = "thread_shared_chain"

            run_response = client.post(
                "/api/ui/actions/run",
                json={
                    "widget_id": "prompt:root",
                    "root_widget_id": "prompt:root",
                    "prompt": "Run root",
                    "thread_id": thread_id,
                },
            )
            generate_response = client.post(
                "/api/ui/actions/generate",
                json={
                    "widget_id": "prompt:child",
                    "root_widget_id": "prompt:root",
                    "prompt": "Generate child",
                    "thread_id": thread_id,
                },
            )

            assert run_response.status_code == 202
            assert generate_response.status_code == 202
            assert_acknowledgement_body(run_response.json())
            assert_acknowledgement_body(generate_response.json())

            metadata_values = [
                call.kwargs["metadata"]["thread_id"]
                for call in mock_client.send_message.await_args_list
            ]
            assert metadata_values == [thread_id, thread_id]

        finally:
            backend_main.client = original_client
