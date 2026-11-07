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

import src.backend.alerts.routes as alerts_routes
import src.backend.chat.responses as chat_responses
import src.backend.chat.routes as chat_routes
import src.backend.events.dispatch as events_dispatch
import src.backend.events.routes as events_routes
import src.backend.events.stream as events_stream
import src.backend.lifecycle.state as lifecycle_state
import src.backend.monitoring.status as monitoring_status
import src.backend.schedules.service as schedules_service
from src.backend.alerts.service import clear_alerts, create_alert
from src.backend.chat.models import AgentResponse
from src.backend.chat.threads import clear_threads
from src.backend.events.documents import seed_document_types
from src.backend.main import app
from src.backend.monitoring.state import StateReader
from src.backend.storage.documents import (
    BACKEND_STATE_PREFIX,
    PROGENA_STATE_PREFIX,
    CheckpointThreadSnapshot,
    InMemoryDocumentStore,
    set_default_document_store,
)
from src.progena.documents.kinds import SESSION_KEY_PREFIX


def _create_session_document(store, thread_id, title="New chat", **extra):
    store.save_document(
        PROGENA_STATE_PREFIX,
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


def _stream_events(queue: asyncio.Queue) -> list[Any]:
    return [queue.get_nowait()["envelope"] for _ in range(queue.qsize())]


def _select_chat(client, ui_stream, thread_id: str, request_id: str = "select") -> Any:
    """Open a chat as tab ``tab-1`` and return its one targeted selection event."""
    _stream_events(ui_stream)
    response = client.get(
        f"/api/ui/chats/{thread_id}?client_id=tab-1", headers={"X-Operation-Id": request_id}
    )
    assert_acknowledged(response, ["chat.session.selected"], request_id=request_id)
    [selected] = [
        event
        for event in _stream_events(ui_stream)
        if event.payload.get("request_id") == request_id
    ]
    assert selected.type == "chat.session.selected"
    return selected


def _list_chats(client, ui_stream, query: str = "", request_id: str = "list") -> Any:
    _stream_events(ui_stream)
    response = client.get(
        f"/api/ui/chats?client_id=tab-1{query}", headers={"X-Operation-Id": request_id}
    )
    assert response.status_code == 202, response.text
    [page] = [
        event
        for event in _stream_events(ui_stream)
        if event.payload.get("request_id") == request_id
    ]
    return page


@pytest.fixture
def client(ui_stream_client):
    """Create a test client with an active canonical stream."""
    from conftest import UiStreamTestClient

    return UiStreamTestClient(TestClient(app), ui_stream_client)


@pytest.fixture(autouse=True)
def clear_thread_registry():
    """Keep the in-memory thread registry isolated between tests."""

    clear_threads()
    yield
    clear_threads()


@pytest.fixture(autouse=True)
def clear_chat_list_state():
    """Opening a chat registers task interest; sent rows are remembered for dedupe."""
    yield
    chat_routes.chat_background_tasks.background_task_events.interests.clear()
    chat_routes._sent_chat_list_rows.clear()


@pytest.fixture
def kanban_state_reader(tmp_path, monkeypatch, document_store):
    """Provide an isolated state reader for kanban persistence tests."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    state_reader = StateReader(
        document_store=document_store,
        logs_dir=logs_dir,
    )
    monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

    events_dispatch._document_event_dispatcher = None
    yield state_reader
    events_dispatch._document_event_dispatcher = None


@pytest.fixture
def tasks_state_reader(tmp_path, monkeypatch, document_store):
    """State reader backed by the shared document store for tasks (DB-only).

    Tasks have no file fallback, so these tests need a real document store. The
    board, task bodies, and activity all persist under the shared
    ``("progena", "state")`` namespace — the same documents the runtime
    agent reads/edits through ``/state/tasks/`` as ``board.json`` plus per-task
    ``<id>.md`` files.
    """
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    state_reader = StateReader(
        document_store=document_store,
        logs_dir=logs_dir,
    )
    monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

    events_dispatch._document_event_dispatcher = None
    yield state_reader
    events_dispatch._document_event_dispatcher = None


@pytest.fixture
def document_store():
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    seed_document_types(store)
    yield store
    set_default_document_store(InMemoryDocumentStore())


class TestUIRoutes:
    """Tests for /api/ui endpoints."""

    def test_widgets_endpoint(self, client):
        request_id = "widgets-request"
        response = client.get("/api/ui/widgets", headers={"X-Operation-Id": request_id})

        assert_acknowledged(
            response, ["widgets.updated"], request_id=request_id, check_request_id_header=True
        )

    def test_process_metrics_excludes_db_fields(self):
        """The periodic tick carries only process metrics, never DB-derived state.

        progena_running belongs here too: it's a live SSE heartbeat check,
        not a Postgres read (see integrations.runtime.get_progena_liveness()).
        """

        metrics = monitoring_status._process_metrics()
        assert set(metrics) == {"status", "uptime_seconds", "memory_mb", "progena_running"}
        assert metrics["status"] in {"healthy", "draining"}
        assert isinstance(metrics["progena_running"], bool)
        # None of the DB-derived fields leak into the metrics tick.
        assert "widgets" not in metrics
        assert "agents" not in metrics
        assert "schedule" not in metrics

    def test_widgets_endpoint_broadcasts_snapshot(self, client, ui_stream):
        """The acknowledgement is backed by a request-correlated, tab-scoped event."""

        request_id = "widgets-stream-request"
        response = client.get(
            "/api/ui/widgets?client_id=tab-1", headers={"X-Operation-Id": request_id}
        )

        assert_acknowledged(
            response, ["widgets.updated"], request_id=request_id, check_request_id_header=True
        )
        envelope = ui_stream.get_nowait()["envelope"]
        assert envelope.type == "widgets.updated"
        assert envelope.payload["request_id"] == request_id
        assert isinstance(envelope.payload["items"], list)

    def test_mcp_endpoint_broadcasts_snapshot(self, client, ui_stream):
        """MCP data is available only from a requester-correlated canonical event."""

        request_id = "mcp-stream-request"
        response = client.get("/api/ui/mcp?client_id=tab-1", headers={"X-Operation-Id": request_id})

        assert_acknowledged(
            response, ["mcp.snapshot"], request_id=request_id, check_request_id_header=True
        )
        [envelope] = [
            event
            for event in _stream_events(ui_stream)
            if event.payload.get("request_id") == request_id
        ]
        assert envelope.type == "mcp.snapshot"
        assert isinstance(envelope.payload["items"], list)

    def test_chat_endpoints_read_session_documents(self, client, document_store, ui_stream):
        _create_session_document(document_store, "thread-1", "Backend chat")
        thread_id = "thread-1"

        patch_request_id = "chat-patch-request"
        patch_response = client.patch(
            f"/api/ui/chats/{thread_id}?client_id=tab-1",
            json={"pinned": True, "archived": True},
            headers={"X-Operation-Id": patch_request_id},
        )
        assert_acknowledged(
            patch_response,
            ["chat.list.row.upserted"],
            request_id=patch_request_id,
            check_request_id_header=True,
        )
        stored = document_store.get_document(
            PROGENA_STATE_PREFIX, f"{SESSION_KEY_PREFIX}{thread_id}"
        )
        assert stored is not None
        assert stored.payload["title"] == "Backend chat"
        assert stored.payload["pinned"] is True
        assert stored.payload["archived"] is True

        list_request_id = "chat-list-request"
        replay_start = len(events_stream._ui_event_replay)
        list_response = client.get(
            "/api/ui/chats?filter=all&client_id=tab-1",
            headers={"X-Operation-Id": list_request_id},
        )
        assert_acknowledged(
            list_response,
            ["chat.list.loaded"],
            request_id=list_request_id,
            check_request_id_header=True,
        )

        [loaded] = [
            event
            for event in _stream_events(ui_stream)
            if event.payload.get("request_id") == list_request_id
        ]
        assert loaded.type == "chat.list.loaded"
        assert loaded.payload["total"] == 1
        assert (loaded.payload["offset"], loaded.payload["groups"]) == (0, [])
        assert loaded.payload["view"]["filter"] == "all"
        [row] = loaded.payload["rows"]
        assert (row["thread_id"], row["pinned"], row["archived"]) == (thread_id, True, True)
        # The page belongs to the requesting tab: never replayed to others.
        assert events_stream._ui_event_replay[replay_start:] == []

    def test_chat_list_requires_an_active_stream_and_all_group_starts(self, client, ui_stream):
        assert client.get("/api/ui/chats?client_id=other-tab").status_code == 409
        assert (
            client.get("/api/ui/chats?client_id=tab-1&today=2026-03-10T00:00:00Z").status_code
            == 400
        )

    def test_chat_list_counts_groups_from_the_browsers_day_starts(
        self, client, document_store, ui_stream
    ):
        _create_session_document(document_store, "recent", "Recent")
        starts = (
            "&today=2000-01-04T13:00:00Z&yesterday=2000-01-03T13:00:00Z"
            "&this_week=1999-12-28T13:00:00Z&last_week=1999-12-21T13:00:00Z"
        )

        page = _list_chats(client, ui_stream, f"&offset=0&limit=1{starts}").payload

        assert page["view"]["group_starts"]["today"] == "2000-01-04T13:00:00Z"
        assert {group["key"]: group["count"] for group in page["groups"]}["today"] == 1
        assert sum(group["count"] for group in page["groups"]) == page["total"] == 1

    @pytest.mark.asyncio
    async def test_concurrent_chat_mutations_emit_their_own_correlated_events(self, document_store):
        """Each persisted route mutation settles only its own acknowledgement."""
        import httpx

        from src.backend.events.documents import DocumentEventDispatcher

        _create_session_document(document_store, "thread-patch", "Before")
        _create_session_document(document_store, "thread-delete", "Disposable")
        _create_session_document(document_store, "thread-empty", "Untouched")
        _create_session_document(document_store, "thread-idempotent", "Already pinned")
        dispatcher = DocumentEventDispatcher(
            store_getter=lambda: document_store, consumer="test.chat-route-correlation"
        )
        dispatcher.add_handler(chat_routes._publish_chat_session_document_event)
        await dispatcher.start()
        subscriber: asyncio.Queue = asyncio.Queue()
        events_stream._ui_stream_subscribers[subscriber] = None
        events_stream._ui_stream_clients[subscriber] = "chat-list-tab"
        events_stream.subscribe_topic("chat-list-tab", "chats")
        patch_request_id = "chat-mutation-patch"
        delete_request_id = "chat-mutation-delete"
        missing_request_id = "chat-mutation-missing"
        empty_request_id = "chat-mutation-empty"
        idempotent_request_id = "chat-mutation-idempotent"

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as async_client:
                (
                    patch_response,
                    delete_response,
                    missing_response,
                    empty_response,
                    idempotent_response,
                ) = await asyncio.gather(
                    async_client.patch(
                        "/api/ui/chats/thread-patch?client_id=chat-list-tab",
                        json={"title": "After", "pinned": True, "archived": True},
                        headers={"X-Operation-Id": patch_request_id},
                    ),
                    async_client.delete(
                        "/api/ui/chats/thread-delete?client_id=chat-list-tab",
                        headers={"X-Operation-Id": delete_request_id},
                    ),
                    async_client.patch(
                        "/api/ui/chats/missing?client_id=chat-list-tab",
                        json={"title": "Never persisted"},
                        headers={"X-Operation-Id": missing_request_id},
                    ),
                    async_client.patch(
                        "/api/ui/chats/thread-empty?client_id=chat-list-tab",
                        json={},
                        headers={"X-Operation-Id": empty_request_id},
                    ),
                    async_client.patch(
                        "/api/ui/chats/thread-idempotent?client_id=chat-list-tab",
                        json={"pinned": False},
                        headers={"X-Operation-Id": idempotent_request_id},
                    ),
                )
            await dispatcher.drain()
        finally:
            await dispatcher.stop()
            events_stream._ui_stream_subscribers.pop(subscriber, None)
            events_stream._ui_stream_clients.pop(subscriber, None)
            events_stream._ui_stream_topics.pop("chat-list-tab", None)

        assert_acknowledged(
            patch_response,
            ["chat.list.row.upserted"],
            request_id=patch_request_id,
            check_request_id_header=True,
        )
        assert_acknowledged(
            delete_response,
            ["chat.list.row.deleted"],
            request_id=delete_request_id,
            check_request_id_header=True,
        )
        assert missing_response.status_code == 404
        assert_acknowledged(
            empty_response,
            ["chat.list.row.upserted"],
            request_id=empty_request_id,
            check_request_id_header=True,
        )
        assert_acknowledged(
            idempotent_response,
            ["chat.list.row.upserted"],
            request_id=idempotent_request_id,
            check_request_id_header=True,
        )
        emitted = [subscriber.get_nowait()["envelope"] for _ in range(subscriber.qsize())]
        by_request_id = {
            request_id: [
                event for event in emitted if event.payload.get("request_id") == request_id
            ]
            for request_id in (
                patch_request_id,
                delete_request_id,
                missing_request_id,
                empty_request_id,
                idempotent_request_id,
            )
        }
        assert [event.type for event in by_request_id[patch_request_id]] == [
            "chat.list.row.upserted"
        ]
        assert [event.type for event in by_request_id[delete_request_id]] == [
            "chat.list.row.deleted"
        ]
        assert by_request_id[missing_request_id] == []
        assert [event.payload["row"]["thread_id"] for event in by_request_id[empty_request_id]] == [
            "thread-empty"
        ]
        assert [
            event.payload["row"]["thread_id"] for event in by_request_id[idempotent_request_id]
        ] == ["thread-idempotent"]
        patched_row = by_request_id[patch_request_id][0].payload["row"]
        assert patched_row["thread_id"] == "thread-patch"
        assert patched_row["title"] == "After"
        assert patched_row["pinned"] is True
        assert patched_row["archived"] is True
        assert by_request_id[delete_request_id][0].payload == {
            "thread_id": "thread-delete",
            "request_id": delete_request_id,
        }

    def test_chat_detail_is_one_targeted_event_with_queue_and_background_tasks(
        self, client, document_store, ui_stream
    ):
        _create_session_document(document_store, "thread-select", "Selectable chat")
        thread_id = "thread-select"
        task_events = chat_routes.chat_background_tasks.background_task_events
        replay_start = len(events_stream._ui_event_replay)

        try:
            selected = _select_chat(client, ui_stream, thread_id, request_id="chat-detail")

            assert selected.session_id == thread_id
            assert selected.payload["session"]["thread_id"] == thread_id
            # The runtime client is absent in tests: the page loads its queue itself.
            assert selected.payload["queue"] is None
            assert selected.payload["background_tasks"] == {
                "thread_id": thread_id,
                "tasks": [],
                "revision": 0,
            }
            # Opening the chat registered this tab's background-task interest.
            assert thread_id in task_events.interests["tab-1"]
            assert events_stream._ui_event_replay[replay_start:] == []
        finally:
            task_events.unsubscribe("tab-1")

    def test_chat_detail_reports_interruption_only_for_an_unfinished_turn(
        self, client, document_store, monkeypatch, ui_stream
    ):
        """A checkpoint is read only when the saved transcript ends mid-turn."""
        reads: list[str] = []

        async def read_checkpoint(thread_id: str, _store):
            reads.append(thread_id)
            return {
                "exists": True,
                "next": ["tools"],
                "waiting": False,
                "failed": False,
                "runtime_instance": "old-runtime",
                "updated_at": "2026-10-28T10:00:00Z",
            }

        document_store.save_document(
            PROGENA_STATE_PREFIX,
            "health",
            {"runtime_instance": "live-runtime", "started_at": "2026-10-28T10:01:00Z"},
        )
        monkeypatch.setattr(
            chat_routes.checkpoint_state, "read_thread_checkpoint_state", read_checkpoint
        )
        for thread_id, last in (
            (
                "thread-cut",
                {
                    "id": "a1",
                    "type": "ai",
                    "content": "",
                    "tool_calls": [{"id": "call-1", "name": "read_file", "args": {}}],
                },
            ),
            ("thread-done", {"id": "a1", "type": "ai", "content": "All done."}),
        ):
            _create_session_document(document_store, thread_id, thread_id)
            document_store.upsert_checkpoint_thread_snapshot(
                CheckpointThreadSnapshot(
                    thread_id=thread_id,
                    checkpoint_id="cp-1",
                    checkpoint_ts="2026-10-28T09:59:00Z",
                    messages=[{"id": "u1", "type": "human", "content": "go"}, last],
                )
            )

        cut = _select_chat(client, ui_stream, "thread-cut", "cut").payload["session"]
        done = _select_chat(client, ui_stream, "thread-done", "done").payload["session"]

        assert cut["interruption"] == {"next": ["tools"], "at": "2026-10-28T10:00:00Z"}
        assert done["interruption"] is None
        assert reads == ["thread-cut"]

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ({"waiting": True}, None),
            ({"failed": True}, None),
            ({"runtime_instance": "live"}, None),
            ({"next": []}, None),
        ],
    )
    def test_chat_interruption_requires_an_orphaned_pending_checkpoint(self, state, expected):
        checkpoint = {
            "exists": True,
            "next": ["tools"],
            "waiting": False,
            "failed": False,
            "runtime_instance": "old",
            "updated_at": "2026-10-28T09:00:00Z",
            **state,
        }
        live = chat_routes.runtime_liveness.LiveRuntime("live", "2026-10-28T10:00:00Z")

        assert chat_routes._interruption_from_checkpoint_state(checkpoint, live) == expected
        assert chat_routes._interruption_from_checkpoint_state(checkpoint, None) is None

    def test_chat_detail_rehydrates_live_response_activity(self, client, document_store, ui_stream):
        """A selected thread recovers live activity without persisting transient state."""

        thread_id = "thread-live-delegation"
        response_request_id = "live-delegation-response"
        _create_session_document(document_store, thread_id, "Delegating chat")
        chat_responses._register_response_activity(response_request_id, thread_id)
        try:
            generating_selected = _select_chat(
                client, ui_stream, thread_id, "generating-detail-request"
            )
            assert (
                generating_selected.payload["session"]["active_request_id"] == response_request_id
            )
            assert generating_selected.payload["session"]["activity"] == "generating"

            chat_responses._track_response_delegation_event(
                response_request_id,
                {
                    "event_type": "tool_start",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "routine-live",
                        "subagent_type": "routine-live",
                    },
                },
            )
            delegating_selected = _select_chat(
                client, ui_stream, thread_id, "delegating-detail-request"
            )
            assert (
                delegating_selected.payload["session"]["active_request_id"] == response_request_id
            )
            assert delegating_selected.payload["session"]["activity"] == "delegating"

            chat_responses._clear_response_activity(response_request_id)
            terminal_selected = _select_chat(
                client, ui_stream, thread_id, "terminal-detail-request"
            )
            assert terminal_selected.payload["session"]["active_request_id"] is None
            assert terminal_selected.payload["session"]["activity"] is None
        finally:
            chat_responses._clear_response_activity(response_request_id)

    def test_chat_card_rehydrates_history_from_checkpoint_thread(
        self, client, document_store, ui_stream
    ):
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
            document_store,
            "thread_sess-history",
            first_message="count backwards",
            preview="99, 98, 97",
        )
        thread_id = "thread_sess-history"

        listed = next(
            row
            for row in _list_chats(client, ui_stream, "&filter=all").payload["rows"]
            if row["thread_id"] == thread_id
        )
        assert listed["title"] == "count backwards"
        assert listed["preview"] == "99, 98, 97"

        session = _select_chat(client, ui_stream, thread_id).payload["session"]
        assert session["thread_id"] == "thread_sess-history"
        assert [message["content"] for message in session["messages"]] == [
            "count backwards",
            "99, 98, 97",
        ]
        assert session["messages"][1]["reasoning_text"] == "Need to count down."
        assert session["messages"][1]["reasoning_token_count"] == 3
        assert session["messages"][1]["token_count"] == 12

    def test_chat_detail_caps_messages_and_flags_has_more(self, client, document_store, ui_stream):
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

        session = _select_chat(client, ui_stream, thread_id).payload["session"]
        assert session["has_more_messages"] is True
        assert len(session["messages"]) == 50
        assert session["messages"][0]["content"] == "message 10"
        assert session["messages"][-1]["content"] == "message 59"
        assert session["message_count"] == 60

    def test_chat_messages_page_loads_older_page_until_exhausted(
        self, client, document_store, ui_stream
    ):
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
            params={"before_message_id": "m10", "client_id": "tab-1"},
        )
        assert older_response.status_code == 202

        page_event = next(
            event
            for event in _stream_events(ui_stream)
            if event.type == "chat.messages.page.loaded"
        )
        assert page_event.type == "chat.messages.page.loaded"
        assert page_event.payload["thread_id"] == thread_id
        assert page_event.payload["has_more"] is False
        page_messages = page_event.payload["messages"]
        assert len(page_messages) == 10
        assert page_messages[0]["content"] == "message 0"
        assert page_messages[-1]["content"] == "message 9"

    def test_deleted_chat_stays_deleted_despite_live_runtime_state(
        self, client, document_store, monkeypatch, ui_stream
    ):

        thread_id = "thread-deleted"
        _create_session_document(document_store, thread_id, "Doomed")
        assert client.delete(f"/api/ui/chats/{thread_id}?client_id=tab-1").status_code == 202

        class _LiveStateReader:
            """The runtime still holds a current_state document for the thread."""

            def get_progena_state(self, thread_id: str | None = None) -> dict[str, Any]:
                return {"thread_id": thread_id, "cycle_count": 1}

            def get_thread_messages(self, thread_id: str) -> list[Any]:
                return [{"id": "m1", "type": "human", "content": "hello"}]

        monkeypatch.setattr(
            "src.backend.monitoring.state.get_state_reader", lambda: _LiveStateReader()
        )
        assert client.get(f"/api/ui/chats/{thread_id}?client_id=tab-1").status_code == 404
        assert client.get(f"/api/ui/chats/{thread_id}/messages?client_id=tab-1").status_code == 404
        assert not [
            event for event in _stream_events(ui_stream) if event.type == "chat.session.selected"
        ]

    def test_chat_detail_reports_runtime_outage_but_the_list_still_renders(
        self, client, document_store, monkeypatch, ui_stream
    ):
        from src.backend.integrations.runtime_history import RuntimeUnavailable

        _create_session_document(document_store, "thread-out", "Has history")

        def _unavailable(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeUnavailable("runtime unreachable")

        monkeypatch.setattr(document_store, "get_checkpoint_thread_snapshot", _unavailable)

        response = client.get("/api/ui/chats/thread-out?client_id=tab-1")
        assert response.status_code == 503
        # Rows are the session documents alone, so the runtime is not involved.
        rows = _list_chats(client, ui_stream).payload["rows"]
        assert [row["title"] for row in rows] == ["Has history"]

    def test_rename_is_accepted_while_the_runtime_is_down(
        self, client, document_store, monkeypatch, ui_stream
    ):
        from src.backend.integrations.runtime_history import RuntimeUnavailable

        _create_session_document(document_store, "thread-rename", "Old")

        def _unavailable(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeUnavailable("runtime unreachable")

        monkeypatch.setattr(document_store, "get_checkpoint_thread_snapshot", _unavailable)

        response = client.patch(
            "/api/ui/chats/thread-rename?client_id=tab-1", json={"title": "New"}
        )

        assert response.status_code == 202
        stored = document_store.get_document(
            PROGENA_STATE_PREFIX, f"{SESSION_KEY_PREFIX}thread-rename"
        )
        assert stored.payload["title"] == "New"
        assert (
            client.patch("/api/ui/chats/missing?client_id=tab-1", json={"title": "x"}).status_code
            == 404
        )

    async def test_session_document_event_sends_the_current_row_once(self, document_store):
        """Document events are historical: the row sent is the document as it is
        now, an uncorrelated repeat of an unchanged row is not resent, and a
        delete sends only the id."""
        from src.backend.events.documents import TypedDocumentEvent
        from src.backend.storage.documents import DocumentEvent

        _create_session_document(document_store, "thread-row", "Old title")
        key = f"{SESSION_KEY_PREFIX}thread-row"
        historical = document_store.get_document(PROGENA_STATE_PREFIX, key).payload
        document_store.merge_document(
            PROGENA_STATE_PREFIX, key, {"title": "New title", "preview": "latest"}
        )

        def typed(op: str) -> TypedDocumentEvent:
            event = DocumentEvent(
                seq=1,
                txid=1,
                prefix=PROGENA_STATE_PREFIX,
                key=key,
                op=op,
                before=historical,
                after=None if op == "deleted" else historical,
                origin=None,
                created_at=datetime.now(UTC),
            )
            return TypedDocumentEvent(event=event, document_type=None, output=None)

        subscriber: asyncio.Queue = asyncio.Queue()
        events_stream._ui_stream_subscribers[subscriber] = None
        events_stream._ui_stream_clients[subscriber] = "chat-list-tab"
        events_stream.subscribe_topic("chat-list-tab", "chats")
        try:
            await chat_routes._publish_chat_session_document_event(typed("updated"))
            await chat_routes._publish_chat_session_document_event(typed("updated"))
            await chat_routes._publish_chat_session_document_event(typed("deleted"))
            emitted = [subscriber.get_nowait()["envelope"] for _ in range(subscriber.qsize())]
        finally:
            events_stream._ui_stream_subscribers.pop(subscriber, None)
            events_stream._ui_stream_clients.pop(subscriber, None)
            events_stream._ui_stream_topics.pop("chat-list-tab", None)
        assert [envelope.type for envelope in emitted] == [
            "chat.list.row.upserted",
            "chat.list.row.deleted",
        ]
        row = emitted[0].payload["row"]
        assert (row["thread_id"], row["title"], row["preview"]) == (
            "thread-row",
            "New title",
            "latest",
        )
        assert emitted[1].payload == {"thread_id": "thread-row"}

    async def test_every_turn_session_write_reaches_the_list_through_the_store_watcher(
        self, document_store, monkeypatch, tmp_path
    ):
        """Per-turn content/preview writes log no document event; the store
        notification must still reach the watcher and send the row."""
        reader = StateReader(document_store=document_store, logs_dir=tmp_path)
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: reader)
        reader.add_watcher(monitoring_status._queue_state_watch_status)
        _create_session_document(document_store, "thread-turn", "Turning")
        reader._last_snapshot = reader.get_snapshot()
        document_store.merge_document(
            PROGENA_STATE_PREFIX,
            f"{SESSION_KEY_PREFIX}thread-turn",
            {"preview": "the answer", "last_activity": "2030-01-01T00:00:00Z", "content": "x"},
        )
        queue: asyncio.Queue = asyncio.Queue()
        events_stream._ui_stream_subscribers[queue] = None
        events_stream._ui_stream_clients[queue] = "chat-list-tab"
        events_stream.subscribe_topic("chat-list-tab", "chats")
        try:
            reader._on_store_change(
                json.dumps({"prefix": PROGENA_STATE_PREFIX, "key": "session:thread-turn"})
            )
            await asyncio.gather(*monitoring_status._active_chat_list_row_tasks)
        finally:
            events_stream._ui_stream_subscribers.pop(queue, None)
            events_stream._ui_stream_clients.pop(queue, None)
            events_stream._ui_stream_topics.pop("chat-list-tab", None)

        [row_event] = [e for e in _stream_events(queue) if e.type == "chat.list.row.upserted"]
        row = row_event.payload["row"]
        assert (row["thread_id"], row["preview"], row["last_activity"]) == (
            "thread-turn",
            "the answer",
            "2030-01-01T00:00:00Z",
        )

    async def test_slow_chat_read_does_not_block_other_requests(
        self, document_store, monkeypatch, ui_stream
    ):
        import time

        import httpx

        def _slow_snapshot(*_args: Any, **_kwargs: Any) -> None:
            time.sleep(1.0)  # a slow runtime read, synchronous like the real reader
            return None

        monkeypatch.setattr(document_store, "get_checkpoint_thread_snapshot", _slow_snapshot)
        _create_session_document(document_store, "thread-slow")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
            started = time.monotonic()
            slow = asyncio.create_task(
                async_client.get("/api/ui/chats/thread-slow?client_id=tab-1")
            )
            await asyncio.sleep(0.1)  # let the slow read start
            fast = await async_client.get("/api/ui/preferences?client_id=tab-1")
            fast_done_after = time.monotonic() - started
            await slow
        assert fast.status_code == 202
        # Had the slow read run on the event loop, nothing (not even the sleep
        # above) could finish before it did, ~1s in.
        assert fast_done_after < 0.6

    def test_chat_list_hides_schedule_activity_threads(self, client, document_store, ui_stream):
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
        rows = _list_chats(client, ui_stream, "&filter=all").payload["rows"]
        assert all(row["thread_id"] != "2026-06-19T09:00:00+00:00" for row in rows)

    def test_chat_delete_removes_session_document(self, client, document_store, ui_stream):
        _create_session_document(document_store, "thread-delete", "Disposable chat")
        thread_id = "thread-delete"

        delete_response = client.delete(f"/api/ui/chats/{thread_id}?client_id=tab-1")

        assert delete_response.status_code == 202
        assert (
            document_store.get_document(PROGENA_STATE_PREFIX, f"{SESSION_KEY_PREFIX}{thread_id}")
            is None
        )
        rows = _list_chats(client, ui_stream, "&filter=all").payload["rows"]
        assert all(row["thread_id"] != thread_id for row in rows)

    def test_chat_list_shows_session_documents(self, client, document_store, ui_stream):
        _create_session_document(document_store, "thread_sess-1", "Chat One")
        _create_session_document(document_store, "thread_sess-2", "Chat Two")

        page = _list_chats(client, ui_stream, "&filter=all").payload
        assert page["total"] == 2
        assert {row["thread_id"] for row in page["rows"]} == {"thread_sess-1", "thread_sess-2"}

    def test_chat_patch_updates_session_document(
        self,
        client,
        document_store,
        ui_stream,
    ):
        _create_session_document(
            document_store, "thread_current-session-1", "Current synthetic chat"
        )

        patch_response = client.patch(
            "/api/ui/chats/thread_current-session-1?client_id=tab-1",
            json={"pinned": True, "archived": True},
        )

        assert patch_response.status_code == 202
        stored = document_store.get_document(
            PROGENA_STATE_PREFIX, f"{SESSION_KEY_PREFIX}thread_current-session-1"
        )
        assert stored is not None
        assert stored.payload["pinned"] is True
        assert stored.payload["archived"] is True

        delete_response = client.delete("/api/ui/chats/thread_current-session-1?client_id=tab-1")

        assert delete_response.status_code == 202
        assert (
            document_store.get_document(
                PROGENA_STATE_PREFIX, f"{SESSION_KEY_PREFIX}thread_current-session-1"
            )
            is None
        )

    def test_kanban_endpoint_returns_default_board(self, client, tasks_state_reader, ui_stream):
        response = client.get("/api/ui/kanban?client_id=tab-1")
        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)

        [replay] = _stream_events(ui_stream)
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
        self, client, tasks_state_reader, ui_stream
    ):
        """A headerless activity read replies only to its requesting stream."""

        created = client.post(
            "/api/ui/kanban/cards?client_id=tab-1", json={"title": "Activity task"}
        )
        assert created.status_code == 202
        board = tasks_state_reader.document_store.get_document(
            PROGENA_STATE_PREFIX, "kanban_board", None
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
            PROGENA_STATE_PREFIX,
            f"kanban_task_activity:{card_id}",
            persisted_activity,
        )

        _stream_events(ui_stream)
        response = client.get(f"/api/ui/tasks/{card_id}/activity?client_id=tab-1")
        request_id = response.headers["X-Operation-Id"]
        assert_acknowledged(
            response,
            ["task.activity.updated"],
            request_id=request_id,
            check_request_id_header=True,
        )
        envelopes = _stream_events(ui_stream)

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

    def test_task_content_update_acknowledges_and_persists_body(
        self, client, tasks_state_reader, ui_stream
    ):
        """Task body writes reply with saved content and activity to their requester."""

        created = client.post(
            "/api/ui/kanban/cards?client_id=tab-1",
            json={"title": "Persisted task", "summary": "Keep the brief"},
        )
        assert created.status_code == 202
        board = tasks_state_reader.document_store.get_document(
            PROGENA_STATE_PREFIX, "kanban_board", None
        )
        assert board is not None
        card_id = board.payload["card_order"][-1]
        request_id = "task-content-save-42"
        _stream_events(ui_stream)
        response = client.put(
            f"/api/ui/tasks/{card_id}/content?client_id=tab-1",
            json={"content": "## Acceptance\n\nKeep this body\n\n"},
            headers={"X-Operation-Id": request_id},
        )
        emitted = _stream_events(ui_stream)

        assert_acknowledged(
            response,
            ["task.content.updated", "task.activity.updated"],
            request_id=request_id,
            check_request_id_header=True,
        )
        content_event = next(event for event in emitted if event.type == "task.content.updated")
        assert content_event.payload["card_id"] == card_id
        assert content_event.payload["path"] == f"tasks/{card_id}.md"
        assert content_event.payload["content"] == "## Acceptance\n\nKeep this body"
        assert content_event.payload["request_id"] == request_id

        stored = tasks_state_reader.document_store.get_document(
            PROGENA_STATE_PREFIX, f"kanban_task_content:{card_id}", None
        )
        assert stored is not None
        assert stored.payload == {"content": "## Acceptance\n\nKeep this body"}

    def test_kanban_card_create_and_patch_persist(self, client, tasks_state_reader, ui_stream):
        create_response = client.post(
            "/api/ui/kanban/cards?client_id=tab-1",
            json={"title": "Build kanban board", "summary": "Server first"},
        )

        data = create_response.json()
        assert_acknowledgement_body(data)
        assert "tasks.snapshot" in data["event_types"]
        store = tasks_state_reader.document_store
        board = store.get_document(PROGENA_STATE_PREFIX, "kanban_board", None)
        card_id = board.payload["card_order"][-1]
        # Cards persist as their own ``kanban_card:<id>`` document; the board
        # structure only records column layout + ``card_order``.
        stored_board = store.get_document(PROGENA_STATE_PREFIX, "kanban_board", None)
        assert stored_board is not None
        assert "cards" not in stored_board.payload
        stored_card = store.get_document(PROGENA_STATE_PREFIX, f"kanban_card:{card_id}", None)
        assert stored_card is not None
        assert stored_card.payload["id"] == card_id

        patch_response = client.patch(
            f"/api/ui/kanban/cards/{card_id}?client_id=tab-1",
            json={
                "column_id": "build",
                "status": "running",
                "agent_name": "build_worker",
                "branch_name": "feat/kanban-board",
                "labels": ["frontend", "live-first"],
            },
        )

        assert patch_response.status_code == 202
        data = patch_response.json()
        assert_acknowledgement_body(data)
        assert "tasks.snapshot" in data["event_types"]

        stored_card = store.get_document(PROGENA_STATE_PREFIX, f"kanban_card:{card_id}", None)
        assert stored_card is not None
        assert stored_card.payload["column_id"] == "build"
        assert stored_card.payload["status"] == "running"
        assert stored_card.payload["agent_name"] == "build_worker"
        assert stored_card.payload["branch_name"] == "feat/kanban-board"
        assert stored_card.payload["labels"] == ["frontend", "live-first"]
        assert stored_card.payload["started_at"] is not None

        _stream_events(ui_stream)
        board_response = client.get("/api/ui/kanban?client_id=tab-1")
        assert board_response.status_code == 202

        [snapshot] = _stream_events(ui_stream)
        board = snapshot.payload
        assert board["cards"][0]["id"] == card_id
        assert board["cards"][0]["column_id"] == "build"

    def test_kanban_card_project_id_persists_and_patches(
        self, client, tasks_state_reader, ui_stream
    ):
        create_response = client.post(
            "/api/ui/kanban/cards?client_id=tab-1",
            json={"title": "Scoped card", "project_id": "acme"},
        )
        data = create_response.json()
        assert_acknowledgement_body(data)
        assert "tasks.snapshot" in data["event_types"]
        store = tasks_state_reader.document_store
        board = store.get_document(PROGENA_STATE_PREFIX, "kanban_board", None)
        card_id = board.payload["card_order"][-1]

        stored_card = tasks_state_reader.document_store.get_document(
            PROGENA_STATE_PREFIX, f"kanban_card:{card_id}", None
        )
        assert stored_card.payload["project_id"] == "acme"

        patch_response = client.patch(
            f"/api/ui/kanban/cards/{card_id}?client_id=tab-1",
            json={"project_id": "beta"},
        )
        assert patch_response.status_code == 202
        data = patch_response.json()
        assert_acknowledgement_body(data)
        stored_card = tasks_state_reader.document_store.get_document(
            PROGENA_STATE_PREFIX, f"kanban_card:{card_id}", None
        )
        assert stored_card.payload["project_id"] == "beta"

        cleared = client.patch(
            f"/api/ui/kanban/cards/{card_id}?client_id=tab-1",
            json={"project_id": ""},
        )
        assert cleared.status_code == 202
        assert_acknowledgement_body(cleared.json())
        stored_card = tasks_state_reader.document_store.get_document(
            PROGENA_STATE_PREFIX, f"kanban_card:{card_id}", None
        )
        assert stored_card.payload["project_id"] is None

    def test_kanban_card_metadata_persists_subtasks_and_dependencies(
        self, client, tasks_state_reader, ui_stream
    ):
        client.post(
            "/api/ui/kanban/cards?client_id=tab-1",
            json={"title": "Metadata task"},
        )
        store = tasks_state_reader.document_store
        board = store.get_document(PROGENA_STATE_PREFIX, "kanban_board", None)
        card_id = board.payload["card_order"][-1]

        patch_response = client.patch(
            f"/api/ui/kanban/cards/{card_id}?client_id=tab-1",
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
        stored_card = store.get_document(PROGENA_STATE_PREFIX, f"kanban_card:{card_id}", None)
        assert stored_card.payload["metadata"]["subtasks"][0]["title"] == "First subtask"
        assert stored_card.payload["metadata"]["subtasks"][1]["done"] is True
        assert stored_card.payload["metadata"]["dependencies"] == ["task-123", "docs/spec.md"]

        patch_response = client.patch(
            f"/api/ui/kanban/cards/{card_id}?client_id=tab-1",
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
            PROGENA_STATE_PREFIX, f"kanban_card:{card_id}", None
        )
        assert stored_card_updated.payload["metadata"]["collaborators"] == [
            "review_agent",
            "qa_user",
        ]
        assert stored_card_updated.payload["metadata"]["attachments"][1]["kind"] == "pr"

    def test_kanban_card_reorder_persists_positions(self, client, tasks_state_reader, ui_stream):
        client.post("/api/ui/kanban/cards?client_id=tab-1", json={"title": "First card"})
        store = tasks_state_reader.document_store
        board = store.get_document(PROGENA_STATE_PREFIX, "kanban_board", None)
        first_id = board.payload["card_order"][0]
        client.post("/api/ui/kanban/cards?client_id=tab-1", json={"title": "Second card"})
        client.post("/api/ui/kanban/cards?client_id=tab-1", json={"title": "Third card"})
        board = store.get_document(PROGENA_STATE_PREFIX, "kanban_board", None)
        third_id = board.payload["card_order"][2]

        reorder_response = client.patch(
            f"/api/ui/kanban/cards/{third_id}?client_id=tab-1",
            json={"position": 1},
        )

        assert reorder_response.status_code == 202
        data = reorder_response.json()
        assert_acknowledgement_body(data)
        board = store.get_document(PROGENA_STATE_PREFIX, "kanban_board", None)
        assert board.payload["card_order"][0] == first_id
        assert board.payload["card_order"][1] == third_id

        move_response = client.patch(
            f"/api/ui/kanban/cards/{first_id}?client_id=tab-1",
            json={"column_id": "build", "position": 0},
        )

        assert move_response.status_code == 202
        data = move_response.json()
        assert_acknowledgement_body(data)

        _stream_events(ui_stream)
        board_response = client.get("/api/ui/kanban?client_id=tab-1")
        assert board_response.status_code == 202
        [snapshot] = _stream_events(ui_stream)
        board = snapshot.payload
        backlog_cards = [card for card in board["cards"] if card["column_id"] == "backlog"]
        build_cards = [card for card in board["cards"] if card["column_id"] == "build"]

        assert [card["title"] for card in backlog_cards] == ["Third card", "Second card"]
        assert [card["position"] for card in backlog_cards] == [0, 1]
        assert [card["title"] for card in build_cards] == ["First card"]
        assert build_cards[0]["position"] == 0

    def test_kanban_normalizes_legacy_columns(self, client, tasks_state_reader, ui_stream):
        legacy_board = {
            "id": "progena-kanban",
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
            PROGENA_STATE_PREFIX, "kanban_board", legacy_board
        )

        board_response = client.get("/api/ui/kanban?client_id=tab-1")
        assert board_response.status_code == 202

        [replay] = _stream_events(ui_stream)
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
        structure = store.get_document(PROGENA_STATE_PREFIX, "kanban_board", None)
        assert "cards" not in structure.payload
        assert set(structure.payload["card_order"]) == {
            "legacy-research",
            "legacy-test",
            "legacy-review",
        }
        assert store.get_document(PROGENA_STATE_PREFIX, "kanban_card:legacy-test", None) is not None

    def test_kanban_card_delete_persists(self, client, tasks_state_reader, ui_stream):
        create_response = client.post(
            "/api/ui/kanban/cards?client_id=tab-1", json={"title": "Disposable card"}
        )
        data = create_response.json()
        assert_acknowledgement_body(data)
        store = tasks_state_reader.document_store
        board = store.get_document(PROGENA_STATE_PREFIX, "kanban_board", None)
        card_id = board.payload["card_order"][-1]

        delete_response = client.delete(f"/api/ui/kanban/cards/{card_id}?client_id=tab-1")

        assert delete_response.status_code == 202
        _stream_events(ui_stream)
        board_response = client.get("/api/ui/kanban?client_id=tab-1")
        assert board_response.status_code == 202

        [replay] = _stream_events(ui_stream)
        board = replay.payload
        assert all(card["id"] != card_id for card in board["cards"])
        # Deleting the card also removes its backing document and drops it from
        # the board's ``card_order``.
        store = tasks_state_reader.document_store
        assert store.get_document(PROGENA_STATE_PREFIX, f"kanban_card:{card_id}", None) is None
        structure = store.get_document(PROGENA_STATE_PREFIX, "kanban_board", None)
        assert card_id not in structure.payload["card_order"]

    def test_schedule_widget_delete_persists_removal(self, client, kanban_state_reader, ui_stream):
        create_response = client.post(
            "/api/ui/schedules?client_id=tab-1",
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
        schedule_doc = store.get_document(PROGENA_STATE_PREFIX, "schedule", {})
        before_delete = schedule_doc.payload if schedule_doc else {}
        schedule_id = list(before_delete.keys())[0]
        assert list(before_delete.keys()) == [schedule_id]

        delete_response = client.delete(f"/api/ui/widgets/schedule:{schedule_id}?client_id=tab-1")

        assert delete_response.status_code == 202
        data = delete_response.json()
        assert_acknowledgement_body(data)
        schedule_doc = store.get_document(PROGENA_STATE_PREFIX, "schedule", {})
        after_delete = schedule_doc.payload if schedule_doc else {}
        assert after_delete == {}

        widgets_resp = client.get("/api/ui/widgets?client_id=tab-1")
        assert widgets_resp.status_code == 202
        widgets = next(
            item["envelope"].payload
            for item in list(ui_stream._queue)
            if item["envelope"].type == "widgets.updated"
        )
        assert f"schedule:{schedule_id}" not in {widget["id"] for widget in widgets["items"]}

        repeated_delete = client.delete(f"/api/ui/widgets/schedule:{schedule_id}?client_id=tab-1")

        assert repeated_delete.status_code == 202
        data = repeated_delete.json()
        assert_acknowledgement_body(data)
        response = client.get("/api/ui/schedules?client_id=tab-1")
        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)

        snapshot = next(
            item["envelope"].payload
            for item in list(ui_stream._queue)
            if item["envelope"].type == "schedules.snapshot"
        )
        assert isinstance(snapshot.get("items"), list)

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

        alerts_resp = client.get("/api/ui/alerts")
        assert alerts_resp.status_code == 202
        alerts = events_stream._ui_event_replay[-1].payload
        first_alert = next(alert for alert in alerts["alerts"] if alert["id"] == first.id)
        assert first_alert["viewed"] is True

    def test_dismiss_alert_marks_viewed_without_deleting(self, client):
        created = create_alert(type="warning", title="Build", message="Build failed")

        response = client.post(f"/api/ui/alerts/{created.id}/dismiss")

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)

        alerts_resp = client.get("/api/ui/alerts")
        assert alerts_resp.status_code == 202
        alerts = events_stream._ui_event_replay[-1].payload
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

        alerts_resp = client.get("/api/ui/alerts")
        assert alerts_resp.status_code == 202
        alerts = events_stream._ui_event_replay[-1].payload
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


class TestLogsEndpoint:
    """Tests for /api/ui/logs endpoint."""

    def test_logs_json_groups_sources_by_folder_and_preserves_log_timestamp(
        self, client, kanban_state_reader, ui_stream
    ):
        """Test endpoint accepts group/source params."""
        builder_dir = kanban_state_reader.logs_dir / "builder"
        builder_dir.mkdir(parents=True, exist_ok=True)

        (kanban_state_reader.logs_dir / "progena.log").write_text(
            "2026-03-11 21:59:52 - __main__ - INFO - Progena starting...\n",
            encoding="utf-8",
        )
        (builder_dir / "builder.log").write_text(
            "2026-03-08 00:33:35 - external.builder - INFO - Builder ready\n",
            encoding="utf-8",
        )

        response = client.get(
            "/api/ui/logs?format=json&folder=builder&file=builder/builder.log&start=2026-03-01&end=2026-03-12&limit=0&client_id=tab-1"
        )

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        assert "request_id" in data
        assert "event_types" in data

    def test_logs_json_supports_incremental_cursor_and_regex_search(
        self, client, kanban_state_reader, ui_stream
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
            "/api/ui/logs?format=json&search=beta|alpha&search_mode=regex&limit=0&client_id=tab-1"
        )
        assert full_response.status_code == 202
        data = full_response.json()
        assert_acknowledgement_body(data)

    def test_logs_json_supports_date_range_filter(self, client, kanban_state_reader, ui_stream):
        """Test inclusive start/end date filtering."""
        range_end = datetime.now(UTC).date()
        range_start = range_end - timedelta(days=1)

        response = client.get(
            f"/api/ui/logs?format=json&start={range_start.isoformat()}&end={range_end.isoformat()}&limit=0&client_id=tab-1"
        )

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)

    def test_logs_export_returns_downloadable_payload(self, client, kanban_state_reader, ui_stream):
        """Test export endpoint returns accepted response."""
        timestamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        (kanban_state_reader.logs_dir / "decisions.jsonl").write_text(
            json.dumps({"message": "decision made", "timestamp": timestamp, "level": "info"})
            + "\n",
            encoding="utf-8",
        )

        response = client.get("/api/ui/logs/export?format=jsonl&client_id=tab-1")
        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        assert "request_id" in data
        assert "event_types" in data

    def test_logs_export_nonempty_emits_correlated_reconstructable_chunk(
        self, client, kanban_state_reader
    ):
        """A nonempty export delivers its complete data only on the UI stream."""

        request_id = "nonempty-log-export-client-request"
        # Log files hold zone-less UTC; the API returns them as UTC instants.
        timestamp = datetime.now(UTC).replace(microsecond=0)
        queued = timestamp - timedelta(minutes=1)
        queued_at = queued.strftime("%Y-%m-%d %H:%M:%S")
        failed_at = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        log_dir = kanban_state_reader.logs_dir / "progena"
        log_dir.mkdir(parents=True)
        (log_dir / "progena.stdout.log").write_text(
            f"{queued_at} - src.progena.decisions - INFO - decision queued\n", encoding="utf-8"
        )
        (log_dir / "progena.stderr.log").write_text(
            f"{failed_at} - src.progena.decisions - ERROR - decision failed\n", encoding="utf-8"
        )

        queue = asyncio.Queue()
        events_stream._ui_stream_subscribers[queue] = None
        events_stream._ui_stream_clients[queue] = "tab-1"
        try:
            response = client.get(
                "/api/ui/logs/export?format=jsonl&client_id=tab-1",
                headers={"X-Operation-Id": request_id},
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
            events_stream._ui_stream_subscribers.pop(queue, None)
            events_stream._ui_stream_clients.pop(queue, None)

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
                for key in ("timestamp", "level", "source", "message", "relative_path", "group")
            }
            for entry in exported_entries
        ] == [
            {
                "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "level": "error",
                "source": "src.progena.decisions",
                "message": "decision failed",
                "relative_path": "progena/progena.stderr.log",
                "group": "progena",
            },
            {
                "timestamp": queued.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "level": "info",
                "source": "src.progena.decisions",
                "message": "decision queued",
                "relative_path": "progena/progena.stdout.log",
                "group": "progena",
            },
        ]

    def test_logs_export_empty_emits_only_lifecycle_events(self, client, kanban_state_reader):
        """Empty exports do not advertise or emit a data chunk."""

        request_id = "empty-log-export-request"
        queue = asyncio.Queue()
        assert not any(kanban_state_reader.logs_dir.iterdir())
        events_stream._ui_stream_subscribers[queue] = None
        events_stream._ui_stream_clients[queue] = "tab-1"
        try:
            response = client.get(
                "/api/ui/logs/export?client_id=tab-1",
                headers={"X-Operation-Id": request_id},
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
            events_stream._ui_stream_subscribers.pop(queue, None)
            events_stream._ui_stream_clients.pop(queue, None)

        envelopes = [event["envelope"] for event in emitted]
        assert [envelope.type for envelope in envelopes] == [
            "logs.export.started",
            "logs.export.completed",
        ]
        assert all(envelope.payload["request_id"] == request_id for envelope in envelopes)

    def test_logs_export_supports_csv(self, client, kanban_state_reader, ui_stream):
        """Test CSV export output."""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        (kanban_state_reader.logs_dir / "progena.log").write_text(
            f"{timestamp} - __main__ - INFO - Started\n",
            encoding="utf-8",
        )

        response = client.get("/api/ui/logs/export?format=csv&client_id=tab-1")

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        assert "request_id" in data
        assert "event_types" in data


class TestScheduleEndpoint:
    """Tests for /api/ui/schedule endpoint."""

    def test_create_and_patch_keep_paused_in_sync(self, client, monkeypatch, ui_stream):
        """enabled drives the runtime-honoured paused flag on create and patch."""
        store = InMemoryDocumentStore()
        store.save_document(PROGENA_STATE_PREFIX, "schedule", {})
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

        created = client.post(
            "/api/ui/schedules?client_id=tab-1",
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
        doc = store.get_document(PROGENA_STATE_PREFIX, "schedule", {})
        assert doc is not None
        schedule_ids = list(doc.payload.keys())
        assert len(schedule_ids) == 1
        schedule_id = schedule_ids[0]
        entry = doc.payload[schedule_id]
        assert entry["paused"] is False

        patched = client.patch(
            f"/api/ui/widgets/schedule:{schedule_id}?client_id=tab-1",
            json={"enabled": False},
        )
        assert patched.status_code == 202
        patched_data = patched.json()
        assert_acknowledgement_body(patched_data)
        entry = store.get_document(PROGENA_STATE_PREFIX, "schedule", {}).payload[schedule_id]
        assert entry["paused"] is True
        assert entry["enabled"] is False

    def test_broadcast_schedule_snapshot_emits_ui_event(self, monkeypatch):
        """Background schedule updates reach only schedule-topic watchers."""

        store = InMemoryDocumentStore()
        created_at = datetime.now(UTC).isoformat()
        store.save_document(
            PROGENA_STATE_PREFIX,
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
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

        watcher, other = asyncio.Queue(), asyncio.Queue()
        events_stream._ui_stream_subscribers[watcher] = None
        events_stream._ui_stream_subscribers[other] = None
        events_stream._ui_stream_clients[watcher] = "schedule-watcher"
        events_stream._ui_stream_clients[other] = "other-tab"
        events_stream.subscribe_topic("schedule-watcher", schedules_service.SCHEDULES_TOPIC)
        try:
            envelope = schedules_service.broadcast_schedule_snapshot()
            watcher_events = _stream_events(watcher)
            other_events = _stream_events(other)
        finally:
            events_stream._ui_stream_subscribers.pop(watcher, None)
            events_stream._ui_stream_subscribers.pop(other, None)
            events_stream._ui_stream_clients.pop(watcher, None)
            events_stream._ui_stream_clients.pop(other, None)
            events_stream.clear_topics("schedule-watcher")

        assert envelope.type == "schedules.snapshot"
        assert envelope.payload["items"][0]["id"] == "sched-live"
        [streamed] = [event for event in watcher_events if event.type == "schedules.snapshot"]
        assert streamed.payload["items"][0]["message"] == "ping"
        assert not [event for event in other_events if event.type == "schedules.snapshot"]
        assert [event.type for event in watcher_events if event.type == "nav.activity"] == [
            "nav.activity"
        ]
        assert [event.type for event in other_events if event.type == "nav.activity"] == [
            "nav.activity"
        ]

    def test_schedule_snapshot_normalizes_legacy_naive_one_shot_timestamp(self, monkeypatch):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENA_STATE_PREFIX,
            "schedule",
            {
                "cron-once": {
                    "id": "cron-once",
                    "name": "One-off",
                    "message": "ping",
                    "schedule_type": "once",
                    "schedule": "2026-06-19T09:47:00",
                    "scheduled_at": "2026-06-19T09:47:00",
                }
            },
        )
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

        envelope = schedules_service.broadcast_schedule_snapshot()

        item = envelope.payload["items"][0]
        assert item["schedule"] == "2026-06-19T09:47:00+00:00"
        assert item["scheduled_at"] == "2026-06-19T09:47:00+00:00"

    def test_schedule_snapshot_includes_read_only_interrupted_run_views(self, monkeypatch):
        """Schedule run state is derived from the live runtime without persistence writes."""
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENA_STATE_PREFIX,
            "schedule",
            {
                "sched-live": {
                    "id": "sched-live",
                    "name": "Live schedule",
                    "message": "ping",
                    "cron_expression": "0 * * * *",
                    "paused": False,
                }
            },
        )
        store.save_document(
            PROGENA_STATE_PREFIX,
            "health",
            {"runtime_instance": "live", "started_at": "2026-10-28T10:00:00+00:00"},
        )
        store.save_document(
            PROGENA_STATE_PREFIX,
            "schedule_runs",
            [
                {
                    "run_id": "dead",
                    "schedule_id": "sched-live",
                    "thread_id": "sched-live_dead",
                    "status": "running",
                    "runtime_instance": "dead",
                    "started_at": "2026-10-28T09:00:00+00:00",
                },
                {
                    "run_id": "live",
                    "schedule_id": "sched-live",
                    "thread_id": "sched-live_live",
                    "status": "running",
                    "runtime_instance": "live",
                    "started_at": "2026-10-28T10:01:00+00:00",
                },
                {
                    "run_id": "legacy",
                    "schedule_id": "sched-live",
                    "thread_id": "sched-live_legacy",
                    "status": "queued",
                    "started_at": "2026-10-28T09:00:00+00:00",
                },
                {
                    "run_id": "completed",
                    "schedule_id": "sched-live",
                    "thread_id": "sched-live_completed",
                    "status": "completed",
                    "runtime_instance": "dead",
                    "started_at": "2026-10-28T09:00:00+00:00",
                    "completed_at": "2026-10-28T09:01:00+00:00",
                },
            ],
        )
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

        envelope = schedules_service.broadcast_schedule_snapshot()
        runs = {run["run_id"]: run for run in envelope.payload["items"][0]["runs"]}

        assert runs["dead"]["status"] == "interrupted"
        assert runs["live"]["status"] == "running"
        assert runs["legacy"]["status"] == "interrupted"
        assert runs["completed"]["status"] == "completed"
        assert set(runs["completed"]) == {
            "run_id",
            "status",
            "started_at",
            "completed_at",
            "thread_id",
            "error",
        }
        store.delete_document(PROGENA_STATE_PREFIX, "health")
        unknown_live = schedules_service.broadcast_schedule_snapshot()
        statuses = {
            run["run_id"]: run["status"] for run in unknown_live.payload["items"][0]["runs"]
        }
        assert statuses["dead"] == "running"


class TestLogsPage:
    """Tests for the Logs API endpoint."""

    def test_logs_page_loaded_event_carries_complete_causal_payload(
        self, client, kanban_state_reader, ui_stream
    ):
        """The acknowledgement resolves only through the correlated page payload."""

        (kanban_state_reader.logs_dir / "progena.log").write_text(
            "2026-03-11 21:59:52 - __main__ - INFO - Causal log record\n",
            encoding="utf-8",
        )
        request_id = "59251b89-8ddb-4e18-913b-566e2419c139"
        response = client.get(
            "/api/ui/logs?format=json&folder=root&file=progena.log"
            "&start=2026-03-01&end=2026-03-12&limit=10&client_id=tab-1",
            headers={"X-Operation-Id": request_id},
        )

        assert_acknowledged(
            response, ["logs.page.loaded"], request_id=request_id, check_request_id_header=True
        )
        event = ui_stream.get_nowait()["envelope"]
        assert event.type == "logs.page.loaded"
        assert event.payload.get("request_id") == request_id
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
            "file": "progena.log",
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
            "file": "progena.log",
        }
        assert payload["date_range"] == {"start": "2026-03-01", "end": "2026-03-12"}
        assert payload["cursor"] == {
            "after": None,
            "newest_cursor": payload["newest_cursor"],
            "cursor_found": payload["cursor_found"],
        }

    def test_logs_page_loaded_event_reaches_only_the_requesting_tab(
        self, client, kanban_state_reader, ui_stream
    ):
        other_tab: asyncio.Queue = asyncio.Queue()
        events_stream._ui_stream_subscribers[other_tab] = None
        events_stream._ui_stream_clients[other_tab] = "tab-2"
        replay_start = len(events_stream._ui_event_replay)
        try:
            response = client.get("/api/ui/logs?format=json&client_id=tab-1")
            assert response.status_code == 202
            assert ui_stream.get_nowait()["envelope"].type == "logs.page.loaded"
            assert other_tab.empty()
        finally:
            events_stream._ui_stream_subscribers.pop(other_tab, None)
            events_stream._ui_stream_clients.pop(other_tab, None)
        assert not [
            envelope
            for envelope in events_stream._ui_event_replay[replay_start:]
            if envelope.type == "logs.page.loaded"
        ]

    def test_logs_page_requires_an_active_stream_for_the_client(self, client, kanban_state_reader):
        response = client.get("/api/ui/logs?format=json&client_id=missing-tab")

        assert response.status_code == 409


class TestUIStreamEvents:
    """Tests for browser-visible UI stream events."""

    @pytest.fixture(autouse=True)
    def _fresh_shutdown_event(self):
        # The event is process-wide and set for good by any app shutdown; a stream
        # opened while it is set is a draining one and gets no `connection.ready`.
        events_stream._sse_shutdown_event = None
        yield
        events_stream._sse_shutdown_event = None

    def test_broadcast_alerts_state_pushes_full_alert_payload(self):

        clear_alerts()
        alert = create_alert(type="warning", title="Build", message="Build failed")
        queue = asyncio.Queue()
        events_stream._ui_stream_subscribers[queue] = None
        try:
            alerts_routes.broadcast_alerts_state()
            event = queue.get_nowait()
        finally:
            events_stream._ui_stream_subscribers.pop(queue, None)
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

        clear_alerts()
        alert = create_alert(type="info", title="Heads up", message="Alert from SSE")
        monkeypatch.setattr(monitoring_status, "start_status_monitor", lambda: None)
        monkeypatch.setattr(
            monitoring_status, "_get_current_status", AsyncMock(return_value={"connected": True})
        )

        generator = events_routes._generate_ui_stream()
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

    @pytest.mark.asyncio
    async def test_ui_stream_opened_while_draining_gets_shutdown_and_no_ready(self, monkeypatch):
        monkeypatch.setattr(monitoring_status, "start_status_monitor", lambda: None)
        shutdown = events_stream.get_sse_shutdown_event()
        shutdown.set()
        try:
            generator = events_routes._generate_ui_stream()
            events = [event async for event in generator]
        finally:
            shutdown.clear()

        assert [event.data["type"] for event in events] == ["connection.shutdown"]
        assert events_stream._ui_stream_subscribers == {}

    @pytest.mark.asyncio
    async def test_ui_stream_after_query_replays_missed_envelopes(self, monkeypatch):
        """A fresh EventSource cannot send Last-Event-ID; ``after`` resumes it."""
        from fastapi.responses import Response

        monkeypatch.setattr(monitoring_status, "start_status_monitor", lambda: None)
        monkeypatch.setattr(
            monitoring_status, "_get_current_status", AsyncMock(return_value={"connected": True})
        )
        delivered = events_stream.broadcast_ui_event("chat.response.started", {"n": 1})
        missed = events_stream.broadcast_ui_event("chat.response.completed", {"n": 2})

        async def first_event(**cursor: str | None) -> dict[str, Any]:
            generator = events_routes.ui_stream(Response(), client_id=None, **cursor)
            try:
                event = await generator.__anext__()
            finally:
                await generator.aclose()
            assert isinstance(event.data, dict)
            return event.data

        resumed = await first_event(last_event_id=None, after=delivered.id)
        assert resumed["id"] == missed.id
        assert resumed["type"] == "chat.response.completed"

        header_wins = await first_event(last_event_id=missed.id, after=delivered.id)
        assert header_wins["type"] == "connection.ready"


class TestWorkspaceBridgeActions:
    """Tests for backend action bridge endpoints."""

    def test_patch_schedule_widget_updates_schedule_document(self, client, monkeypatch, ui_stream):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENA_STATE_PREFIX,
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
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

        response = client.patch(
            "/api/ui/widgets/schedule:sched-1?client_id=tab-1",
            json={
                "name": "Nightly check",
                "instruction": "Run the nightly review",
                "enabled": False,
                "expression": "0 1 * * *",
            },
        )

        assert response.status_code == 202
        document = store.get_document(PROGENA_STATE_PREFIX, "schedule", {})
        assert document is not None
        updated = document.payload["sched-1"]
        assert updated["name"] == "Nightly check"
        assert updated["message"] == "Run the nightly review"
        assert updated["schedule"] == "0 1 * * *"
        assert updated["enabled"] is False

    def test_patch_mcp_widget_updates_mcp_config_document(self, client, monkeypatch, ui_stream):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENA_STATE_PREFIX,
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
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

        response = client.patch(
            "/api/ui/widgets/mcp:jina?client_id=tab-1",
            json={
                "enabled": True,
                "description": "Live web search",
                "url": "https://mcp.jina.ai/v1?include_tags=search,read",
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)

        document = store.get_document(PROGENA_STATE_PREFIX, "mcp", {})
        assert document is not None
        updated = document.payload
        assert updated["mcpServers"]["jina"]["enabled"] is True
        assert updated["mcpServers"]["jina"]["description"] == "Live web search"
        assert (
            updated["mcpServers"]["jina"]["url"]
            == "https://mcp.jina.ai/v1?include_tags=search,read"
        )

    def test_mcp_snapshot_omits_tool_detail_content_route_returns_it(
        self, client, monkeypatch, ui_stream
    ):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENA_STATE_PREFIX,
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
            PROGENA_STATE_PREFIX,
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
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

        list_request_id = "mcp-list-lean"
        list_response = client.get(
            "/api/ui/mcp?client_id=tab-1", headers={"X-Operation-Id": list_request_id}
        )
        assert_acknowledged(
            list_response,
            ["mcp.snapshot"],
            request_id=list_request_id,
            check_request_id_header=True,
        )
        [list_envelope] = [
            event
            for event in _stream_events(ui_stream)
            if event.payload.get("request_id") == list_request_id
        ]
        jina_item = next(item for item in list_envelope.payload["items"] if item["name"] == "jina")
        assert "tools" not in jina_item["data"]

        detail_request_id = "mcp-detail-fetch"
        detail_response = client.get(
            "/api/ui/mcp/content",
            params={"id": "mcp:jina", "client_id": "tab-1"},
            headers={"X-Operation-Id": detail_request_id},
        )
        assert_acknowledged(
            detail_response,
            ["mcp.server.detail.updated"],
            request_id=detail_request_id,
            check_request_id_header=True,
        )
        [detail_envelope] = [
            event
            for event in _stream_events(ui_stream)
            if event.payload.get("request_id") == detail_request_id
        ]
        detail_tools = detail_envelope.payload["data"]["tools"]
        assert {tool["name"] for tool in detail_tools} == {"search", "read"}

    def test_mcp_content_route_404s_for_unknown_server(self, client, monkeypatch, ui_stream):
        store = InMemoryDocumentStore()
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

        response = client.get(
            "/api/ui/mcp/content", params={"id": "mcp:missing", "client_id": "tab-1"}
        )

        assert response.status_code == 404

    def test_patch_mcp_tool_broadcasts_server_detail_update(self, client, monkeypatch, ui_stream):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENA_STATE_PREFIX,
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
            PROGENA_STATE_PREFIX,
            "mcp_tools",
            {"tools": [{"name": "search", "server": "jina", "description": "", "enabled": True}]},
        )
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

        response = client.patch(
            "/api/ui/widgets/mcp_tool:search?client_id=tab-1", json={"enabled": False}
        )

        assert response.status_code == 202
        assert set(response.json()["event_types"]) == {
            "widgets.updated",
            "mcp.snapshot",
            "mcp.server.detail.updated",
        }
        detail_event = next(
            event
            for event in _stream_events(ui_stream)
            if event.type == "mcp.server.detail.updated"
        )
        detail_tools = detail_event.payload["data"]["tools"]
        assert next(tool for tool in detail_tools if tool["name"] == "search")["enabled"] is False

    def test_refresh_agent_widget_returns_live_card_details(
        self, client, tmp_path, monkeypatch, ui_stream
    ):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENA_STATE_PREFIX,
            "registry",
            {
                "agents": [
                    {
                        "name": "example_agent",
                        "kind": "a2a_agent",
                        "version": "0.2.0",
                        "description": "Example health",
                        "entrypoint": "python example.py",
                        "transport": "http",
                        "enabled": True,
                        "url": "http://example.test/example",
                        "created_at": "2026-04-01T00:00:00Z",
                        "updated_at": "2026-04-01T00:00:00Z",
                        "status": "running",
                    }
                ]
            },
        )
        store.save_document(PROGENA_STATE_PREFIX, "mcp", {"mcpServers": {}})

        state_reader = StateReader(
            document_store=store,
        )
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

        async def fake_inspect_agent_widget(widget, reader):
            assert widget["id"] == "agent:example_agent"
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
                "agent_card_url": "http://example.test/example/.well-known/agent-card.json",
                "url": "http://example.test/example",
                "version": "0.2.0",
            }

        monkeypatch.setattr(
            "src.backend.schedules.service._inspect_agent_widget",
            fake_inspect_agent_widget,
        )

        response = client.post("/api/ui/widgets/agent:example_agent/refresh?client_id=tab-1")

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)

        # The calling tab receives widgets.updated with the transient inspection data.
        widget_event = next(
            item["envelope"]
            for item in list(ui_stream._queue)
            if item["envelope"].type == "widgets.updated"
        )
        assert widget_event.type == "widgets.updated"

    def test_refresh_mcp_widget_is_rejected(self, client, monkeypatch, ui_stream):
        # MCP tool discovery is owned by the Progena runtime's MCP init (and
        # ``mcp_refresh``); the backend no longer probes servers itself.
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENA_STATE_PREFIX,
            "registry",
            {"agents": []},
        )
        store.save_document(
            PROGENA_STATE_PREFIX,
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
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)
        response = client.post("/api/ui/widgets/mcp:jina/refresh?client_id=tab-1")
        assert response.status_code == 400
        assert "agent" in response.json()["detail"].lower()

    def test_create_schedule_writes_schedule_document(self, client, monkeypatch, ui_stream):
        store = InMemoryDocumentStore()
        store.save_document(PROGENA_STATE_PREFIX, "schedule", {})
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

        response = client.post(
            "/api/ui/schedules?client_id=tab-1",
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
        assert any(event.type == "schedules.snapshot" for event in _stream_events(ui_stream))
        document = store.get_document(PROGENA_STATE_PREFIX, "schedule", {})
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
        self, client, monkeypatch, ui_stream
    ):
        store = InMemoryDocumentStore()
        store.save_document(PROGENA_STATE_PREFIX, "schedule", {})
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)

        response = client.post(
            "/api/ui/schedules?client_id=tab-1",
            json={
                "name": "One-off reminder",
                "instruction": "Reply once.",
                "schedule_type": "once",
                "expression": "2026-06-19T09:47:00",
                "timezone": "UTC",
                "enabled": True,
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert_acknowledgement_body(data)
        assert any(event.type == "schedules.snapshot" for event in _stream_events(ui_stream))
        document = store.get_document(PROGENA_STATE_PREFIX, "schedule", {})
        assert document is not None
        schedule_ids = list(document.payload.keys())
        assert len(schedule_ids) == 1
        schedule_id = schedule_ids[0]
        assert schedule_id.startswith("cron_")
        updated = document.payload[schedule_id]
        assert updated["id"].startswith("cron_")
        assert updated["schedule_type"] == "once"
        assert updated["schedule"] == "2026-06-19T09:47:00+00:00"
        assert updated["scheduled_at"] == "2026-06-19T09:47:00+00:00"
        assert "cron_expression" not in updated

    def test_create_event_widget_writes_event_widget_file(
        self, client, kanban_state_reader, ui_stream
    ):
        response = client.post(
            "/api/ui/events?client_id=tab-1",
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

        widgets_resp = client.get("/api/ui/widgets?client_id=tab-1")
        assert widgets_resp.status_code == 202
        widgets_items = next(
            item["envelope"].payload["items"]
            for item in list(ui_stream._queue)
            if item["envelope"].type == "widgets.updated"
        )
        assert any(widget["id"] == f"event:{event_widget_id}" for widget in widgets_items)

    def test_patch_event_widget_updates_config_file(self, client, kanban_state_reader, ui_stream):
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
            "/api/ui/widgets/event:evt-1?client_id=tab-1",
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
        self, client, tasks_state_reader, ui_stream
    ):
        store = tasks_state_reader.document_store
        client.post(
            "/api/ui/events?client_id=tab-1",
            json={"name": "Created", "event_type": "kanban_card.created"},
        )
        stored_doc = store.get_document(BACKEND_STATE_PREFIX, "event_widgets", None)
        stored = stored_doc.payload if stored_doc else {}
        created_widget_id = next(iter(stored.keys()))

        client.post(
            "/api/ui/events?client_id=tab-1",
            json={"name": "Updated", "event_type": "kanban_card.updated"},
        )
        stored_doc = store.get_document(BACKEND_STATE_PREFIX, "event_widgets", None)
        stored = stored_doc.payload if stored_doc else {}
        updated_widget_id = next(key for key in stored if key != created_widget_id)

        client.post(
            "/api/ui/kanban/cards?client_id=tab-1",
            json={"title": "Evented card", "summary": "Initial summary"},
        )
        await events_dispatch.get_document_event_dispatcher().drain()
        board = store.get_document(PROGENA_STATE_PREFIX, "kanban_board", None)
        card_id = board.payload["card_order"][-1]

        _stream_events(ui_stream)
        widgets_resp = client.get("/api/ui/widgets?client_id=tab-1")
        assert widgets_resp.status_code == 202
        widgets_items = next(
            event.payload["items"]
            for event in _stream_events(ui_stream)
            if event.type == "widgets.updated"
        )
        created_w = next(
            widget for widget in widgets_items if widget["id"] == f"event:{created_widget_id}"
        )
        created_data = created_w["data"]
        assert created_data["trigger_count"] == 1
        assert created_data["last_payload"]["id"] == card_id
        assert created_data["last_payload"]["document_type"] == "kanban_card"
        assert created_data["last_payload"]["op"] == "created"

        update_response = client.patch(
            f"/api/ui/kanban/cards/{card_id}?client_id=tab-1",
            json={"summary": "Updated summary"},
        )
        assert update_response.status_code == 202
        await events_dispatch.get_document_event_dispatcher().drain()
        _stream_events(ui_stream)
        widgets_resp = client.get("/api/ui/widgets?client_id=tab-1")
        assert widgets_resp.status_code == 202
        widgets_items = next(
            event.payload["items"]
            for event in _stream_events(ui_stream)
            if event.type == "widgets.updated"
        )
        updated_w = next(
            widget for widget in widgets_items if widget["id"] == f"event:{updated_widget_id}"
        )
        updated_data = updated_w["data"]
        assert updated_data["trigger_count"] == 1
        assert updated_data["last_payload"]["id"] == card_id
        assert updated_data["last_payload"]["document_type"] == "kanban_card"
        assert updated_data["last_payload"]["op"] == "updated"

    @pytest.mark.asyncio
    async def test_kanban_update_and_delete_events_trigger_matching_widgets(
        self, client, tasks_state_reader, ui_stream
    ):
        store = tasks_state_reader.document_store
        client.post(
            "/api/ui/events?client_id=tab-1",
            json={"name": "Updated", "event_type": "kanban_card.updated"},
        )
        stored_doc = store.get_document(BACKEND_STATE_PREFIX, "event_widgets", None)
        stored = stored_doc.payload if stored_doc else {}
        updated_widget_id = next(iter(stored.keys()))

        client.post(
            "/api/ui/events?client_id=tab-1",
            json={"name": "Deleted", "event_type": "kanban_card.deleted"},
        )
        stored_doc = store.get_document(BACKEND_STATE_PREFIX, "event_widgets", None)
        stored = stored_doc.payload if stored_doc else {}
        deleted_widget_id = next(key for key in stored if key != updated_widget_id)

        client.post("/api/ui/kanban/cards?client_id=tab-1", json={"title": "First card"})
        await events_dispatch.get_document_event_dispatcher().drain()
        board = store.get_document(PROGENA_STATE_PREFIX, "kanban_board", None)
        first_id = board.payload["card_order"][0]
        client.post("/api/ui/kanban/cards?client_id=tab-1", json={"title": "Second card"})
        await events_dispatch.get_document_event_dispatcher().drain()
        board = store.get_document(PROGENA_STATE_PREFIX, "kanban_board", None)
        second_id = board.payload["card_order"][-1]

        move_response = client.patch(
            f"/api/ui/kanban/cards/{second_id}?client_id=tab-1",
            json={"column_id": "done"},
        )
        assert move_response.status_code == 202
        await events_dispatch.get_document_event_dispatcher().drain()

        _stream_events(ui_stream)
        widgets_resp = client.get("/api/ui/widgets?client_id=tab-1")
        assert widgets_resp.status_code == 202
        widgets_items = next(
            event.payload["items"]
            for event in _stream_events(ui_stream)
            if event.type == "widgets.updated"
        )
        updated_w = next(
            widget for widget in widgets_items if widget["id"] == f"event:{updated_widget_id}"
        )
        updated_data = updated_w["data"]
        assert updated_data["trigger_count"] == 1
        assert updated_data["last_payload"]["id"] == second_id
        assert updated_data["last_payload"]["document_type"] == "kanban_card"
        assert updated_data["last_payload"]["op"] == "updated"

        delete_response = client.delete(f"/api/ui/kanban/cards/{first_id}?client_id=tab-1")
        assert delete_response.status_code == 202
        await events_dispatch.get_document_event_dispatcher().drain()

        _stream_events(ui_stream)
        widgets_resp = client.get("/api/ui/widgets?client_id=tab-1")
        assert widgets_resp.status_code == 202
        widgets_items = next(
            event.payload["items"]
            for event in _stream_events(ui_stream)
            if event.type == "widgets.updated"
        )
        deleted_w = next(
            widget for widget in widgets_items if widget["id"] == f"event:{deleted_widget_id}"
        )
        deleted_data = deleted_w["data"]
        assert deleted_data["trigger_count"] == 1
        assert deleted_data["last_payload"]["id"] == first_id
        assert deleted_data["last_payload"]["document_type"] == "kanban_card"
        assert deleted_data["last_payload"]["op"] == "deleted"

    def test_run_and_generate_actions_use_existing_client(self, client, ui_stream):
        original_client = lifecycle_state.client
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(
            return_value=AgentResponse(
                response="bridge-response", reasoning=None, reasoning_tokens=None
            )
        )
        lifecycle_state.client = mock_client
        try:
            run_response = client.post(
                "/api/ui/actions/run?client_id=tab-1",
                json={"widget_id": "prompt:test", "prompt": "Run this", "input": "From upstream"},
            )
            generate_response = client.post(
                "/api/ui/actions/generate?client_id=tab-1",
                json={
                    "widget_id": "prompt:test",
                    "prompt": "Generate this",
                    "input": "Generated input",
                },
            )

            assert run_response.status_code == 202
            assert generate_response.status_code == 202
            run_ack = run_response.json()
            generate_ack = generate_response.json()
            assert_acknowledgement_body(run_ack)
            assert_acknowledgement_body(generate_ack)
            assert {
                event.payload["request_id"]
                for event in _stream_events(ui_stream)
                if event.type == "widgets.updated"
            } == {run_ack["request_id"], generate_ack["request_id"]}
            assert mock_client.send_message.await_count == 2
            sent_messages = [call.args[0] for call in mock_client.send_message.await_args_list]
            assert sent_messages == [
                "Widget ID: prompt:test\n\nPrompt:\nRun this\n\nInput:\nFrom upstream",
                "Widget ID: prompt:test\n\nInput:\nGenerated input\n\nGenerate this",
            ]
        finally:
            lifecycle_state.client = original_client

    def test_workspace_actions_generate_distinct_threads_per_execution(self, client, ui_stream):
        original_client = lifecycle_state.client
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(
            return_value=AgentResponse(
                response="bridge-response", reasoning=None, reasoning_tokens=None
            )
        )
        lifecycle_state.client = mock_client
        try:
            first = client.post(
                "/api/ui/actions/run?client_id=tab-1",
                json={"widget_id": "prompt:first", "prompt": "Run first"},
            )
            second = client.post(
                "/api/ui/actions/run?client_id=tab-1",
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
            lifecycle_state.client = original_client

    def test_workspace_actions_reuse_explicit_thread(self, client, ui_stream):
        original_client = lifecycle_state.client
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(
            return_value=AgentResponse(
                response="bridge-response", reasoning=None, reasoning_tokens=None
            )
        )
        lifecycle_state.client = mock_client
        try:
            thread_id = "thread_shared_chain"

            run_response = client.post(
                "/api/ui/actions/run?client_id=tab-1",
                json={
                    "widget_id": "prompt:root",
                    "root_widget_id": "prompt:root",
                    "prompt": "Run root",
                    "thread_id": thread_id,
                },
            )
            generate_response = client.post(
                "/api/ui/actions/generate?client_id=tab-1",
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
            lifecycle_state.client = original_client
