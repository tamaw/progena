"""
CRITICAL First Contact Tests.

These tests ensure the Progenitor Agent and Client UI provide proper feedback
for humanity's first contact with alien civilizations. All edge cases and
error scenarios must be handled gracefully - the fate of humanity depends on it!

Tests cover:
1. Complete UI data pipeline
2. Real-time feedback consistency
3. Error recovery and graceful degradation
4. Data integrity under all conditions
5. Sub-agent status visibility
"""

import asyncio
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from fastapi.testclient import TestClient
from app.chat_server.main import app
from app.chat_server.state_reader import (
    StateReader,
    StateSnapshot,
    HealthInfo,
    AgentInfo,
    ToolInfo,
    BacklogItem,
    ScheduleItem,
    LoopInfo,
    BuildInfo,
)
from app.chat_server.ui_routes import (
    UIStatusResponse,
    SubAgentInfo,
    LoopInfo as UILoopInfo,
    broadcast_event,
    _event_subscribers,
)


@pytest.fixture
def client():
    """Create a test client for the aliens to use."""
    return TestClient(app)


@pytest.fixture
def complete_state_files(tmp_path):
    """
    Create a complete set of state files simulating a fully operational
    Progenitor agent with multiple sub-agents - ready for alien inspection.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    
    # Health file - agent is running strong
    health_data = {
        "status": "running",
        "last_active": datetime.now(timezone.utc).isoformat(),
        "run_id": "first-contact-mission-001",
        "cycle_count": 1337,
        "pid": 42424,
    }
    (state_dir / "health.json").write_text(json.dumps(health_data))
    
    # Registry with multiple diverse agents
    registry_data = {
        "agents": [
            {
                "name": "diplomatic-protocol",
                "kind": "a2a_agent",
                "version": "3.0.0",
                "description": "Handles diplomatic communication protocols",
                "capabilities": ["negotiate", "translate", "protocol"],
                "entrypoint": "python diplomatic.py",
                "transport": "http",
                "enabled": True,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-31T00:00:00Z",
                "status": "ready",
                "port": 9001,
                "pid": 11111,
            },
            {
                "name": "tech-analyzer",
                "kind": "a2a_agent",
                "version": "2.5.0",
                "description": "Analyzes and classifies alien technology",
                "capabilities": ["analyze", "classify", "report"],
                "entrypoint": "python analyzer.py",
                "transport": "http",
                "enabled": True,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-31T00:00:00Z",
                "status": "busy",
                "status_message": "Analyzing incoming data stream",
                "port": 9002,
                "pid": 22222,
            },
            {
                "name": "language-decoder",
                "kind": "a2a_agent",
                "version": "1.0.0",
                "description": "Decodes unknown language patterns",
                "capabilities": ["decode", "learn", "translate"],
                "entrypoint": "python decoder.py",
                "transport": "stdio",
                "enabled": True,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-31T00:00:00Z",
                "status": "ready",
            },
        ],
        "tools": [
            {"name": "universal_translator", "description": "Translates any language", "enabled": True},
            {"name": "spectrum_analyzer", "description": "Analyzes signal spectrums", "enabled": True},
            {"name": "pattern_matcher", "description": "Matches complex patterns", "enabled": True},
        ],
    }
    (state_dir / "registry.json").write_text(json.dumps(registry_data))
    
    # Build state - actively working on first contact
    build_state = {
        "build_in_progress": True,
        "build_phase": "execute",
        "build_task_description": "Establishing first contact protocol",
        "build_started_at": (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(),
        "current_iteration": 7,
        "verification_score": 0.89,
        "build_messages": [
            {"role": "user", "content": "Begin first contact sequence"},
            {"role": "assistant", "content": "Initializing diplomatic protocols..."},
            {"role": "user", "content": "Alien signal detected"},
            {"role": "assistant", "content": "Analyzing signal pattern..."},
            {"role": "assistant", "content": "Signal decoded successfully"},
        ],
        "backlog": [
            {
                "id": "fc-001",
                "item_type": "critical_task",
                "status": "in_progress",
                "description": "Establish two-way communication channel",
                "priority": 100,
            },
            {
                "id": "fc-002",
                "item_type": "task",
                "status": "pending",
                "description": "Decode alien greeting protocol",
                "priority": 90,
            },
            {
                "id": "fc-003",
                "item_type": "task",
                "status": "completed",
                "description": "Analyze signal frequency patterns",
                "priority": 80,
            },
        ],
        "actions_this_cycle": 3,
    }
    (state_dir / "build_state.json").write_text(json.dumps(build_state))
    
    # Schedule
    schedule = [
        {"id": "sched-1", "name": "Signal scan", "schedule": "*/5 * * * *", "enabled": True, "next_run": None},
        {"id": "sched-2", "name": "Protocol check", "schedule": "0 * * * *", "enabled": True, "next_run": None},
    ]
    (state_dir / "schedule.json").write_text(json.dumps(schedule))
    
    return state_dir


class TestCriticalUIDataPipeline:
    """Test the complete data pipeline from state files to UI."""

    def test_all_endpoints_return_200(self, client):
        """All UI endpoints must be accessible - alien visitors must not see errors."""
        endpoints = [
            "/api/ui/status",
            "/api/ui/agents",
            "/api/ui/backlog",
            "/api/ui/schedule",
            "/api/ui/loops",
            "/api/ui/tasks",
            "/api/ui/alerts",
            "/api/ui/metrics",
            "/api/ui/logs",
        ]
        
        for endpoint in endpoints:
            response = client.get(endpoint)
            assert response.status_code == 200, f"Endpoint {endpoint} failed with {response.status_code}"

    def test_status_endpoint_complete_data(self, client):
        """Status endpoint must provide ALL data needed by the UI."""
        response = client.get("/api/ui/status")
        assert response.status_code == 200
        data = response.json()
        
        # Core connectivity
        assert "connected" in data
        assert "server_status" in data
        assert "progenitor_running" in data
        assert "progenitor_status" in data
        
        # Agent info
        assert "pid" in data
        assert "run_id" in data
        assert "cycle_count" in data
        assert "uptime_seconds" in data
        assert "version" in data
        assert "last_active" in data
        
        # Collections for UI display
        assert "loops" in data
        assert "agents" in data
        assert "backlog" in data
        assert "schedule" in data
        
        # Build status
        assert "build" in data
        
        # All must be correct types
        assert isinstance(data["loops"], list)
        assert isinstance(data["agents"], list)
        assert isinstance(data["backlog"], list)
        assert isinstance(data["schedule"], list)

    def test_state_reader_with_complete_state(self, complete_state_files):
        """StateReader must correctly read all state data for aliens."""
        reader = StateReader(state_dir=complete_state_files)
        snapshot = reader.get_snapshot()
        
        # Health
        assert snapshot.health.status == "running"
        assert snapshot.health.run_id == "first-contact-mission-001"
        assert snapshot.health.cycle_count == 1337
        assert snapshot.health.pid == 42424
        assert snapshot.health.is_running()  # Recent last_active
        
        # Agents
        assert len(snapshot.agents) == 3
        agent_names = {a.name for a in snapshot.agents}
        assert "diplomatic-protocol" in agent_names
        assert "tech-analyzer" in agent_names
        assert "language-decoder" in agent_names
        
        # Agent details
        tech_analyzer = next(a for a in snapshot.agents if a.name == "tech-analyzer")
        assert tech_analyzer.status == "busy"
        assert tech_analyzer.status_message == "Analyzing incoming data stream"
        assert tech_analyzer.pid == 22222
        
        # Tools
        assert len(snapshot.tools) == 3
        tool_names = {t.name for t in snapshot.tools}
        assert "universal_translator" in tool_names
        
        # Backlog
        assert len(snapshot.backlog) == 3
        in_progress = [b for b in snapshot.backlog if b.status == "in_progress"]
        assert len(in_progress) == 1
        assert in_progress[0].id == "fc-001"
        
        # Schedule
        assert len(snapshot.schedule) == 2
        
        # Build
        assert snapshot.build is not None
        assert snapshot.build.in_progress is True
        assert snapshot.build.phase == "execute"
        assert snapshot.build.iteration == 7
        assert snapshot.build.score == 0.89
        assert snapshot.build.message_count == 5
        
        # Loops (should have Planner/Executor when running)
        assert len(snapshot.loops) == 2


class TestRealTimeFeedbackConsistency:
    """Test that real-time feedback is consistent and reliable."""

    def test_broadcast_event_reaches_subscribers(self):
        """Events must be broadcast to all subscribers - aliens might be listening."""
        _event_subscribers.clear()
        
        # Create multiple subscriber queues (like multiple alien terminals)
        queues = [asyncio.Queue() for _ in range(5)]
        for q in queues:
            _event_subscribers.append(q)
        
        try:
            # Broadcast an important message
            broadcast_event("first_contact", {
                "status": "initiated",
                "signal_strength": 0.98,
                "decoded_message": "Greetings from Earth",
            })
            
            # All subscribers must receive the event
            for i, q in enumerate(queues):
                assert not q.empty(), f"Subscriber {i} did not receive the event"
                event = q.get_nowait()
                assert event["type"] == "first_contact"
                # Data is JSON-encoded as string
                event_data = json.loads(event["data"])
                assert event_data["status"] == "initiated"
        finally:
            for q in queues:
                _event_subscribers.remove(q)

    def test_multiple_broadcasts_in_sequence(self):
        """Multiple events must arrive in order."""
        _event_subscribers.clear()
        queue = asyncio.Queue()
        _event_subscribers.append(queue)
        
        try:
            events = [
                ("signal_detected", {"source": "Alpha Centauri"}),
                ("signal_analyzing", {"progress": 50}),
                ("signal_decoded", {"message": "Peace"}),
                ("response_sent", {"status": "success"}),
            ]
            
            for event_type, data in events:
                broadcast_event(event_type, data)
            
            # Verify all events arrived in order
            received = []
            while not queue.empty():
                received.append(queue.get_nowait())
            
            assert len(received) == 4
            assert received[0]["type"] == "signal_detected"
            assert received[1]["type"] == "signal_analyzing"
            assert received[2]["type"] == "signal_decoded"
            assert received[3]["type"] == "response_sent"
        finally:
            _event_subscribers.remove(queue)

    def test_status_changes_detected(self, complete_state_files):
        """Status changes must be detected between reads."""
        reader = StateReader(state_dir=complete_state_files)
        
        # Initial read
        snapshot1 = reader.get_snapshot()
        assert snapshot1.health.cycle_count == 1337
        
        # Simulate progress
        health_file = complete_state_files / "health.json"
        health = json.loads(health_file.read_text())
        health["cycle_count"] = 1338
        health["last_active"] = datetime.now(timezone.utc).isoformat()
        health_file.write_text(json.dumps(health))
        
        # Clear cache to force re-read
        reader._cache.clear()
        
        # New read should see the change
        snapshot2 = reader.get_snapshot()
        assert snapshot2.health.cycle_count == 1338
        
        # Verify change is detected
        assert reader._state_changed(snapshot2)


class TestErrorRecoveryAndGracefulDegradation:
    """Test graceful handling of errors - aliens must never see crashes."""

    def test_missing_health_file(self, tmp_path):
        """Missing health file should return unknown status, not crash."""
        state_dir = tmp_path / "empty_state"
        state_dir.mkdir()
        
        reader = StateReader(state_dir=state_dir)
        health = reader.get_health()
        
        assert health.status == "unknown"
        assert not health.is_running()
        assert health.pid is None

    def test_corrupt_json_files(self, tmp_path):
        """Corrupt JSON should be handled gracefully."""
        state_dir = tmp_path / "corrupt_state"
        state_dir.mkdir()
        
        # Write corrupt JSON to all files
        corrupt_files = [
            "health.json",
            "registry.json",
            "build_state.json",
            "schedule.json",
        ]
        
        for filename in corrupt_files:
            (state_dir / filename).write_text("{this is not valid json!!!")
        
        reader = StateReader(state_dir=state_dir)
        
        # All methods should return defaults, not crash
        health = reader.get_health()
        assert health.status == "unknown"
        
        agents, tools = reader.get_registry()
        assert agents == []
        assert tools == []
        
        backlog = reader.get_backlog()
        assert backlog == []
        
        schedule = reader.get_schedule()
        assert schedule == []
        
        # Snapshot should still work
        snapshot = reader.get_snapshot()
        assert snapshot is not None

    def test_partial_state_files(self, tmp_path):
        """Partial state should be handled - show what we have."""
        state_dir = tmp_path / "partial_state"
        state_dir.mkdir()
        
        # Only write health file
        health_data = {
            "status": "running",
            "last_active": datetime.now(timezone.utc).isoformat(),
            "run_id": "partial-run",
            "cycle_count": 10,
            "pid": 1234,
        }
        (state_dir / "health.json").write_text(json.dumps(health_data))
        
        reader = StateReader(state_dir=state_dir)
        snapshot = reader.get_snapshot()
        
        # Health should be correct
        assert snapshot.health.status == "running"
        assert snapshot.health.is_running()
        
        # Other data should be empty but valid
        assert snapshot.agents == []
        assert snapshot.tools == []
        assert snapshot.backlog == []
        assert snapshot.schedule == []

    def test_stale_health_detection(self, tmp_path):
        """Stale health (last_active too old) should report not running."""
        state_dir = tmp_path / "stale_state"
        state_dir.mkdir()
        
        # Write health with old last_active (2 minutes ago)
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        health_data = {
            "status": "running",
            "last_active": old_time,
            "run_id": "stale-run",
            "cycle_count": 100,
            "pid": 5678,
        }
        (state_dir / "health.json").write_text(json.dumps(health_data))
        
        reader = StateReader(state_dir=state_dir)
        health = reader.get_health()
        
        # Status says running, but is_running() should be False
        assert health.status == "running"
        assert not health.is_running()  # Because last_active is too old

    def test_api_endpoints_handle_missing_state(self, client):
        """API endpoints should work even without state files."""
        # All these should return valid responses
        response = client.get("/api/ui/status")
        assert response.status_code == 200
        data = response.json()
        assert "connected" in data
        
        response = client.get("/api/ui/agents")
        assert response.status_code == 200
        
        response = client.get("/api/ui/backlog")
        assert response.status_code == 200


class TestDataIntegrityUnderAllConditions:
    """Test data integrity - aliens will scrutinize every detail."""

    def test_all_build_phases_valid(self, complete_state_files):
        """All build phases must be recognized."""
        reader = StateReader(state_dir=complete_state_files)
        
        phases = ["observe", "think", "plan", "build", "execute", "verify", "learn"]
        
        for phase in phases:
            build_file = complete_state_files / "build_state.json"
            build_state = json.loads(build_file.read_text())
            build_state["build_phase"] = phase
            build_file.write_text(json.dumps(build_state))
            
            reader._cache.clear()
            snapshot = reader.get_snapshot()
            assert snapshot.build.phase == phase

    def test_agent_status_values(self, complete_state_files):
        """All agent statuses must be valid."""
        reader = StateReader(state_dir=complete_state_files)
        
        valid_statuses = ["ready", "busy", "offline", "stopped", "error", "starting"]
        
        for status in valid_statuses:
            registry_file = complete_state_files / "registry.json"
            registry = json.loads(registry_file.read_text())
            registry["agents"][0]["status"] = status
            registry_file.write_text(json.dumps(registry))
            
            reader._cache.clear()
            agents, _ = reader.get_registry()
            assert agents[0].status == status

    def test_snapshot_serialization_roundtrip(self, complete_state_files):
        """Snapshot must survive JSON serialization (for SSE transmission)."""
        reader = StateReader(state_dir=complete_state_files)
        snapshot = reader.get_snapshot()
        
        # Convert to dict
        data = reader.snapshot_to_dict(snapshot)
        
        # Serialize to JSON and back
        json_str = json.dumps(data)
        parsed = json.loads(json_str)
        
        # Verify all data survived
        assert parsed["health"]["status"] == "running"
        assert parsed["health"]["is_running"] is True
        assert parsed["health"]["cycle_count"] == 1337
        assert len(parsed["agents"]) == 3
        assert len(parsed["tools"]) == 3
        assert len(parsed["backlog"]) == 3
        assert len(parsed["schedule"]) == 2
        assert len(parsed["loops"]) == 2

    def test_timestamp_handling(self):
        """Timestamps must be parsed correctly regardless of format."""
        # ISO format with timezone
        health1 = HealthInfo(
            status="running",
            last_active="2024-01-31T12:00:00+00:00",
            run_id="test",
            cycle_count=1,
            pid=1,
        )
        # is_running will be False because timestamp is old, but should not crash
        assert isinstance(health1.is_running(), bool)
        
        # ISO format with Z
        health2 = HealthInfo(
            status="running",
            last_active="2024-01-31T12:00:00Z",
            run_id="test",
            cycle_count=1,
            pid=1,
        )
        assert isinstance(health2.is_running(), bool)
        
        # Recent timestamp should show running
        health3 = HealthInfo(
            status="running",
            last_active=datetime.now(timezone.utc).isoformat(),
            run_id="test",
            cycle_count=1,
            pid=1,
        )
        assert health3.is_running() is True


class TestSubAgentStatusVisibility:
    """Test that sub-agent status is properly visible to aliens."""

    def test_agent_list_contains_all_details(self, client):
        """Agent list must contain all details needed by UI."""
        response = client.get("/api/ui/agents")
        assert response.status_code == 200
        data = response.json()
        
        assert "agents" in data
        assert "total" in data
        
        for agent in data["agents"]:
            # Required fields
            assert "name" in agent
            assert "status" in agent
            assert "kind" in agent

    def test_agent_status_endpoint_exists(self, client):
        """Individual agent status endpoint must exist."""
        # Should return 404 for non-existent agent (not 500)
        response = client.get("/api/ui/agents/nonexistent-agent")
        assert response.status_code == 404

    def test_loops_reflect_activity(self, complete_state_files):
        """Loops must reflect current activity."""
        reader = StateReader(state_dir=complete_state_files)
        
        # With active build, loops should show activity
        health = reader.get_health()
        loops = reader.get_loops(health)
        
        assert len(loops) == 2
        
        loop_dict = {l.name: l for l in loops}
        assert "Planner" in loop_dict
        assert "Executor" in loop_dict
        
        # With actions_this_cycle=3, Executor should be active
        assert loop_dict["Executor"].status == "active"


class TestUIPageIntegrity:
    """Test that UI pages render correctly.
    
    Note: The UI is now a React SPA. All routes return the same HTML shell
    with client-side rendering via React.
    """

    def test_all_ui_pages_render(self, client):
        """All UI pages must render HTML without errors."""
        pages = [
            "/",
            "/ui",
            "/ui/dashboard",
            "/ui/agents",
            "/ui/logs",
        ]
        
        for path in pages:
            response = client.get(path)
            assert response.status_code == 200, f"Page {path} failed"
            assert "text/html" in response.headers["content-type"]
            # React SPA shell should include Progenitor title and root div
            assert "Progenitor" in response.text, f"Page {path} missing 'Progenitor'"

    def test_static_assets_accessible(self, client):
        """React bundled assets must be accessible."""
        # With React SPA, assets are bundled and served from /ui/assets/
        response = client.get("/ui/")
        assert response.status_code == 200
        # Should have references to bundled assets
        assert "/ui/assets/" in response.text

    def test_navigation_elements_present(self, client):
        """All pages must return React SPA shell for client-side navigation."""
        pages = ["/", "/ui/dashboard", "/ui/agents", "/ui/logs"]
        
        for page in pages:
            response = client.get(page)
            assert response.status_code == 200
            # React SPA returns same shell for all routes; navigation is client-side
            assert "root" in response.text.lower()


class TestCriticalChatFlow:
    """Test the critical chat flow that aliens will use."""

    def test_models_endpoint_available(self, client):
        """Models endpoint must list available models."""
        response = client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        
        assert "data" in data
        assert len(data["data"]) >= 1
        
        for model in data["data"]:
            assert "id" in model
            assert "object" in model

    def test_chat_completions_endpoint_accepts_requests(self, client):
        """Chat completions must accept requests (may error if agent not running)."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [
                    {"role": "user", "content": "Greetings, we come in peace."}
                ],
                "stream": False,
            },
        )
        
        # Should not be 404 or 405 - endpoint exists
        assert response.status_code in [200, 500, 503]

    def test_responses_endpoint_accepts_requests(self, client):
        """Responses endpoint must accept requests."""
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "Begin first contact protocol",
            },
        )
        
        # Should not be 404 or 405 - endpoint exists
        assert response.status_code in [200, 500, 503]

    def test_health_endpoint(self, client):
        """Health endpoint must always return status."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        
        assert "status" in data
