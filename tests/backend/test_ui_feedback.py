"""
Tests for UI Feedback Mechanisms.

Critical tests ensuring the aliens see proper feedback from the Progenitor agent
and all sub-agents. These tests verify:
- SSE (Server-Sent Events) for real-time UI updates
- Agent status propagation
- Build phase tracking
- Loop status (Planner/Executor)
- Error feedback to UI
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.backend.document_store import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore
from app.backend.main import app
from app.backend.state_reader import (
    AgentInfo,
    BacklogItem,
    BuildInfo,
    HealthInfo,
    StateReader,
)


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


class TestErrorFeedback:
    """Tests for error handling and feedback to UI."""

    def test_invalid_endpoint_returns_404(self, client):
        """Test that invalid endpoints return proper 404."""
        response = client.get("/api/ui/nonexistent")
        assert response.status_code == 404

    def test_alerts_endpoint(self, client):
        """Test /api/ui/alerts returns the finite UI acknowledgement contract."""
        request_id = "ui-feedback-alerts"
        response = client.get("/api/ui/alerts", headers={"X-Request-ID": request_id})
        assert response.status_code == 202
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["alerts.updated"],
        }

    def test_dismiss_nonexistent_alert(self, client):
        """Test dismissing non-existent alert returns 404."""
        response = client.post("/api/ui/alerts/nonexistent/dismiss")
        assert response.status_code == 404


class TestStateReader:
    """Tests for StateReader that provides data to UI endpoints."""

    def test_health_info_is_running_check(self):
        """Test HealthInfo.is_running() logic."""
        # Not running - status not running
        health = HealthInfo(
            status="stopped",
            last_active=datetime.now(UTC).isoformat(),
            thread_id="thread-test",
            cycle_count=10,
            pid=1234,
        )
        assert not health.is_running()

        # Not running - no last_active
        health = HealthInfo(
            status="running",
            last_active=None,
            thread_id="thread-test",
            cycle_count=10,
            pid=1234,
        )
        assert not health.is_running()

        # Running - recent last_active
        health = HealthInfo(
            status="running",
            last_active=datetime.now(UTC).isoformat(),
            thread_id="thread-test",
            cycle_count=10,
            pid=1234,
        )
        assert health.is_running()

    def test_health_info_from_dict_parses_enriched_fields(self):
        """version/started_at/llm_* round-trip through from_dict()."""
        health = HealthInfo.from_dict(
            {
                "status": "running",
                "cycle_count": 5,
                "version": "abc123def456",
                "started_at": "2026-01-01T00:00:00Z",
                "llm_model": "gpt-5.3-chat",
                "llm_reasoning_enabled": True,
                "llm_reasoning_effort": "medium",
            }
        )
        assert health.version == "abc123def456"
        assert health.started_at == "2026-01-01T00:00:00Z"
        assert health.llm_model == "gpt-5.3-chat"
        assert health.llm_reasoning_enabled is True
        assert health.llm_reasoning_effort == "medium"

    def test_health_info_stale_detection(self):
        """Test that stale progenitor is detected."""
        from datetime import timedelta

        # Stale - last_active too old (> 60 seconds)
        old_time = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
        health = HealthInfo(
            status="running",
            last_active=old_time,
            thread_id="thread-test",
            cycle_count=10,
            pid=1234,
        )
        assert not health.is_running()

    def test_agent_info_from_dict(self):
        """Test AgentInfo.from_dict() handles all fields."""
        data = {
            "name": "test-agent",
            "kind": "a2a_agent",
            "version": "1.0.0",
            "description": "Test agent",
            "capabilities": ["cap1", "cap2"],
            "entrypoint": "python main.py",
            "endpoint": "http://localhost:9000",
            "transport": "http",
            "enabled": True,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "status": "ready",
            "port": 9000,
            "pid": 1234,
        }

        agent = AgentInfo.from_dict(data)
        assert agent.name == "test-agent"
        assert agent.kind == "a2a_agent"
        assert agent.status == "ready"
        assert agent.port == 9000

    def test_build_info_from_dict(self):
        """Test BuildInfo.from_dict() extracts build state."""
        data = {
            "build_in_progress": True,
            "build_phase": "execute",
            "build_task_description": "Implementing feature X",
            "build_started_at": "2024-01-01T00:00:00Z",
            "iteration": {"current": 3},
            "verification": {"current_score": 0.75},
            "build_messages": [{"role": "user", "content": "test"}] * 5,
        }

        build = BuildInfo.from_dict(data)
        assert build.in_progress is True
        assert build.phase == "execute"
        assert build.task_description == "Implementing feature X"
        assert build.iteration == 3
        assert build.score == 0.75
        assert build.message_count == 5

    def test_backlog_item_from_dict(self):
        """Test BacklogItem.from_dict() handles fields."""
        data = {
            "id": "task-001",
            "item_type": "feature",
            "status": "in_progress",
            "description": "Build the thing",
            "priority": 1,
            "created_at": "2024-01-01T00:00:00Z",
        }

        item = BacklogItem.from_dict(data)
        assert item.id == "task-001"
        assert item.item_type == "feature"
        assert item.status == "in_progress"
        assert item.priority == 1


class TestStateReaderFileOperations:
    """Tests for StateReader file reading operations."""

    def test_read_health_with_no_document_store(self):
        """Test reading health when there's no document store at all."""
        reader = StateReader(document_store=InMemoryDocumentStore())
        health = reader.get_health()
        assert health.status == "unknown"
        assert health.last_active is None
        assert not health.is_running()

    def test_read_health_document_not_yet_written(self):
        """Before the heartbeat loop ever writes a "health" document, get_health()
        falls back to current_state (also absent here), landing on "unknown"."""
        store = InMemoryDocumentStore()
        reader = StateReader(document_store=store)
        health = reader.get_health()
        assert health.status == "unknown"

    def test_read_valid_health_document(self):
        """Test reading a valid "health" Postgres document."""
        health_data = {
            "status": "running",
            "last_active": datetime.now(UTC).isoformat(),
            "thread_id": "thread-123",
            "cycle_count": 42,
            "pid": 9999,
        }
        store = InMemoryDocumentStore()
        store.save_document(PROGENITOR_STATE_PREFIX, "health", health_data)
        reader = StateReader(document_store=store)
        health = reader.get_health()
        assert health.status == "running"
        assert health.thread_id == "thread-123"
        assert health.cycle_count == 42
        assert health.pid == 9999

    def test_read_registry_file(self):
        """Test reading agents.json and the Progenitor MCP source config."""
        agents_data = {
            "agents": [
                {
                    "name": "agent-1",
                    "kind": "a2a_agent",
                    "version": "1.0.0",
                    "description": "First agent",
                    "capabilities": [],
                    "entrypoint": "python",
                    "transport": "stdio",
                    "enabled": True,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "status": "ready",
                }
            ]
        }
        mcp_data = {
            "mcpServers": {
                "tool-1": {
                    "description": "A tool",
                    "enabled": True,
                    "transport": "http",
                    "url": "https://example.com/mcp",
                }
            }
        }
        store = InMemoryDocumentStore()
        store.save_document(PROGENITOR_STATE_PREFIX, "registry", agents_data)
        store.save_document(PROGENITOR_STATE_PREFIX, "mcp", mcp_data)
        reader = StateReader(document_store=store)
        agents, tools = reader.get_registry()

        assert len(agents) == 1
        assert agents[0].name == "agent-1"
        assert len(tools) == 1
        assert tools[0].name == "tool-1"

    def test_read_registry_merges_legacy_and_map(self):
        """Map entries should be visible even if legacy list is present."""
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "registry",
            {
                "agents": [{"name": "legacy-agent", "url": "http://legacy:8100"}],
                "legacy-agent": {"url": "http://map:8101"},
                "map-agent": {"url": "http://map:8102"},
            },
        )
        reader = StateReader(document_store=store)
        agents, tools = reader.get_registry()

        by_name = {a.name: a.url for a in agents}
        assert by_name == {
            "legacy-agent": "http://map:8101",
            "map-agent": "http://map:8102",
        }
        assert tools == []

    def test_state_snapshot(self):
        """Test getting complete state snapshot."""
        # Seed all state as Postgres documents (health/schedule/build_state are
        # all resolved through the document store now, not disk files).
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "health",
            {
                "status": "running",
                "last_active": datetime.now(UTC).isoformat(),
                "thread_id": "thread-123",
                "cycle_count": 42,
                "pid": 9999,
            },
        )
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "build_state",
            {
                "build_in_progress": True,
                "build_phase": "verify",
                "iteration": {"current": 2},
                "verification": {"current_score": 0.5},
                "build_messages": [],
                "backlog": [
                    {
                        "id": "1",
                        "item_type": "task",
                        "status": "pending",
                        "description": "Test task",
                    }
                ],
            },
        )
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "schedule",
            {
                "sched-1": {
                    "id": "sched-1",
                    "name": "Daily check",
                    "schedule": "0 0 * * *",
                    "enabled": True,
                }
            },
        )
        reader = StateReader(document_store=store)
        snapshot = reader.get_snapshot()

        assert snapshot.health.status == "running"
        assert snapshot.health.cycle_count == 42
        assert len(snapshot.backlog) == 1
        assert len(snapshot.schedule) == 1
        assert snapshot.build is not None
        assert snapshot.build.in_progress is True
        assert snapshot.build.phase == "verify"

    def test_workspace_payload_tolerates_scalar_json_log_lines(self, tmp_path):
        """Scalar JSON log lines should not crash widget or workspace payload generation."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(exist_ok=True)

        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "registry",
            {
                "agents": [
                    {
                        "name": "example_agent",
                        "kind": "a2a_agent",
                        "version": "1.0.0",
                        "description": "Example agent",
                        "capabilities": [],
                        "entrypoint": "python",
                        "transport": "http",
                        "enabled": True,
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                        "status": "ready",
                    }
                ]
            },
        )
        (logs_dir / "example_agent.log").write_text('1\n{"message":"started"}\n', encoding="utf-8")

        reader = StateReader(
            document_store=store,
            logs_dir=logs_dir,
        )

        payload = reader.get_workspace_payload()

        assert "widgets" in payload
        assert any(widget["name"] == "example_agent" for widget in payload["widgets"])


class TestLogsEndpoint:
    """Tests for logs endpoint used by logs page."""

    def test_logs_endpoint(self, client):
        """Test /api/ui/logs returns the finite UI acknowledgement contract."""
        request_id = "ui-feedback-logs"
        response = client.get("/api/ui/logs", headers={"X-Request-ID": request_id})
        assert response.status_code == 202
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["logs.page.loaded"],
        }

    def test_logs_with_level_filter(self, client):
        """Test logs endpoint filters by level without changing its acknowledgement."""
        request_id = "ui-feedback-error-logs"
        response = client.get(
            "/api/ui/logs?level=ERROR",
            headers={"X-Request-ID": request_id},
        )
        assert response.status_code == 202
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["logs.page.loaded"],
        }
        # Should not error even if no ERROR logs exist
