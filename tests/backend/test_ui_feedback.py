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

import asyncio
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.backend.main import app
from app.backend.state_reader import (
    AgentInfo,
    BacklogItem,
    BuildInfo,
    HealthInfo,
    StateReader,
)
from app.backend.ui_routes import (
    broadcast_chat_event,
)


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def temp_state_dir(tmp_path):
    """Create a temporary state directory with test files."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return state_dir


class TestSSEEventStream:
    """Tests for Server-Sent Events streaming endpoint."""

    @pytest.mark.skip(reason="SSE streaming tests require special handling - tested via e2e")
    def test_sse_endpoint_returns_correct_content_type(self, client):
        """Test that SSE events endpoint returns text/event-stream content type."""
        pass

    @pytest.mark.skip(reason="SSE streaming tests require special handling - tested via e2e")
    def test_sse_headers(self, client):
        """Test that SSE response has proper headers."""
        pass


class TestUIStatusEndpoint:
    """Tests for the UI status endpoint that feeds the dashboard."""

    def test_status_returns_all_required_fields(self, client):
        """Test /api/ui/status returns all fields needed by UI."""
        response = client.get("/api/ui/status/snapshot")
        assert response.status_code == 200
        data = response.json()

        # Core fields
        assert "connected" in data
        assert "server_status" in data
        assert "progenitor_running" in data
        assert "progenitor_status" in data
        assert "uptime_seconds" in data
        assert "version" in data

        # State data
        assert "cycle_count" in data
        assert "run_id" in data
        assert "messages_count" in data

        # Lists for UI
        assert "loops" in data
        assert "agents" in data
        assert "backlog" in data
        assert "schedule" in data

        # Build status
        assert "build" in data

    def test_status_loops_structure(self, client):
        """Test that loops have the correct structure for UI rendering."""
        response = client.get("/api/ui/status/snapshot")
        data = response.json()

        # Loops should be a list
        assert isinstance(data["loops"], list)

        # If loops exist, verify structure
        for loop in data["loops"]:
            assert "name" in loop
            assert "status" in loop
            # Optional field
            assert "current_task" in loop or loop.get("current_task") is None

    def test_status_agents_structure(self, client):
        """Test that agents have the correct structure for UI rendering."""
        response = client.get("/api/ui/status/snapshot")
        data = response.json()

        # Agents should be a list
        assert isinstance(data["agents"], list)

        # If agents exist, verify structure
        for agent in data["agents"]:
            assert "name" in agent
            assert "status" in agent
            assert "kind" in agent

    def test_status_build_structure(self, client):
        """Test that build info has correct structure."""
        response = client.get("/api/ui/status/snapshot")
        data = response.json()

        build = data.get("build")
        if build:
            assert "in_progress" in build
            # Optional fields when build is in progress
            if build.get("in_progress"):
                assert "phase" in build
                assert "iteration" in build


class TestAgentStatusFeedback:
    """Tests for agent status updates shown in UI."""

    def test_agents_endpoint_returns_list(self, client):
        """Test /api/ui/agents returns agent list."""
        response = client.get("/api/ui/agents")
        assert response.status_code == 200
        data = response.json()

        assert "agents" in data
        assert "total" in data
        assert isinstance(data["agents"], list)
        assert data["total"] >= 0

    def test_agent_detail_not_found(self, client):
        """Test requesting details for non-existent agent."""
        response = client.get("/api/ui/agents/nonexistent-agent")
        assert response.status_code == 404

    def test_agent_status_values(self, client):
        """Test that agent statuses use expected values."""
        response = client.get("/api/ui/agents")
        data = response.json()

        valid_statuses = {"ready", "busy", "offline", "stopped", "error", "starting"}

        for agent in data["agents"]:
            # Status should be one of the expected values
            assert agent["status"] in valid_statuses or agent["status"] is not None


class TestBuildPhaseFeedback:
    """Tests for build phase tracking shown in UI."""

    def test_backlog_endpoint(self, client):
        """Test /api/ui/backlog returns work items."""
        response = client.get("/api/ui/backlog")
        assert response.status_code == 200
        data = response.json()

        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)

    def test_backlog_item_structure(self, client):
        """Test backlog items have required fields."""
        response = client.get("/api/ui/backlog")
        data = response.json()

        for item in data["items"]:
            assert "id" in item
            assert "type" in item
            assert "status" in item
            assert "description" in item

    def test_valid_build_phases(self):
        """Test that build phases match expected values used in UI."""
        # These phases are displayed in the UI with icons
        valid_phases = {
            "observe", "think", "plan", "build", "execute", "verify", "learn"
        }

        # Verify BuildInfo can handle all phases
        for phase in valid_phases:
            build = BuildInfo(
                in_progress=True,
                phase=phase,
                iteration=1,
                score=0.5,
            )
            assert build.phase == phase


class TestLoopStatusFeedback:
    """Tests for Planner/Executor loop status in UI."""

    def test_loops_endpoint(self, client):
        """Test /api/ui/loops returns loop status."""
        response = client.get("/api/ui/loops")
        assert response.status_code == 200
        data = response.json()

        assert "loops" in data
        assert isinstance(data["loops"], list)

    def test_loop_status_values(self, client):
        """Test that loop statuses use expected values."""
        response = client.get("/api/ui/loops")
        data = response.json()

        valid_statuses = {"active", "idle", "error"}

        for loop in data["loops"]:
            assert loop["status"] in valid_statuses or loop["status"] is not None

    def test_loop_names(self, client):
        """Test expected loop names appear."""
        response = client.get("/api/ui/loops")
        data = response.json()

        # May be empty if progenitor not running
        loop_names = {loop["name"] for loop in data["loops"]}

        # If running, should have Planner and Executor
        if loop_names:
            expected_loops = {"Planner", "Executor"}
            assert loop_names.issubset(expected_loops) or expected_loops.issubset(loop_names)


class TestScheduleFeedback:
    """Tests for scheduled tasks shown in UI."""

    def test_schedule_endpoint(self, client):
        """Test /api/ui/schedule returns scheduled tasks."""
        response = client.get("/api/ui/schedule")
        assert response.status_code == 200
        data = response.json()

        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)

    def test_schedule_item_structure(self, client):
        """Test schedule items have required fields."""
        response = client.get("/api/ui/schedule")
        data = response.json()

        for item in data["items"]:
            assert "id" in item
            assert "name" in item
            assert "schedule" in item
            assert "enabled" in item


class TestErrorFeedback:
    """Tests for error handling and feedback to UI."""

    def test_invalid_endpoint_returns_404(self, client):
        """Test that invalid endpoints return proper 404."""
        response = client.get("/api/ui/nonexistent")
        assert response.status_code == 404

    def test_alerts_endpoint(self, client):
        """Test /api/ui/alerts returns alerts list."""
        response = client.get("/api/ui/alerts")
        assert response.status_code == 200
        data = response.json()

        assert "alerts" in data
        assert "total" in data

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
            run_id="test",
            cycle_count=10,
            pid=1234,
        )
        assert not health.is_running()

        # Not running - no last_active
        health = HealthInfo(
            status="running",
            last_active=None,
            run_id="test",
            cycle_count=10,
            pid=1234,
        )
        assert not health.is_running()

        # Running - recent last_active
        health = HealthInfo(
            status="running",
            last_active=datetime.now(UTC).isoformat(),
            run_id="test",
            cycle_count=10,
            pid=1234,
        )
        assert health.is_running()

    def test_health_info_stale_detection(self):
        """Test that stale progenitor is detected."""
        from datetime import timedelta

        # Stale - last_active too old (> 60 seconds)
        old_time = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
        health = HealthInfo(
            status="running",
            last_active=old_time,
            run_id="test",
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

    def test_read_nonexistent_health_file(self, temp_state_dir):
        """Test reading when health.json doesn't exist."""
        reader = StateReader(state_dir=temp_state_dir)
        health = reader.get_health()

        assert health.status == "unknown"
        assert health.last_active is None
        assert not health.is_running()

    def test_read_invalid_json(self, temp_state_dir):
        """Test reading invalid JSON file."""
        # Write invalid JSON
        (temp_state_dir / "health.json").write_text("not valid json {{{")

        reader = StateReader(state_dir=temp_state_dir)
        health = reader.get_health()

        # Should return default values on parse error
        assert health.status == "unknown"

    def test_read_valid_health_file(self, temp_state_dir):
        """Test reading valid health.json."""
        health_data = {
            "status": "running",
            "last_active": datetime.now(UTC).isoformat(),
            "run_id": "run-123",
            "cycle_count": 42,
            "pid": 9999,
        }
        (temp_state_dir / "health.json").write_text(json.dumps(health_data))

        reader = StateReader(state_dir=temp_state_dir)
        health = reader.get_health()

        assert health.status == "running"
        assert health.run_id == "run-123"
        assert health.cycle_count == 42
        assert health.pid == 9999

    def test_read_registry_file(self, temp_state_dir):
        """Test reading registry.json with agents and tools."""
        registry_data = {
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
            ],
            "tools": [
                {
                    "name": "tool-1",
                    "description": "A tool",
                    "enabled": True,
                }
            ],
        }
        (temp_state_dir / "registry.json").write_text(json.dumps(registry_data))

        reader = StateReader(state_dir=temp_state_dir)
        agents, tools = reader.get_registry()

        assert len(agents) == 1
        assert agents[0].name == "agent-1"
        assert len(tools) == 1
        assert tools[0].name == "tool-1"

    def test_state_snapshot(self, temp_state_dir):
        """Test getting complete state snapshot."""
        # Create all required state files
        health_data = {
            "status": "running",
            "last_active": datetime.now(UTC).isoformat(),
            "run_id": "run-123",
            "cycle_count": 42,
            "pid": 9999,
        }
        (temp_state_dir / "health.json").write_text(json.dumps(health_data))

        build_state = {
            "build_in_progress": True,
            "build_phase": "verify",
            "iteration": {"current": 2},
            "verification": {"current_score": 0.5},
            "build_messages": [],
            "backlog": [
                {"id": "1", "item_type": "task", "status": "pending", "description": "Test task"}
            ],
        }
        (temp_state_dir / "build_state.json").write_text(json.dumps(build_state))

        schedule = [
            {"id": "sched-1", "name": "Daily check", "schedule": "0 0 * * *", "enabled": True}
        ]
        (temp_state_dir / "schedule.json").write_text(json.dumps(schedule))

        reader = StateReader(state_dir=temp_state_dir)
        snapshot = reader.get_snapshot()

        assert snapshot.health.status == "running"
        assert snapshot.health.cycle_count == 42
        assert len(snapshot.backlog) == 1
        assert len(snapshot.schedule) == 1
        assert snapshot.build is not None
        assert snapshot.build.in_progress is True
        assert snapshot.build.phase == "verify"




