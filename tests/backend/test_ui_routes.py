"""
Tests for Chat UI API routes.

Note: The backend is now API-only and does not serve static files.
The frontend runs separately via npm/Vite.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.backend.alerts import clear_alerts, create_alert
from app.backend.document_store import (
    PROGENITOR_STATE_PREFIX,
    CheckpointThreadSnapshot,
    InMemoryDocumentStore,
    set_default_document_store,
)
from app.backend.main import app
from app.backend.models import AgentResponse
from app.backend.state_reader import StateReader
from app.backend.threads import clear_threads


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
def kanban_state_reader(tmp_path, monkeypatch):
    """Provide an isolated state reader for kanban persistence tests."""
    state_dir = tmp_path / "data" / "state"
    config_dir = tmp_path / "data" / "config"
    sessions_dir = tmp_path / "data" / "sessions"
    logs_dir = tmp_path / "logs"

    state_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    sessions_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)

    state_reader = StateReader(
        state_dir=state_dir,
        config_dir=config_dir,
        sessions_dir=sessions_dir,
        logs_dir=logs_dir,
    )
    monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)
    yield state_reader


@pytest.fixture
def document_store():
    store = InMemoryDocumentStore()
    set_default_document_store(store)
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

    def test_ui_status_endpoint(self, client):
        """Test /api/ui/status returns valid JSON."""
        response = client.get("/api/ui/status/snapshot")
        assert response.status_code == 200
        data = response.json()
        assert "connected" in data
        assert "server_status" in data
        assert "progenitor_running" in data

    def test_ui_agents_endpoint(self, client):
        """Test /api/ui/agents returns agent list."""
        response = client.get("/api/ui/agents")
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert "total" in data
        assert isinstance(data["agents"], list)

    def test_ui_tasks_endpoint(self, client):
        """Test /api/ui/tasks returns task list."""
        response = client.get("/api/ui/tasks")
        assert response.status_code == 200
        data = response.json()
        assert "tasks" in data
        assert "total" in data

    def test_workspace_endpoint(self, client):
        response = client.get("/api/ui/workspace")
        assert response.status_code == 200
        data = response.json()
        assert "workspace" in data
        assert "health" in data
        assert "sessions" in data
        assert "widgets" in data

    def test_widgets_endpoint(self, client):
        response = client.get("/api/ui/widgets")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_database_schema_endpoint_reports_store_schema(self, client, document_store):
        response = client.get("/api/ui/database/schema")

        assert response.status_code == 200
        tables = response.json()["tables"]
        store_schema = next(table for table in tables if table["table"] == "store")
        assert {column["name"] for column in store_schema["columns"]} >= {
            "prefix",
            "key",
            "value",
            "updated_at",
        }

    def test_chat_endpoints_store_metadata_without_transcripts(self, client, document_store):
        create_response = client.post(
            "/api/ui/chats",
            json={"title": "Backend chat", "thread_id": "thread-1"},
        )

        assert create_response.status_code == 200
        chat_id = create_response.json()["session"]["chat_session_id"]
        assert chat_id.startswith("conv_")
        stored = document_store.get_document("backend.chat.sessions", chat_id)
        assert stored is not None
        assert stored.payload["title"] == "Backend chat"
        assert "messages" not in stored.payload

        patch_response = client.patch(
            f"/api/ui/chats/{chat_id}",
            json={"pinned": True, "shared": True, "archived": True},
        )
        assert patch_response.status_code == 200
        assert patch_response.json()["session"]["pinned"] is True

        list_response = client.get("/api/ui/chats?include_archived=true")
        assert list_response.status_code == 200
        assert list_response.json()["total"] == 1
        assert list_response.json()["sessions"][0]["chat_session_id"] == chat_id

    def test_chat_detail_request_emits_request_scoped_selection_event(self, client, document_store):
        from app.backend import ui_routes

        create_response = client.post(
            "/api/ui/chats",
            json={"title": "Selectable chat", "thread_id": "thread-select"},
        )
        assert create_response.status_code == 200
        chat_id = create_response.json()["session"]["chat_session_id"]
        assert chat_id.startswith("conv_")

        detail_response = client.get(f"/api/ui/chats/{chat_id}?request_id=request-123")

        assert detail_response.status_code == 200
        upserted = ui_routes._ui_event_replay[-2]
        selected = ui_routes._ui_event_replay[-1]
        assert upserted.type == "chat.session.upserted"
        assert upserted.payload["session"]["chat_session_id"] == chat_id
        assert selected.type == "chat.session.selected"
        assert selected.session_id == chat_id
        assert selected.payload["request_id"] == "request-123"
        assert selected.payload["session"]["chat_session_id"] == chat_id

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
        create_response = client.post(
            "/api/ui/chats",
            json={"title": "New chat", "progenitor_session_id": "sess-history"},
        )
        assert create_response.status_code == 200
        chat_id = create_response.json()["session"]["chat_session_id"]
        assert chat_id.startswith("conv_")

        list_response = client.get("/api/ui/chats?include_archived=true")
        detail_response = client.get(f"/api/ui/chats/{chat_id}?request_id=request-history")

        assert list_response.status_code == 200
        listed = next(
            session
            for session in list_response.json()["sessions"]
            if session["chat_session_id"] == chat_id
        )
        assert listed["title"] == "count backwards"
        assert listed["message_count"] == 2
        assert listed["preview"] == "99, 98, 97"

        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["thread_id"] == "thread_sess-history"
        assert detail["checkpoint_id"] == "checkpoint-1"
        assert [message["content"] for message in detail["messages"]] == [
            "count backwards",
            "99, 98, 97",
        ]
        assert detail["messages"][1]["reasoning_text"] == "Need to count down."
        assert detail["messages"][1]["reasoning_token_count"] == 3
        assert detail["messages"][1]["token_count"] == 12

    def test_chat_detail_loads_checkpoint_only_thread_ids_without_sess_prefix(
        self,
        client,
        document_store,
    ):
        thread_id = "2026-06-06T09:53:08.888313+00:00"
        document_store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id=thread_id,
                checkpoint_id="checkpoint-legacy-thread",
                checkpoint_ts="2026-06-06T09:53:08.888313+00:00",
                messages=[
                    {"id": "m1", "type": "human", "content": "legacy hello"},
                    {"id": "m2", "type": "ai", "content": "legacy reply"},
                ],
            )
        )

        list_response = client.get("/api/ui/chats?include_archived=true")
        assert list_response.status_code == 200
        listed = next(
            session
            for session in list_response.json()["sessions"]
            if session["chat_session_id"] == thread_id
        )
        assert listed["thread_id"] == thread_id

        detail_response = client.get(f"/api/ui/chats/{thread_id}?request_id=request-legacy-thread")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["chat_session_id"] == thread_id
        assert detail["thread_id"] == thread_id
        assert [message["content"] for message in detail["messages"]] == [
            "legacy hello",
            "legacy reply",
        ]

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
        assert list_response.status_code == 200
        assert all(
            session["thread_id"] != "2026-06-19T09:00:00+00:00"
            for session in list_response.json()["sessions"]
        )

    def test_chat_delete_sets_soft_delete_flag_and_hides_from_list(self, client, document_store):
        create_response = client.post(
            "/api/ui/chats",
            json={"title": "Disposable chat", "thread_id": "thread-delete"},
        )
        assert create_response.status_code == 200
        chat_id = create_response.json()["session"]["chat_session_id"]

        delete_response = client.delete(f"/api/ui/chats/{chat_id}")

        assert delete_response.status_code == 204
        stored = document_store.get_document("backend.chat.sessions", chat_id)
        assert stored is not None
        assert stored.payload["deleted"] is True
        list_response = client.get("/api/ui/chats?include_archived=true")
        assert list_response.status_code == 200
        assert all(
            session["chat_session_id"] != chat_id for session in list_response.json()["sessions"]
        )

    def test_chat_patch_and_delete_materialize_current_state_card(
        self,
        client,
        document_store,
        kanban_state_reader,
    ):
        state_path = kanban_state_reader.state_dir / "current_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "session_id": "current-session-1",
                    "title": "Current synthetic chat",
                    "messages": [{"id": "m1", "role": "user", "content": "hello"}],
                }
            ),
            encoding="utf-8",
        )
        kanban_state_reader.invalidate(state_path)

        patch_response = client.patch(
            "/api/ui/chats/current-session-1",
            json={"pinned": True, "archived": True},
        )

        assert patch_response.status_code == 200
        assert patch_response.json()["session"]["pinned"] is True
        stored = document_store.get_document("backend.chat.sessions", "current-session-1")
        assert stored is not None
        assert stored.payload["pinned"] is True
        assert stored.payload["archived"] is True

        delete_response = client.delete("/api/ui/chats/current-session-1")

        assert delete_response.status_code == 204
        stored_after_delete = document_store.get_document(
            "backend.chat.sessions", "current-session-1"
        )
        assert stored_after_delete is not None
        assert stored_after_delete.payload["deleted"] is True

    def test_widget_schemas_include_event_widget(self, client):
        response = client.get("/api/ui/widget-schemas")

        assert response.status_code == 200
        schemas = {schema["type"]: schema for schema in response.json()["schemas"]}
        assert "event" in schemas
        event_schema = schemas["event"]
        identity_section = next(
            section for section in event_schema["sections"] if section["id"] == "identity"
        )
        event_type_field = next(
            field for field in identity_section["fields"] if field["key"] == "eventType"
        )
        assert event_type_field["control"] == "select"
        assert {option["value"] for option in event_type_field["options"]} == {
            "kanban.card.created",
            "kanban.card.updated",
            "kanban.card.moved",
            "kanban.card.deleted",
            "kanban.card.status_changed",
        }

    def test_widget_schemas_include_user_widget_tools(self, client):
        response = client.get("/api/ui/widget-schemas")

        assert response.status_code == 200
        schemas = {schema["type"]: schema for schema in response.json()["schemas"]}
        user_schema = schemas["user"]
        identity_section = next(
            section for section in user_schema["sections"] if section["id"] == "identity"
        )
        message_type_field = next(
            field for field in identity_section["fields"] if field["key"] == "messageType"
        )
        assert {option["value"] for option in message_type_field["options"]} == {
            "say_to_user",
            "notify_user",
            "ask_user",
        }

    def test_widget_schemas_include_agent_and_mcp_refresh_tabs(self, client):
        response = client.get("/api/ui/widget-schemas")

        assert response.status_code == 200
        schemas = {schema["type"]: schema for schema in response.json()["schemas"]}
        assert schemas["prompt"]["help_text"] == (
            "Stores prompt text, targets an agent, and runs or generates code from the latest prompt."
        )
        assert schemas["user"]["help_text"] == (
            "Configures a user-facing tool call so the current run can send a message, "
            "notification, or question."
        )
        assert [tab["id"] for tab in schemas["agent"]["tabs"]] == [
            "overview",
            "details",
            "capabilities",
            "skills",
            "logs",
        ]
        assert schemas["agent"]["help_text"] == (
            "Shows a linked agent, its recent activity, and the latest thread-linked output for this card."
        )
        assert [tab["id"] for tab in schemas["mcp"]["tabs"]] == [
            "overview",
            "details",
            "tools",
            "resources",
            "prompts",
            "logs",
        ]
        assert schemas["mcp"]["help_text"] == (
            "Shows an MCP integration, its recent activity, and the latest thread-linked output for this card."
        )

    def test_prompts_endpoint_lists_nested_prompts(self, client, tmp_path, monkeypatch):
        prompts_dir = tmp_path / "data" / "prompts"
        (prompts_dir / "builder_agent").mkdir(parents=True)
        (prompts_dir / "progenitor" / "nested").mkdir(parents=True)
        (prompts_dir / "builder_agent" / "think.md").write_text("builder content", encoding="utf-8")
        (prompts_dir / "progenitor" / "nested" / "decide.md").write_text(
            "progenitor content", encoding="utf-8"
        )
        monkeypatch.delenv("PROGENITOR_PROMPTS_DIR", raising=False)
        monkeypatch.setenv("PROGENITOR_DATA_DIR", str(prompts_dir.parent))

        response = client.get("/api/ui/prompts")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert {prompt["path"] for prompt in data["prompts"]} == {
            "builder_agent/think.md",
            "progenitor/nested/decide.md",
        }
        builder_prompt = next(
            prompt for prompt in data["prompts"] if prompt["path"] == "builder_agent/think.md"
        )
        assert builder_prompt["primary_agent"] == "builder_agent"
        assert "builder_agent" in builder_prompt["associated_agents"]

    def test_prompts_endpoint_updates_prompt_content(self, client, tmp_path, monkeypatch):
        prompts_dir = tmp_path / "data" / "prompts"
        (prompts_dir / "builder_agent").mkdir(parents=True)
        prompt_path = prompts_dir / "builder_agent" / "think.md"
        prompt_path.write_text("before", encoding="utf-8")
        monkeypatch.delenv("PROGENITOR_PROMPTS_DIR", raising=False)
        monkeypatch.setenv("PROGENITOR_DATA_DIR", str(prompts_dir.parent))

        response = client.put(
            "/api/ui/prompts/content",
            json={"path": "builder_agent/think.md", "content": "after"},
        )

        assert response.status_code == 200
        assert response.json()["content"] == "after"
        assert prompt_path.read_text(encoding="utf-8") == "after"

    def test_skills_endpoint_lists_updates_and_creates_skills(self, client, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        prompts_dir = data_dir / "prompts"
        skills_dir = data_dir / "skills"
        existing_skill = skills_dir / "progenitor" / "runtime-inspection" / "SKILL.md"
        prompts_dir.mkdir(parents=True)
        existing_skill.parent.mkdir(parents=True)
        existing_skill.write_text(
            "---\nname: Runtime Inspection\ndescription: Inspect runtime state.\n---\n\nUse tools.",
            encoding="utf-8",
        )
        monkeypatch.delenv("PROGENITOR_PROMPTS_DIR", raising=False)
        monkeypatch.setenv("PROGENITOR_DATA_DIR", str(data_dir))

        list_response = client.get("/api/ui/skills")

        assert list_response.status_code == 200
        list_data = list_response.json()
        assert list_data["total"] == 1
        assert list_data["skills"][0]["path"] == "progenitor/runtime-inspection/SKILL.md"
        assert list_data["skills"][0]["title"] == "Runtime Inspection"

        update_response = client.put(
            "/api/ui/skills/content",
            json={"path": "progenitor/runtime-inspection/SKILL.md", "content": "after"},
        )

        assert update_response.status_code == 200
        assert update_response.json()["content"] == "after"
        assert existing_skill.read_text(encoding="utf-8") == "after"

        create_response = client.post(
            "/api/ui/skills",
            json={
                "agent": "builder_agent",
                "name": "repo-triage",
                "content": "---\nname: Repo Triage\n---\n\nInspect repo state.",
            },
        )

        assert create_response.status_code == 201
        assert create_response.json()["path"] == "builder_agent/repo-triage/SKILL.md"
        assert (skills_dir / "builder_agent" / "repo-triage" / "SKILL.md").is_file()

    def test_kanban_endpoint_returns_default_board(self, client, kanban_state_reader):
        response = client.get("/api/ui/kanban")

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Delivery Kanban"
        assert [column["id"] for column in data["columns"]] == [
            "backlog",
            "plan",
            "build",
            "validate",
            "done",
        ]
        assert data["cards"] == []
        assert {status["id"] for status in data["card_schema"]["statuses"]} == {
            "idle",
            "running",
            "complete",
            "failed",
            "blocked",
        }

    def test_kanban_card_create_and_patch_persist(self, client, kanban_state_reader):
        create_response = client.post(
            "/api/ui/kanban/cards",
            json={"title": "Build kanban board", "summary": "Server first"},
        )

        assert create_response.status_code == 200
        created = create_response.json()
        assert created["column_id"] == "backlog"
        assert created["status"] == "idle"
        assert (kanban_state_reader.config_dir / "kanban.json").exists()

        patch_response = client.patch(
            f"/api/ui/kanban/cards/{created['id']}",
            json={
                "column_id": "build",
                "status": "running",
                "agent_name": "builder_progenitor",
                "branch_name": "feat/kanban-board",
                "labels": ["frontend", "live-first"],
            },
        )

        assert patch_response.status_code == 200
        updated = patch_response.json()
        assert updated["column_id"] == "build"
        assert updated["status"] == "running"
        assert updated["agent_name"] == "builder_progenitor"
        assert updated["branch_name"] == "feat/kanban-board"
        assert updated["labels"] == ["frontend", "live-first"]
        assert updated["started_at"] is not None
        assert updated["position"] == 0

        board_response = client.get("/api/ui/kanban")
        board = board_response.json()
        assert board["cards"][0]["id"] == created["id"]
        assert board["cards"][0]["column_id"] == "build"

    def test_kanban_card_reorder_persists_positions(self, client, kanban_state_reader):
        first = client.post("/api/ui/kanban/cards", json={"title": "First card"}).json()
        client.post("/api/ui/kanban/cards", json={"title": "Second card"})
        third = client.post("/api/ui/kanban/cards", json={"title": "Third card"}).json()

        reorder_response = client.patch(
            f"/api/ui/kanban/cards/{third['id']}",
            json={"position": 1},
        )

        assert reorder_response.status_code == 200
        reordered = reorder_response.json()
        assert reordered["position"] == 1

        move_response = client.patch(
            f"/api/ui/kanban/cards/{first['id']}",
            json={"column_id": "build", "position": 0},
        )

        assert move_response.status_code == 200
        moved = move_response.json()
        assert moved["column_id"] == "build"
        assert moved["position"] == 0

        board = client.get("/api/ui/kanban").json()
        backlog_cards = [card for card in board["cards"] if card["column_id"] == "backlog"]
        build_cards = [card for card in board["cards"] if card["column_id"] == "build"]

        assert [card["title"] for card in backlog_cards] == ["Third card", "Second card"]
        assert [card["position"] for card in backlog_cards] == [0, 1]
        assert [card["title"] for card in build_cards] == ["First card"]
        assert build_cards[0]["position"] == 0

    def test_kanban_normalizes_legacy_columns(self, client, kanban_state_reader):
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
        (kanban_state_reader.config_dir / "kanban.json").write_text(
            json.dumps(legacy_board),
            encoding="utf-8",
        )
        kanban_state_reader.invalidate(kanban_state_reader.config_dir / "kanban.json")

        board = client.get("/api/ui/kanban").json()

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

    def test_kanban_card_delete_persists(self, client, kanban_state_reader):
        created = client.post("/api/ui/kanban/cards", json={"title": "Disposable card"}).json()

        delete_response = client.delete(f"/api/ui/kanban/cards/{created['id']}")

        assert delete_response.status_code == 204
        board = client.get("/api/ui/kanban").json()
        assert all(card["id"] != created["id"] for card in board["cards"])

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

        assert create_response.status_code == 200
        created = create_response.json()
        schedule_path = kanban_state_reader.config_dir / "schedule.json"
        before_delete = json.loads(schedule_path.read_text(encoding="utf-8"))
        assert [entry["id"] for entry in before_delete] == [created["id"].split(":", 1)[1]]

        delete_response = client.delete(f"/api/ui/widgets/{created['id']}")

        assert delete_response.status_code == 200
        assert delete_response.json() == {
            "widget_id": created["id"],
            "removed": True,
            "already_removed": False,
            "warning": None,
        }
        after_delete = json.loads(schedule_path.read_text(encoding="utf-8"))
        assert after_delete == []
        widgets = client.get("/api/ui/widgets").json()
        assert created["id"] not in {widget["id"] for widget in widgets["items"]}

        repeated_delete = client.delete(f"/api/ui/widgets/{created['id']}")

        assert repeated_delete.status_code == 200
        repeated_payload = repeated_delete.json()
        assert repeated_payload["widget_id"] == created["id"]
        assert repeated_payload["removed"] is True
        assert repeated_payload["already_removed"] is True
        assert "already removed" in repeated_payload["warning"]

    def test_schedules_alias_endpoint(self, client):
        response = client.get("/api/ui/schedules")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_ui_alerts_endpoint(self, client):
        """Test /api/ui/alerts returns alerts."""
        response = client.get("/api/ui/alerts")
        assert response.status_code == 200
        data = response.json()
        assert "alerts" in data
        assert "total" in data
        assert "unread" in data

    def test_status_snapshot_returns_full_alert_payload(self, client):
        clear_alerts()
        created = create_alert(type="warning", title="Build", message="Build failed")

        try:
            response = client.get("/api/ui/status/snapshot")
        finally:
            clear_alerts()

        assert response.status_code == 200
        data = response.json()
        assert data["alerts"]["unread"] == 1
        assert data["alerts"]["total"] == 1
        assert data["alerts"]["alerts"][0]["id"] == created.id
        assert data["alerts"]["alerts"][0]["title"] == "Build"

    def test_mark_alerts_viewed_endpoint(self, client):
        first = create_alert(type="warning", title="Build", message="Build failed")
        create_alert(type="info", title="Reminder", message="Check logs")

        response = client.post("/api/ui/alerts/viewed", json={"alert_ids": [first.id]})

        assert response.status_code == 200
        data = response.json()
        assert data["updated"] == 1
        assert data["unread"] == 1

        alerts = client.get("/api/ui/alerts").json()
        first_alert = next(alert for alert in alerts["alerts"] if alert["id"] == first.id)
        assert first_alert["viewed"] is True

    def test_dismiss_alert_marks_viewed_without_deleting(self, client):
        created = create_alert(type="warning", title="Build", message="Build failed")

        response = client.post(f"/api/ui/alerts/{created.id}/dismiss")

        assert response.status_code == 200
        data = response.json()
        assert data == {"status": "dismissed", "alert_id": created.id}

        alerts = client.get("/api/ui/alerts").json()
        assert alerts["total"] == 1
        assert alerts["unread"] == 0
        assert alerts["alerts"][0]["id"] == created.id
        assert alerts["alerts"][0]["viewed"] is True

    def test_respond_alert_marks_viewed_without_deleting(self, client):
        created = create_alert(type="info", title="Heads up", message="Acknowledge me")

        response = client.post(f"/api/ui/alerts/{created.id}/respond")

        assert response.status_code == 200
        data = response.json()
        assert data == {"status": "responded", "alert_id": created.id, "response": "acknowledged"}

        alerts = client.get("/api/ui/alerts").json()
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
        """Test that /api/ui/logs returns valid JSON."""
        response = client.get("/api/ui/logs")
        assert response.status_code == 200
        data = response.json()

        assert "logs" in data
        assert isinstance(data["logs"], str)

    def test_logs_json_format(self, client):
        """Test that /api/ui/logs can return JSON format."""
        response = client.get("/api/ui/logs?format=json")
        assert response.status_code == 200
        data = response.json()

        assert "entries" in data
        assert "total" in data
        assert "source_groups" in data
        assert isinstance(data["entries"], list)

    def test_logs_with_limit(self, client):
        """Test that /api/ui/logs respects limit parameter."""
        response = client.get("/api/ui/logs?limit=10")
        assert response.status_code == 200
        # Just verify it doesn't error

    def test_logs_with_level_filter(self, client):
        """Test that /api/ui/logs accepts level filter."""
        response = client.get("/api/ui/logs?level=error")
        assert response.status_code == 200

    def test_logs_with_search(self, client):
        """Test that /api/ui/logs accepts search parameter."""
        response = client.get("/api/ui/logs?search=test")
        assert response.status_code == 200

    def test_logs_json_groups_sources_by_folder_and_preserves_log_timestamp(
        self, client, kanban_state_reader
    ):
        """Test grouped source metadata and parsed timestamps from plain-text logs."""
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

        assert response.status_code == 200
        data = response.json()

        groups = {group["id"]: group for group in data["source_groups"]}
        assert "root" in groups
        assert "builder" in groups
        assert any(option["id"] == "progenitor.log" for option in groups["root"]["options"])
        assert any(option["id"] == "builder/builder.log" for option in groups["builder"]["options"])

        builder_entry = next(
            entry for entry in data["entries"] if entry["relative_path"] == "builder/builder.log"
        )
        assert builder_entry["timestamp"] == "2026-03-08 00:33:35"
        assert builder_entry["source"] == "app.agents.builder_progenitor.agent"

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
        assert full_response.status_code == 200
        full_data = full_response.json()
        assert full_data["total"] == 2
        assert full_data["newest_cursor"]

        top_entry = full_data["entries"][0]
        incremental_response = client.get(f"/api/ui/logs?format=json&after={top_entry['id']}")
        assert incremental_response.status_code == 200
        incremental_data = incremental_response.json()
        assert incremental_data["cursor_found"] is True
        assert all(entry["id"] != top_entry["id"] for entry in incremental_data["entries"])

    def test_logs_json_supports_date_range_filter(self, client, kanban_state_reader):
        """Test inclusive start/end date filtering."""
        range_end = datetime.now(UTC).date()
        range_start = range_end - timedelta(days=1)
        old_date = range_start - timedelta(days=1)
        (kanban_state_reader.logs_dir / "conversation.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "message": "old entry",
                            "timestamp": f"{old_date.isoformat()}T10:00:00Z",
                            "level": "info",
                        }
                    ),
                    json.dumps(
                        {
                            "message": "in range",
                            "timestamp": f"{range_start.isoformat()}T10:00:00Z",
                            "level": "warning",
                        }
                    ),
                    json.dumps(
                        {
                            "message": "boundary end",
                            "timestamp": f"{range_end.isoformat()}T23:59:00Z",
                            "level": "error",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        response = client.get(
            f"/api/ui/logs?format=json&start={range_start.isoformat()}&end={range_end.isoformat()}&limit=0"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["date_range"] == {
            "start": range_start.isoformat(),
            "end": range_end.isoformat(),
        }
        assert [entry["message"] for entry in data["entries"]] == ["boundary end", "in range"]
        assert {entry["level"] for entry in data["entries"]} == {"warn", "error"}

    def test_logs_export_returns_downloadable_payload(self, client, kanban_state_reader):
        """Test export endpoint returns attachment headers."""
        timestamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        (kanban_state_reader.logs_dir / "decisions.jsonl").write_text(
            json.dumps({"message": "decision made", "timestamp": timestamp, "level": "info"})
            + "\n",
            encoding="utf-8",
        )

        response = client.get("/api/ui/logs/export?format=jsonl")
        assert response.status_code == 200
        assert "attachment;" in response.headers["content-disposition"]
        assert "decision made" in response.text

    def test_logs_export_supports_csv(self, client, kanban_state_reader):
        """Test CSV export output."""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        (kanban_state_reader.logs_dir / "progenitor.log").write_text(
            f"{timestamp} - __main__ - INFO - Started\n",
            encoding="utf-8",
        )

        response = client.get("/api/ui/logs/export?format=csv")

        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert "timestamp,level,source,relative_path,message" in response.text
        assert "Started" in response.text


class TestAgentDetailEndpoint:
    """Tests for /api/ui/agents/{name} endpoint."""

    def test_agent_detail_not_found(self, client):
        """Test that requesting a non-existent agent returns 404."""
        response = client.get("/api/ui/agents/nonexistent-agent-xyz")
        assert response.status_code == 404

    def test_agent_detail_response_structure(self, client):
        """Test the expected response structure for agent detail endpoint."""
        # First, get the list of agents
        agents_response = client.get("/api/ui/agents")
        assert agents_response.status_code == 200
        agents = agents_response.json()["agents"]

        if len(agents) > 0:
            # Test with a real agent
            agent_name = agents[0]["name"]
            response = client.get(f"/api/ui/agents/{agent_name}")
            assert response.status_code == 200
            data = response.json()

            # Check expected fields
            assert "name" in data
            assert "status" in data
            assert "kind" in data
            assert "version" in data
            assert "description" in data
            assert "total_requests" in data
            assert "successful_requests" in data
            assert "failed_requests" in data
            assert "total_tokens_input" in data
            assert "total_tokens_output" in data
            assert "avg_response_time" in data
            assert "success_rate" in data
            assert "tokens_per_request_avg" in data


class TestBacklogEndpoint:
    """Tests for /api/ui/backlog endpoint."""

    def test_backlog_returns_valid_structure(self, client):
        """Test that /api/ui/backlog returns valid JSON with expected fields."""
        response = client.get("/api/ui/backlog")
        assert response.status_code == 200
        data = response.json()

        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)


class TestScheduleEndpoint:
    """Tests for /api/ui/schedule endpoint."""

    def test_schedule_returns_valid_structure(self, client):
        """Test that /api/ui/schedule returns valid JSON with expected fields."""
        response = client.get("/api/ui/schedule")
        assert response.status_code == 200
        data = response.json()

        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)

    def test_schedule_computes_future_next_run(self, client, monkeypatch):
        """next_run is recomputed from cron, not the stale last_run fallback."""
        recent = datetime.now(UTC).isoformat()
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "schedule",
            [
                {
                    "id": "sched-future",
                    "name": "Hourly ping",
                    "message": "ping",
                    "cron_expression": "0 * * * *",
                    "last_run": recent,
                    "created_at": recent,
                    "paused": False,
                }
            ],
        )
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)

        item = client.get("/api/ui/schedule").json()["items"][0]
        assert item["next_run"] is not None
        assert datetime.fromisoformat(item["next_run"]) > datetime.now(UTC)
        assert item["next_run"] != item["last_run"]
        assert item["message"] == "ping"
        assert item["paused"] is False
        assert item["enabled"] is True

    def test_create_and_patch_keep_paused_in_sync(self, client, monkeypatch):
        """enabled drives the runtime-honoured paused flag on create and patch."""
        store = InMemoryDocumentStore()
        store.save_document(PROGENITOR_STATE_PREFIX, "schedule", [])
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
        assert created.status_code == 200
        schedule_id = created.json()["id"].split(":", 1)[1]
        entry = store.get_document(PROGENITOR_STATE_PREFIX, "schedule", []).payload[0]
        assert entry["paused"] is False

        patched = client.patch(
            f"/api/ui/widgets/schedule:{schedule_id}",
            json={"enabled": False},
        )
        assert patched.status_code == 200
        entry = store.get_document(PROGENITOR_STATE_PREFIX, "schedule", []).payload[0]
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
            [
                {
                    "id": "sched-live",
                    "name": "Live schedule",
                    "message": "ping",
                    "cron_expression": "0 * * * *",
                    "created_at": created_at,
                    "paused": False,
                }
            ],
        )
        state_reader = StateReader(document_store=store)
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)

        queue = asyncio.Queue()
        ui_routes._ui_stream_subscribers.append(queue)
        try:
            envelope = ui_routes.broadcast_schedule_snapshot()
        finally:
            ui_routes._ui_stream_subscribers.remove(queue)

        assert envelope.type == "schedule.snapshot"
        assert envelope.payload["items"][0]["id"] == "sched-live"
        assert ui_routes._ui_event_replay[-1].type == "schedule.snapshot"

        streamed = queue.get_nowait()
        assert streamed["type"] == "ui.event"
        assert streamed["envelope"].type == "schedule.snapshot"
        assert streamed["envelope"].payload["items"][0]["message"] == "ping"


class TestLoopsEndpoint:
    """Tests for /api/ui/loops endpoint."""

    def test_loops_returns_valid_structure(self, client):
        """Test that /api/ui/loops returns valid JSON with expected fields."""
        response = client.get("/api/ui/loops")
        assert response.status_code == 200
        data = response.json()

        assert "loops" in data
        assert isinstance(data["loops"], list)


class TestNavigationLinks:
    """Tests for API endpoints (UI navigation is now client-side)."""

    def test_api_endpoints_work(self, client):
        """Test that API endpoints are accessible."""
        api_endpoints = [
            "/api/ui/status/snapshot",
            "/api/ui/agents",
            "/api/ui/backlog",
            "/api/ui/schedule",
        ]
        for endpoint in api_endpoints:
            response = client.get(endpoint)
            assert response.status_code == 200, f"Endpoint {endpoint} failed"


class TestAgentsPage:
    """Tests for the Agents API endpoint."""

    def test_agents_api_returns_json(self, client):
        """Test that /api/ui/agents returns JSON."""
        response = client.get("/api/ui/agents")
        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]


class TestLogsPage:
    """Tests for the Logs API endpoint."""

    def test_logs_api_returns_json(self, client):
        """Test that /api/ui/logs returns JSON."""
        response = client.get("/api/ui/logs")
        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]


# Note: These tests used to check for server-rendered navigation HTML content.
# With the React SPA, navigation is rendered client-side, so these tests are simplified
# to just verify the SPA HTML shell is returned correctly. Navigation behavior
# should be tested with end-to-end tests using a browser.

# Note: The following test classes were removed as they tested server-rendered HTML elements:
# - TestUIEnhancedFeatures (dashboard charts, stats grids, logs controls)
# - These elements are now rendered client-side by React


class TestUIAccessibility:
    """Tests for UI API endpoints.

    Note: HTML accessibility tests are no longer relevant since the backend
    is API-only. Frontend accessibility should be tested separately.
    """

    def test_chat_ui_has_favicon(self, client):
        """Test removed - UI pages no longer served by backend."""
        # This test is no longer relevant - backend is API-only
        pass

    def test_pages_have_viewport_meta(self, client):
        """Test removed - UI pages no longer served by backend."""
        # This test is no longer relevant - backend is API-only
        pass


class TestSSEConnection:
    """Tests for Server-Sent Events connectivity."""

    def test_status_endpoint_returns_complete_data(self, client):
        """Test that status endpoint returns all fields needed for UI updates."""
        response = client.get("/api/ui/status/snapshot")
        assert response.status_code == 200
        data = response.json()

        # These fields are used by SSE status updates
        required_fields = [
            "connected",
            "server_status",
            "progenitor_running",
            "uptime_seconds",
            "version",
            "cycle_count",
            "agents",
            "backlog",
            "schedule",
            "loops",
        ]

        for field in required_fields:
            assert field in data, f"Status missing required field: {field}"

    def test_status_has_build_info(self, client):
        """Test that status includes build information."""
        response = client.get("/api/ui/status/snapshot")
        assert response.status_code == 200
        data = response.json()

        # Build info should be present (may be null if no build)
        assert "build" in data

    def test_status_has_memory_info(self, client):
        """Test that status includes memory information."""
        response = client.get("/api/ui/status/snapshot")
        assert response.status_code == 200
        data = response.json()

        # Memory should be tracked
        assert "memory_mb" in data


class TestSplitSSEEndpoints:
    """Tests for the split SSE endpoints (/chat and /status/stream).

    Note: SSE streaming tests are skipped as they hang waiting for the infinite stream.
    The endpoints are verified indirectly via route registration checks.
    """

    def test_events_endpoint_is_registered(self, client):
        """Test that /api/ui/events endpoint is registered (via OpenAPI)."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        openapi = response.json()
        paths = openapi.get("paths", {})
        assert "/api/ui/events" in paths, "Events SSE endpoint should be registered"

    def test_status_stream_endpoint_is_registered(self, client):
        """Test that /api/ui/status SSE stream endpoint is registered."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        openapi = response.json()
        paths = openapi.get("paths", {})
        # The SSE stream endpoint has the same path as the snapshot but different handler
        # The snapshot is registered first, so check both handlers exist
        assert "/api/ui/status" in paths or "/api/ui/status/snapshot" in paths, (
            "Status endpoint should be registered"
        )

    def test_broadcast_functions_exist(self):
        """Test that the broadcast functions are available."""
        from app.backend.ui_routes import (
            broadcast_chat_event,
            broadcast_status_event,
        )

        # Just verify they're callable
        assert callable(broadcast_chat_event)
        assert callable(broadcast_status_event)

    def test_broadcast_alerts_state_pushes_full_alert_payload(self):
        from app.backend import ui_routes

        clear_alerts()
        alert = create_alert(type="warning", title="Build", message="Build failed")
        queue = ui_routes.asyncio.Queue()
        ui_routes._status_subscribers.append(queue)
        try:
            ui_routes.broadcast_alerts_state()
            event = queue.get_nowait()
        finally:
            ui_routes._status_subscribers.remove(queue)
            clear_alerts()

        assert event["type"] == "alerts"
        payload = json.loads(event["data"])
        assert payload["unread"] == 1
        assert payload["total"] == 1
        assert payload["alerts"][0]["id"] == alert.id
        assert payload["alerts"][0]["title"] == "Build"

    @pytest.mark.asyncio
    async def test_status_stream_connected_emits_alerts_payload(self, monkeypatch):
        from app.backend import ui_routes

        clear_alerts()
        alert = create_alert(type="info", title="Heads up", message="Alert from SSE")
        monkeypatch.setattr(ui_routes, "start_status_monitor", lambda: None)
        monkeypatch.setattr(
            ui_routes, "_get_current_status", AsyncMock(return_value={"connected": True})
        )

        generator = ui_routes._generate_status_events()
        try:
            connected_event = await generator.__anext__()
            alerts_event = await generator.__anext__()
        finally:
            await generator.aclose()
            clear_alerts()

        assert "event: connected" in connected_event
        assert "event: alerts" in alerts_event
        payload = json.loads(alerts_event.split("data: ", 1)[1].strip())
        assert payload["unread"] == 1
        assert payload["alerts"][0]["id"] == alert.id


class TestWorkspaceBridgeActions:
    """Tests for backend action bridge endpoints."""

    def test_state_reader_uses_document_store_over_legacy_state_files(self, tmp_path):
        state_dir = tmp_path / "data" / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "current_state.json").write_text(
            json.dumps({"thread_id": "legacy-file", "cycle_count": 1}),
            encoding="utf-8",
        )
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "current_state",
            {"thread_id": "postgres-store", "cycle_count": 9},
        )

        state_reader = StateReader(state_dir=state_dir, document_store=store)

        assert state_reader.get_progenitor_state() == {
            "thread_id": "postgres-store",
            "cycle_count": 9,
        }

    def test_patch_schedule_widget_updates_schedule_document(self, client, monkeypatch):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "schedule",
            [
                {
                    "id": "sched-1",
                    "name": "Daily check",
                    "message": "Run the daily review",
                    "schedule": "0 0 * * *",
                    "cron_expression": "0 0 * * *",
                    "schedule_type": "cron",
                    "enabled": True,
                    "timezone": "UTC",
                }
            ],
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

        assert response.status_code == 200
        document = store.get_document(PROGENITOR_STATE_PREFIX, "schedule", [])
        assert document is not None
        updated = document.payload[0]
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

        assert response.status_code == 200
        payload = response.json()
        assert payload["type"] == "mcp"
        assert payload["data"]["enabled"] is True
        assert payload["data"]["url"] == "https://mcp.jina.ai/v1?include_tags=search,read"

        document = store.get_document(PROGENITOR_STATE_PREFIX, "mcp", {})
        assert document is not None
        updated = document.payload
        assert updated["mcpServers"]["jina"]["enabled"] is True
        assert updated["mcpServers"]["jina"]["description"] == "Live web search"
        assert (
            updated["mcpServers"]["jina"]["url"]
            == "https://mcp.jina.ai/v1?include_tags=search,read"
        )

    def test_refresh_agent_widget_returns_live_card_details(self, client, tmp_path, monkeypatch):
        state_dir = tmp_path / "data" / "state"
        config_dir = tmp_path / "data" / "config"
        sessions_dir = tmp_path / "data" / "sessions"
        logs_dir = tmp_path / "logs"
        state_dir.mkdir(parents=True)
        config_dir.mkdir(parents=True)
        sessions_dir.mkdir(parents=True)
        logs_dir.mkdir(parents=True)
        (state_dir / "health.json").write_text(
            json.dumps({"status": "running", "thread_id": "run_123"}),
            encoding="utf-8",
        )
        (config_dir / "agents.json").write_text(
            json.dumps(
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
                }
            ),
            encoding="utf-8",
        )
        (config_dir / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

        state_reader = StateReader(
            state_dir=state_dir,
            config_dir=config_dir,
            sessions_dir=sessions_dir,
            logs_dir=logs_dir,
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

        assert response.status_code == 200
        payload = response.json()
        assert payload["id"] == "agent:observe_agent"
        assert payload["data"]["inspection"]["source"] == "a2a"
        assert payload["data"]["inspection"]["skills"][0]["id"] == "diagnose"
        assert payload["data"]["agent_card_url"].endswith("/.well-known/agent-card.json")

    def test_refresh_mcp_widget_returns_protocol_catalog(self, client, tmp_path, monkeypatch):
        state_dir = tmp_path / "data" / "state"
        config_dir = tmp_path / "data" / "config"
        sessions_dir = tmp_path / "data" / "sessions"
        logs_dir = tmp_path / "logs"
        state_dir.mkdir(parents=True)
        config_dir.mkdir(parents=True)
        sessions_dir.mkdir(parents=True)
        logs_dir.mkdir(parents=True)
        (state_dir / "health.json").write_text(
            json.dumps({"status": "running", "thread_id": "run_456"}),
            encoding="utf-8",
        )
        (config_dir / "agents.json").write_text(json.dumps({"agents": []}), encoding="utf-8")
        (config_dir / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "jina": {
                            "description": "Web tools",
                            "enabled": True,
                            "transport": "http",
                            "url": "https://mcp.example.test",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        state_reader = StateReader(
            state_dir=state_dir,
            config_dir=config_dir,
            sessions_dir=sessions_dir,
            logs_dir=logs_dir,
        )
        monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)

        async def fake_inspect_mcp_widget(widget, reader):
            assert widget["id"] == "mcp:jina"
            assert reader is state_reader
            return {
                **widget,
                "inspection": {
                    "source": "mcp",
                    "refreshed_at": "2026-04-13T10:31:00Z",
                    "protocol_version": "2025-03-26",
                    "tools": [{"name": "search", "description": "Search the web"}],
                    "resources": [{"name": "docs", "uri": "resource://docs"}],
                    "prompts": [{"name": "summarize", "description": "Summarize a page"}],
                },
                "url": "https://mcp.example.test",
            }

        monkeypatch.setattr(
            "app.backend.ui_routes._inspect_mcp_widget",
            fake_inspect_mcp_widget,
        )

        response = client.post("/api/ui/widgets/mcp:jina/refresh")

        assert response.status_code == 200
        payload = response.json()
        assert payload["id"] == "mcp:jina"
        assert payload["data"]["inspection"]["source"] == "mcp"
        assert payload["data"]["inspection"]["tools"][0]["name"] == "search"
        assert payload["data"]["inspection"]["resources"][0]["uri"] == "resource://docs"
        assert payload["data"]["inspection"]["prompts"][0]["name"] == "summarize"

    def test_create_schedule_writes_schedule_document(self, client, monkeypatch):
        store = InMemoryDocumentStore()
        store.save_document(PROGENITOR_STATE_PREFIX, "schedule", [])
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

        assert response.status_code == 200
        payload = response.json()
        assert payload["type"] == "schedule"
        assert payload["title"] == "Morning Check-in"
        assert payload["data"]["instruction"] == "Summarize overnight alerts and prep the inbox."
        document = store.get_document(PROGENITOR_STATE_PREFIX, "schedule", [])
        assert document is not None
        updated = document.payload
        assert updated[0]["name"] == "Morning Check-in"
        assert updated[0]["message"] == "Summarize overnight alerts and prep the inbox."
        assert updated[0]["id"].startswith("sched_")
        assert updated[0]["cron_expression"] == "*/15 * * * *"

    def test_create_once_schedule_uses_cron_prefix_and_persists_scheduled_at(
        self, client, monkeypatch
    ):
        store = InMemoryDocumentStore()
        store.save_document(PROGENITOR_STATE_PREFIX, "schedule", [])
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

        assert response.status_code == 200
        payload = response.json()
        assert payload["id"].startswith("schedule:cron_")
        document = store.get_document(PROGENITOR_STATE_PREFIX, "schedule", [])
        assert document is not None
        updated = document.payload
        assert updated[0]["id"].startswith("cron_")
        assert updated[0]["schedule_type"] == "once"
        assert updated[0]["scheduled_at"] == "2026-06-19T09:47:00+00:00"
        assert "cron_expression" not in updated[0]

    def test_create_event_widget_writes_event_widget_file(self, client, kanban_state_reader):
        response = client.post(
            "/api/ui/events",
            json={
                "name": "Card created trigger",
                "event_type": "kanban.card.created",
                "enabled": True,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["type"] == "event"
        assert payload["title"] == "Card created trigger"
        assert payload["data"]["event_type"] == "kanban.card.created"
        assert payload["data"]["trigger_count"] == 0

        event_path = kanban_state_reader.config_dir / "event_widgets.json"
        stored = json.loads(event_path.read_text(encoding="utf-8"))
        assert stored[0]["name"] == "Card created trigger"
        assert stored[0]["event_type"] == "kanban.card.created"
        assert stored[0]["enabled"] is True

        widgets = client.get("/api/ui/widgets").json()["items"]
        assert any(widget["id"] == payload["id"] for widget in widgets)

    def test_patch_event_widget_updates_config_file(self, client, kanban_state_reader):
        event_path = kanban_state_reader.config_dir / "event_widgets.json"
        event_path.write_text(
            json.dumps(
                [
                    {
                        "id": "evt-1",
                        "name": "Created trigger",
                        "event_type": "kanban.card.created",
                        "enabled": True,
                        "trigger_count": 0,
                        "last_run_status": "idle",
                        "logs": [],
                    }
                ]
            ),
            encoding="utf-8",
        )
        kanban_state_reader.invalidate(event_path)

        response = client.patch(
            "/api/ui/widgets/event:evt-1",
            json={
                "name": "Deleted trigger",
                "event_type": "kanban.card.deleted",
                "enabled": False,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["type"] == "event"
        assert payload["data"]["event_type"] == "kanban.card.deleted"
        assert payload["data"]["enabled"] is False

        stored = json.loads(event_path.read_text(encoding="utf-8"))
        assert stored[0]["name"] == "Deleted trigger"
        assert stored[0]["event_type"] == "kanban.card.deleted"
        assert stored[0]["enabled"] is False

    def test_kanban_create_and_update_events_trigger_matching_widgets(
        self, client, kanban_state_reader
    ):
        created_widget = client.post(
            "/api/ui/events",
            json={"name": "Created", "event_type": "kanban.card.created"},
        ).json()
        updated_widget = client.post(
            "/api/ui/events",
            json={"name": "Updated", "event_type": "kanban.card.updated"},
        ).json()

        created_card = client.post(
            "/api/ui/kanban/cards",
            json={"title": "Evented card", "summary": "Initial summary"},
        ).json()

        widgets = {item["id"]: item for item in client.get("/api/ui/widgets").json()["items"]}
        created_data = widgets[created_widget["id"]]["data"]
        assert created_data["trigger_count"] == 1
        assert created_data["last_payload"]["card"]["id"] == created_card["id"]
        assert created_data["last_payload"]["event_type"] == "kanban.card.created"

        update_response = client.patch(
            f"/api/ui/kanban/cards/{created_card['id']}",
            json={"summary": "Updated summary"},
        )

        assert update_response.status_code == 200
        widgets = {item["id"]: item for item in client.get("/api/ui/widgets").json()["items"]}
        updated_data = widgets[updated_widget["id"]]["data"]
        assert updated_data["trigger_count"] == 1
        assert updated_data["last_payload"]["card"]["id"] == created_card["id"]
        assert updated_data["last_payload"]["changed_fields"] == ["summary"]
        assert updated_data["last_payload"]["event_type"] == "kanban.card.updated"

    def test_kanban_move_status_and_delete_events_trigger_matching_widgets(
        self, client, kanban_state_reader
    ):
        moved_widget = client.post(
            "/api/ui/events",
            json={"name": "Moved", "event_type": "kanban.card.moved"},
        ).json()
        status_widget = client.post(
            "/api/ui/events",
            json={"name": "Status", "event_type": "kanban.card.status_changed"},
        ).json()
        deleted_widget = client.post(
            "/api/ui/events",
            json={"name": "Deleted", "event_type": "kanban.card.deleted"},
        ).json()

        first = client.post("/api/ui/kanban/cards", json={"title": "First card"}).json()
        second = client.post("/api/ui/kanban/cards", json={"title": "Second card"}).json()

        move_response = client.patch(
            f"/api/ui/kanban/cards/{second['id']}",
            json={"position": 0},
        )
        assert move_response.status_code == 200

        widgets = {item["id"]: item for item in client.get("/api/ui/widgets").json()["items"]}
        moved_data = widgets[moved_widget["id"]]["data"]
        assert moved_data["trigger_count"] == 1
        assert moved_data["last_payload"]["card"]["id"] == second["id"]
        assert moved_data["last_payload"]["from"]["position"] == 1
        assert moved_data["last_payload"]["to"]["position"] == 0

        status_response = client.patch(
            f"/api/ui/kanban/cards/{first['id']}",
            json={"status": "running"},
        )
        assert status_response.status_code == 200

        widgets = {item["id"]: item for item in client.get("/api/ui/widgets").json()["items"]}
        status_data = widgets[status_widget["id"]]["data"]
        assert status_data["trigger_count"] == 1
        assert status_data["last_payload"]["card"]["id"] == first["id"]
        assert status_data["last_payload"]["from_status"] == "idle"
        assert status_data["last_payload"]["to_status"] == "running"

        delete_response = client.delete(f"/api/ui/kanban/cards/{first['id']}")
        assert delete_response.status_code == 204

        widgets = {item["id"]: item for item in client.get("/api/ui/widgets").json()["items"]}
        deleted_data = widgets[deleted_widget["id"]]["data"]
        assert deleted_data["trigger_count"] == 1
        assert deleted_data["last_payload"]["card"]["id"] == first["id"]
        assert deleted_data["last_payload"]["event_type"] == "kanban.card.deleted"

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

            assert run_response.status_code == 200
            assert generate_response.status_code == 200
            assert run_response.json()["response"] == "bridge-response"
            assert generate_response.json()["response"] == "bridge-response"
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

            assert first.status_code == 200
            assert second.status_code == 200
            first_thread_id = first.json()["thread_id"]
            second_thread_id = second.json()["thread_id"]
            assert first_thread_id
            assert second_thread_id
            assert first_thread_id != second_thread_id

            metadata_values = [
                call.kwargs["metadata"]["thread_id"]
                for call in mock_client.send_message.await_args_list
            ]
            assert metadata_values == [first_thread_id, second_thread_id]
        finally:
            backend_main.client = original_client

    def test_workspace_actions_reuse_explicit_thread_and_expose_registry(self, client):
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

            assert run_response.status_code == 200
            assert generate_response.status_code == 200
            assert run_response.json()["thread_id"] == thread_id
            assert generate_response.json()["thread_id"] == thread_id

            metadata_values = [
                call.kwargs["metadata"]["thread_id"]
                for call in mock_client.send_message.await_args_list
            ]
            assert metadata_values == [thread_id, thread_id]

            threads_response = client.get("/api/ui/threads?invocation_type=automation")
            assert threads_response.status_code == 200
            data = threads_response.json()
            assert data["total"] == 1
            record = data["items"][0]
            assert record["thread_id"] == thread_id
            assert record["invocation_type"] == "automation"
            assert record["root_widget_id"] == "prompt:root"
            assert sorted(record["widget_ids"]) == ["prompt:child", "prompt:root"]
            assert record["status"] == "completed"
            assert record["last_response"] == "bridge-response"
        finally:
            backend_main.client = original_client
