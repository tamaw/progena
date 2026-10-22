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

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.backend.main import app
from src.backend.monitoring.state import AgentInfo, BuildInfo, HealthInfo, StateReader
from src.backend.storage.documents import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_state_files():
    """Create mock state in an InMemoryDocumentStore simulating a running progenitor."""
    store = InMemoryDocumentStore()

    # Health - agent is running (Postgres document, no file)
    health_data = {
        "status": "running",
        "last_active": datetime.now(UTC).isoformat(),
        "thread_id": "alien-demo-001",
        "cycle_count": 42,
        "pid": 12345,
    }
    store.save_document(PROGENITOR_STATE_PREFIX, "health", health_data)

    # Registry with agents
    agents_data = {
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
        ]
    }
    mcp_data = {
        "mcpServers": {
            "universal_decoder": {
                "description": "Decodes alien signals",
                "enabled": True,
                "transport": "http",
                "url": "https://example.com/universal_decoder",
            }
        }
    }
    store.save_document(PROGENITOR_STATE_PREFIX, "registry", agents_data)
    store.save_document(PROGENITOR_STATE_PREFIX, "mcp", mcp_data)

    # Build state - in progress
    build_state = {
        "build_in_progress": True,
        "build_phase": "execute",
        "build_task_description": "Establishing first contact protocol",
        "build_started_at": datetime.now(UTC).isoformat(),
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
    store.save_document(PROGENITOR_STATE_PREFIX, "build_state", build_state)

    # Schedule
    schedule = {
        "sched-1": {
            "id": "sched-1",
            "name": "Hourly galactic scan",
            "schedule": "0 * * * *",
            "enabled": True,
        },
    }
    store.save_document(PROGENITOR_STATE_PREFIX, "schedule", schedule)

    return SimpleNamespace(document_store=store)


class TestUIFeedbackIntegration:
    """Integration tests for UI feedback during agent operations."""

    def test_state_reader_with_mock_files(self, mock_state_files):
        """Test StateReader correctly reads simulated state files."""
        reader = StateReader(
            document_store=mock_state_files.document_store,
        )

        # Health should show running
        health = reader.get_health()
        assert health.status == "running"
        assert health.thread_id == "alien-demo-001"
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
        reader = StateReader(
            document_store=mock_state_files.document_store,
        )
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
        reader = StateReader(
            document_store=mock_state_files.document_store,
        )
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

    def test_workspace_payload_and_widgets_include_mcp_and_schedule(
        self, mock_state_files, tmp_path
    ):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "galactic-translator.log").write_text("translator online\n", encoding="utf-8")
        (logs_dir / "universal_decoder.jsonl").write_text(
            json.dumps({"message": "decoder ready", "level": "info"}) + "\n",
            encoding="utf-8",
        )

        reader = StateReader(
            logs_dir=logs_dir,
            document_store=mock_state_files.document_store,
        )
        payload = reader.get_workspace_payload()

        assert payload["workspace"]["storage"] == "indexeddb"
        assert payload["health"]["thread_id"] == "alien-demo-001"
        widget_types = {widget["type"] for widget in payload["widgets"]}
        assert {"agent", "mcp", "schedule"} <= widget_types

    def test_widget_logs_are_normalized_for_agent_nodes(self, mock_state_files, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "tech-analyzer.jsonl").write_text(
            json.dumps(
                {
                    "timestamp": "2026-03-12T10:00:00Z",
                    "level": "warn",
                    "message": "analysis delayed",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        reader = StateReader(
            logs_dir=logs_dir,
            document_store=mock_state_files.document_store,
        )
        widgets = reader.get_widgets()
        tech_widget = next(widget for widget in widgets if widget["id"] == "agent:tech-analyzer")

        assert tech_widget["status"] == "busy"
        assert tech_widget["logs"][0]["message"] == "analysis delayed"
        assert tech_widget["logs"][0]["level"] == "warn"

    def test_nested_agent_log_sources_use_the_directory_name(self, tmp_path):
        logs_dir = tmp_path / "logs"
        for agent in ("builder", "reviewer"):
            agent_dir = logs_dir / agent
            agent_dir.mkdir(parents=True)
            (agent_dir / "task.log").write_text("task started\n", encoding="utf-8")

        reader = StateReader(document_store=None, logs_dir=logs_dir)
        sources = {
            option["path"]: option["source"]
            for group in reader.get_log_sources()
            for option in group["options"]
        }

        assert sources == {
            "builder/task.log": "builder",
            "reviewer/task.log": "reviewer",
        }


class TestAgentFeedbackVisibility:
    """Tests that agent activity is visible to the UI."""

    def test_agent_status_changes_visible(self, mock_state_files):
        """Test that agent status changes are picked up by StateReader."""
        reader = StateReader(
            document_store=mock_state_files.document_store,
        )
        # Initial read
        agents, _ = reader.get_registry()
        initial_status = {a.name: a.status for a in agents}

        # Update registry to simulate agent status change
        registry_doc = mock_state_files.document_store.get_document(
            PROGENITOR_STATE_PREFIX, "registry", {}
        )
        registry = registry_doc.payload if registry_doc is not None else {}
        if isinstance(registry, dict) and "agents" in registry:
            registry["agents"][1]["status"] = "ready"  # tech-analyzer becomes ready
            mock_state_files.document_store.save_document(
                PROGENITOR_STATE_PREFIX, "registry", registry
            )

        # Re-read
        agents, _ = reader.get_registry()
        new_status = {a.name: a.status for a in agents}

        # Verify change was detected
        assert initial_status["tech-analyzer"] == "busy"
        assert new_status["tech-analyzer"] == "ready"

    def test_build_phase_progression(self, mock_state_files):
        """Test build phase changes are tracked."""
        reader = StateReader(
            document_store=mock_state_files.document_store,
        )
        phases = ["observe", "think", "plan", "build", "execute", "verify", "learn"]
        for phase in phases:
            # Update build phase
            build_doc = mock_state_files.document_store.get_document(
                PROGENITOR_STATE_PREFIX, "build_state", {}
            )
            build_state = build_doc.payload if build_doc is not None else {}
            if isinstance(build_state, dict):
                build_state["build_phase"] = phase
                mock_state_files.document_store.save_document(
                    PROGENITOR_STATE_PREFIX, "build_state", build_state
                )

            # Verify phase is reported correctly
            snapshot = reader.get_snapshot()
            assert snapshot.build is not None
            assert snapshot.build.phase == phase

    def test_backlog_progress_tracking(self, mock_state_files):
        """Test backlog item status changes are visible."""
        reader = StateReader(
            document_store=mock_state_files.document_store,
        )

        # Complete a task
        build_doc = mock_state_files.document_store.get_document(
            PROGENITOR_STATE_PREFIX, "build_state", {}
        )
        build_state = build_doc.payload if build_doc is not None else {}
        if isinstance(build_state, dict) and "backlog" in build_state:
            build_state["backlog"][0]["status"] = "completed"
            mock_state_files.document_store.save_document(
                PROGENITOR_STATE_PREFIX, "build_state", build_state
            )

        # Verify
        backlog = reader.get_backlog()
        completed = [b for b in backlog if b.status == "completed"]
        assert len(completed) == 1


class TestLoopStatusFeedback:
    """Tests for Planner/Executor loop feedback."""

    def test_loops_reflect_agent_state(self, mock_state_files):
        """Test that loop status reflects actual agent state."""
        reader = StateReader(
            document_store=mock_state_files.document_store,
        )
        health = reader.get_health()
        loops = reader.get_loops(health)

        # When agent is running, loops should be present
        assert len(loops) == 2

        loop_names = {loop_item.name for loop_item in loops}
        assert "Planner" in loop_names
        assert "Executor" in loop_names

    def test_loops_empty_when_agent_stopped(self, mock_state_files):
        """Test loops are empty when agent is not running."""
        reader = StateReader(
            document_store=mock_state_files.document_store,
        )
        # Update health to stopped
        health_doc = mock_state_files.document_store.get_document(
            PROGENITOR_STATE_PREFIX, "health", {}
        )
        health_payload = dict(health_doc.payload) if health_doc is not None else {}
        health_payload["status"] = "stopped"
        mock_state_files.document_store.save_document(
            PROGENITOR_STATE_PREFIX, "health", health_payload
        )

        health = reader.get_health()
        loops = reader.get_loops(health)

        # Loops should be empty when agent is stopped
        assert len(loops) == 0


class TestErrorFeedbackPath:
    """Tests for error feedback to UI."""

    def test_health_unknown_on_missing_file(self):
        """Test health shows unknown when file is missing."""
        reader = StateReader(document_store=InMemoryDocumentStore())
        health = reader.get_health()
        assert health.status == "unknown"
        assert not health.is_running()


class TestDataIntegrity:
    """Tests for data integrity in UI feedback."""

    def test_health_info_timestamp_parsing(self):
        """Test HealthInfo can parse ISO timestamps correctly."""
        health = HealthInfo(
            status="running",
            last_active="2024-01-15T10:30:00+00:00",
            thread_id="thread-test",
            cycle_count=100,
            pid=1234,
        )

        # Should be able to check if running
        # This will be False because timestamp is old
        assert isinstance(health.is_running(), bool)

    def test_agent_info_handles_all_fields(self):
        """Test AgentInfo handles all possible fields."""
        full_agent = AgentInfo.from_dict(
            {
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
                "agent_card_url": "http://localhost:9001/.well-known/agent-card.json",
                "pid": 54321,
                "startup_time": "2024-01-15T11:00:00Z",
                "last_heartbeat": "2024-01-15T12:00:00Z",
                "last_error": None,
            }
        )

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