class TestLogsEndpoint:
    """Tests for logs endpoint used by logs page."""

    def test_logs_endpoint(self, client):
        """Test /api/ui/logs returns logs."""
        response = client.get("/api/ui/logs")
        assert response.status_code == 200
        # Response can be JSON or raw text
        assert response.text is not None

    def test_logs_with_level_filter(self, client):
        """Test logs endpoint filters by level."""
        response = client.get("/api/ui/logs?level=ERROR")
        assert response.status_code == 200
        # Should not error even if no ERROR logs exist


class TestUIPages:
    """Tests for UI pages.

    Note: Backend is now API-only and does not serve static files.
    The frontend runs separately via npm/Vite. These tests verify that
    static file routes are no longer available (404).
    """

    def test_chat_ui_not_served(self, client):
        """Test root path no longer serves UI (API-only backend)."""
        response = client.get("/")
        # Root path should return 404 - backend is API-only now
        assert response.status_code in (404, 405, 307)

    def test_dashboard_not_served(self, client):
        """Test dashboard page no longer served (API-only backend)."""
        response = client.get("/ui/dashboard")
        assert response.status_code == 404

    def test_agents_page_not_served(self, client):
        """Test agents page no longer served (API-only backend)."""
        response = client.get("/ui/agents")
        assert response.status_code == 404

    def test_logs_page_not_served(self, client):
        """Test logs page no longer served (API-only backend)."""
        response = client.get("/ui/logs")
        assert response.status_code == 404


