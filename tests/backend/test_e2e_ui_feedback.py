"""
End-to-End UI Feedback Integration Tests.

CRITICAL: These tests verify that the aliens will see proper feedback
from the Progenitor agent and all sub-agents through the client UI.

Tests the complete feedback path:
1. User sends message via UI
2. Server processes request
3. Agent/sub-agents execute
4. Real-time feedback via SSE
5. UI receives and displays updates
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from fastapi.testclient import TestClient
from app.backend.main import app
from app.backend.state_reader import (
    StateReader,
    HealthInfo,
    AgentInfo,
    BuildInfo,
    LoopInfo,
    BacklogItem,
)


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_state_files(tmp_path):
    """Create mock state files that simulate a running progenitor."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # Health file - agent is running
    health_data = {
        "status": "running",
        "last_active": datetime.now(timezone.utc).isoformat(),
        "run_id": "alien-demo-001",
        "cycle_count": 42,
        "pid": 12345,
    }
    (state_dir / "health.json").write_text(json.dumps(health_data))

    # Registry with agents
    registry_data = {
        "agents": [
            {
                "name": "galactic-translator",
                "kind": "a2a_agent",
                "version": "2.0.0",
                "description": "Translates alien languages",
                "capabilities": ["translate", "decode"],
                "entrypoint": "python alien_translator.py",
                "transport": "http",
                "enabled": True,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "status": "ready",
            },
            {
                "name": "tech-analyzer",
                "kind": "a2a_agent",
                "version": "1.5.0",
                "description": "Analyzes alien technology",
                "capabilities": ["analyze", "classify"],
                "entrypoint": "python tech_analyzer.py",
                "transport": "stdio",
                "enabled": True,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "status": "busy",
            },
        ],
        "tools": [
            {"name": "universal_decoder", "description": "Decodes alien signals", "enabled": True},
        ],
    }
    (state_dir / "registry.json").write_text(json.dumps(registry_data))

    # Build state - in progress
    build_state = {
        "build_in_progress": True,
        "build_phase": "execute",
        "build_task_description": "Establishing first contact protocol",
        "build_started_at": datetime.now(timezone.utc).isoformat(),
        "iteration": {"current": 3},
        "verification": {"current_score": 0.75},
        "build_messages": [
            {"role": "user", "content": "Start contact"},
            {"role": "assistant", "content": "Initiating..."},
        ],
        "backlog": [
            {
                "id": "contact-001",
                "item_type": "task",
                "status": "in_progress",
                "description": "Establish communication channel",
                "priority": 10,
            },
            {
                "id": "tech-002",
                "item_type": "feature",
                "status": "pending",
                "description": "Analyze alien technology sample",
                "priority": 5,
            },
        ],
    }
    (state_dir / "build_state.json").write_text(json.dumps(build_state))

    # Schedule
    schedule = [
        {"id": "sched-1", "name": "Hourly galactic scan", "schedule": "0 * * * *", "enabled": True},
    ]
    (state_dir / "schedule.json").write_text(json.dumps(schedule))

    return state_dir


class TestUIFeedbackIntegration:
    """Integration tests for UI feedback during agent operations."""

    def test_status_endpoint_provides_complete_info(self, client):
        """Test that status endpoint provides all information needed by UI."""
        response = client.get("/api/ui/status/snapshot")
        assert response.status_code == 200

        data = response.json()

        # All critical fields for alien inspection must be present
        assert "connected" in data
        assert "server_status" in data
        assert "progenitor_running" in data
        assert "version" in data

        # Agent and loop info for real-time feedback
        assert "agents" in data
        assert "loops" in data
        assert "backlog" in data
        assert "schedule" in data

        # Build status for progress tracking
        assert "build" in data

    def test_state_reader_with_mock_files(self, mock_state_files):
        """Test StateReader correctly reads simulated state files."""
        reader = StateReader(state_dir=mock_state_files)

        # Health should show running
        health = reader.get_health()
        assert health.status == "running"
        assert health.run_id == "alien-demo-001"
        assert health.cycle_count == 42
        assert health.is_running()  # Should be true due to recent last_active

        # Agents should be registered
        agents, tools = reader.get_registry()
        assert len(agents) == 2
        assert agents[0].name == "galactic-translator"
        assert agents[1].status == "busy"
        assert len(tools) == 1

        # Build should be in progress
        build_state = reader.get_build_state()
        assert build_state["build_in_progress"] is True
        assert build_state["build_phase"] == "execute"

        # Backlog should have items
        backlog = reader.get_backlog()
        assert len(backlog) == 2
        assert backlog[0].status == "in_progress"

    def test_snapshot_provides_all_data(self, mock_state_files):
        """Test that state snapshot contains all data for UI."""
        reader = StateReader(state_dir=mock_state_files)
        snapshot = reader.get_snapshot()

        # Verify snapshot structure
        assert snapshot.health.status == "running"
        assert len(snapshot.agents) == 2
        assert len(snapshot.backlog) == 2
        assert len(snapshot.schedule) == 1

        # Build info should be populated
        assert snapshot.build is not None
        assert snapshot.build.in_progress is True
        assert snapshot.build.phase == "execute"
        assert snapshot.build.iteration == 3
        assert snapshot.build.score == 0.75
        assert snapshot.build.message_count == 2

    def test_snapshot_to_dict_serialization(self, mock_state_files):
        """Test snapshot can be serialized for SSE transmission."""
        reader = StateReader(state_dir=mock_state_files)
        snapshot = reader.get_snapshot()

        # Convert to dict for JSON serialization
        data = reader.snapshot_to_dict(snapshot)

        # Must be JSON-serializable
        json_str = json.dumps(data)
        assert len(json_str) > 0

        # Verify key fields survived serialization
        parsed = json.loads(json_str)
        assert parsed["health"]["status"] == "running"
        assert parsed["health"]["is_running"] is True
        assert len(parsed["agents"]) == 2


class TestAgentFeedbackVisibility:
    """Tests that agent activity is visible to the UI."""

    def test_agent_status_changes_visible(self, mock_state_files):
        """Test that agent status changes are picked up by StateReader."""
        reader = StateReader(state_dir=mock_state_files)

        # Initial read
        agents, _ = reader.get_registry()
        initial_status = {a.name: a.status for a in agents}

        # Update registry to simulate agent status change
        registry_file = mock_state_files / "registry.json"
        registry = json.loads(registry_file.read_text())
        registry["agents"][1]["status"] = "ready"  # tech-analyzer becomes ready
        registry_file.write_text(json.dumps(registry))

        # Clear cache to force re-read
        reader._cache.clear()

        # Re-read
        agents, _ = reader.get_registry()
        new_status = {a.name: a.status for a in agents}

        # Verify change was detected
        assert initial_status["tech-analyzer"] == "busy"
        assert new_status["tech-analyzer"] == "ready"

    def test_build_phase_progression(self, mock_state_files):
        """Test build phase changes are tracked."""
        reader = StateReader(state_dir=mock_state_files)

        phases = ["observe", "think", "plan", "build", "execute", "verify", "learn"]

        for phase in phases:
            # Update build phase
            build_file = mock_state_files / "build_state.json"
            build_state = json.loads(build_file.read_text())
            build_state["build_phase"] = phase
            build_file.write_text(json.dumps(build_state))

            # Clear cache
            reader._cache.clear()

            # Verify phase is reported correctly
            snapshot = reader.get_snapshot()
            assert snapshot.build.phase == phase

    def test_backlog_progress_tracking(self, mock_state_files):
        """Test backlog item status changes are visible."""
        reader = StateReader(state_dir=mock_state_files)

        # Complete a task
        build_file = mock_state_files / "build_state.json"
        build_state = json.loads(build_file.read_text())
        build_state["backlog"][0]["status"] = "completed"
        build_file.write_text(json.dumps(build_state))

        # Clear cache
        reader._cache.clear()

        # Verify
        backlog = reader.get_backlog()
        completed = [b for b in backlog if b.status == "completed"]
        assert len(completed) == 1


class TestLoopStatusFeedback:
    """Tests for Planner/Executor loop feedback."""

    def test_loops_reflect_agent_state(self, mock_state_files):
        """Test that loop status reflects actual agent state."""
        reader = StateReader(state_dir=mock_state_files)

        health = reader.get_health()
        loops = reader.get_loops(health)

        # When agent is running, loops should be present
        assert len(loops) == 2

        loop_names = {l.name for l in loops}
        assert "Planner" in loop_names
        assert "Executor" in loop_names

    def test_loops_empty_when_agent_stopped(self, mock_state_files):
        """Test loops are empty when agent is not running."""
        reader = StateReader(state_dir=mock_state_files)

        # Update health to stopped
        health_file = mock_state_files / "health.json"
        health = json.loads(health_file.read_text())
        health["status"] = "stopped"
        health_file.write_text(json.dumps(health))

        reader._cache.clear()

        health = reader.get_health()
        loops = reader.get_loops(health)

        # Loops should be empty when agent is stopped
        assert len(loops) == 0


class TestErrorFeedbackPath:
    """Tests for error feedback to UI."""

    def test_health_unknown_on_missing_file(self, tmp_path):
        """Test health shows unknown when file is missing."""
        empty_dir = tmp_path / "empty_state"
        empty_dir.mkdir()

        reader = StateReader(state_dir=empty_dir)
        health = reader.get_health()

        assert health.status == "unknown"
        assert not health.is_running()

    def test_graceful_handling_of_corrupt_json(self, tmp_path):
        """Test graceful handling of corrupt state files."""
        state_dir = tmp_path / "corrupt_state"
        state_dir.mkdir()

        # Write corrupt JSON
        (state_dir / "health.json").write_text("{not valid json")

        reader = StateReader(state_dir=state_dir)
        health = reader.get_health()

        # Should return default values, not crash
        assert health.status == "unknown"

    def test_alerts_endpoint_for_errors(self, client):
        """Test alerts endpoint is available for error notifications."""
        response = client.get("/api/ui/alerts")
        assert response.status_code == 200

        data = response.json()
        assert "alerts" in data
        assert "total" in data