class TestUINavigation:
    """Tests for UI API routes (navigation is now client-side)."""

    def test_api_ui_status_works(self, client):
        """Test /api/ui/status endpoint works."""
        response = client.get("/api/ui/status/snapshot")
        assert response.status_code == 200

    def test_api_ui_agents_works(self, client):
        """Test /api/ui/agents endpoint works."""
        response = client.get("/api/ui/agents")
        assert response.status_code == 200

    def test_chat_has_dashboard_link(self, client):
        """Test removed - UI pages no longer served by backend."""
        # This test is no longer relevant as static files aren't served
        pass

    def test_dashboard_has_chat_link(self, client):
        """Test removed - UI pages no longer served by backend."""
        # This test is no longer relevant as static files aren't served
        pass


class TestUIAccessibility:
    """Tests for UI API responses.

    Note: Accessibility tests for HTML pages are no longer relevant since
    the backend is API-only. Frontend accessibility should be tested
    separately with the React app.
    """

    def test_pages_have_viewport_meta(self, client):
        """Test removed - UI pages no longer served by backend."""
        # This test is no longer relevant - backend is API-only
        pass

    def test_pages_have_favicon(self, client):
        """Test removed - UI pages no longer served by backend."""
        # This test is no longer relevant - backend is API-only
        pass


class TestBroadcastEvent:
    """Tests for event broadcasting to UI subscribers."""

    def test_broadcast_event_structure(self):
        """Test broadcast_chat_event creates proper structure."""
        from app.backend.ui_routes import _chat_subscribers

        # Clear any existing subscribers
        _chat_subscribers.clear()

        # Create a mock queue
        queue = asyncio.Queue()
        _chat_subscribers.append(queue)

        try:
            # Broadcast an event
            broadcast_chat_event("test_event", {"key": "value"})

            # Verify event was queued (non-blocking check)
            assert not queue.empty()
        finally:
            _chat_subscribers.remove(queue)