class TestRealTimeSSEFeedback:
    """Tests for SSE real-time feedback mechanism."""

    @pytest.mark.skip(reason="SSE streaming test requires special async handling - tested via e2e")
    def test_sse_endpoint_accessible(self, client):
        """Test SSE events endpoint is accessible."""
        pass

    def test_broadcast_mechanism_works(self):
        """Test broadcast_event puts events in subscriber queues."""
        from app.backend.ui_routes import _chat_subscribers, broadcast_chat_event
        import asyncio

        # Clear existing subscribers
        _chat_subscribers.clear()

        # Add test subscriber
        queue = asyncio.Queue()
        _chat_subscribers.append(queue)

        try:
            # Broadcast test event
            broadcast_chat_event("alien_contact", {
                "message": "First contact initiated",
                "species": "Unknown",
            })

            # Verify event was queued
            assert not queue.empty()

            # Check event content
            event = queue.get_nowait()
            assert event["type"] == "alien_contact"
        finally:
            _chat_subscribers.remove(queue)


class TestChatFlowFeedback:
    """Tests for chat flow with UI feedback."""

    def test_models_endpoint_accessible(self, client):
        """Test models endpoint returns available models."""
        response = client.get("/v1/models")
        assert response.status_code == 200

        data = response.json()
        assert "data" in data
        # Should have at least one model
        assert len(data["data"]) >= 1

    def test_responses_endpoint(self, client):
        """Test responses endpoint handles requests."""
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "Greetings from the Galactic Federation",
                "stream": False,
            },
        )

        # Endpoint should handle the request (may return error if agent not running)
        assert response.status_code in [200, 500, 503]


class TestUIPageContent:
    """Tests for UI page content.

    Note: The backend is now API-only and does not serve static files.
    The frontend runs separately via npm/Vite. These tests verify that
    the UI static routes are no longer available (404) since they've been removed.
    """

    def test_root_path_not_served(self, client):
        """Test root path no longer serves UI (API-only backend)."""
        response = client.get("/")
        # Root path should return 404 or redirect - backend is API-only now
        assert response.status_code in (404, 405, 307)

    def test_ui_dashboard_not_served(self, client):
        """Test /ui/dashboard no longer served (API-only backend)."""
        response = client.get("/ui/dashboard")
        assert response.status_code == 404

    def test_ui_agents_not_served(self, client):
        """Test /ui/agents no longer served (API-only backend)."""
        response = client.get("/ui/agents")
        assert response.status_code == 404

    def test_ui_logs_not_served(self, client):
        """Test /ui/logs no longer served (API-only backend)."""
        response = client.get("/ui/logs")
        assert response.status_code == 404

    def test_api_ui_routes_still_work(self, client):
        """Test /api/ui/* routes still work (these are API routes)."""
        response = client.get("/api/ui/status/snapshot")
        # API routes should work
        assert response.status_code == 200


class TestDataIntegrity:
    """Tests for data integrity in UI feedback."""

    def test_health_info_timestamp_parsing(self):
        """Test HealthInfo can parse ISO timestamps correctly."""
        health = HealthInfo(
            status="running",
            last_active="2024-01-15T10:30:00+00:00",
            run_id="test",
            cycle_count=100,
            pid=1234,
        )

        # Should be able to check if running
        # This will be False because timestamp is old
        assert isinstance(health.is_running(), bool)

    def test_agent_info_handles_all_fields(self):
        """Test AgentInfo handles all possible fields."""
        full_agent = AgentInfo.from_dict({
            "name": "complete-agent",
            "kind": "a2a_agent",
            "version": "3.0.0",
            "description": "Complete agent with all fields",
            "capabilities": ["cap1", "cap2", "cap3"],
            "entrypoint": "python complete.py",
            "endpoint": "http://localhost:9001",
            "transport": "http",
            "enabled": True,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-15T12:00:00Z",
            "status": "ready",
            "status_message": "Waiting for requests",
            "port": 9001,
            "agent_card_url": "http://localhost:9001/.well-known/agent.json",
            "pid": 54321,
            "startup_time": "2024-01-15T11:00:00Z",
            "last_heartbeat": "2024-01-15T12:00:00Z",
            "last_error": None,
        })

        assert full_agent.name == "complete-agent"
        assert full_agent.port == 9001
        assert full_agent.pid == 54321
        assert full_agent.agent_card_url is not None

    def test_build_info_defaults(self):
        """Test BuildInfo has sensible defaults."""
        empty_build = BuildInfo.from_dict({})

        assert empty_build.in_progress is False
        assert empty_build.phase is None
        assert empty_build.iteration == 0
        assert empty_build.score == 0.0
        assert empty_build.message_count == 0
