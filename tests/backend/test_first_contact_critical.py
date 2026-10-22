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
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.backend.events.stream as events_stream
import app.backend.lifecycle.state as lifecycle_state
from app.backend.main import app
from app.backend.monitoring.state import HealthInfo, StateReader
from app.backend.storage.documents import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore


@pytest.fixture
def client():
    """Create a test client for the aliens to use."""
    return TestClient(app)


@pytest.fixture
def complete_state_files():
    """
    Create a complete set of state documents simulating a fully operational
    Progenitor agent with multiple sub-agents - ready for alien inspection.
    """
    store = InMemoryDocumentStore()

    # Health - agent is running strong
    health_data = {
        "status": "running",
        "last_active": datetime.now(UTC).isoformat(),
        "thread_id": "first-contact-mission-001",
        "cycle_count": 1337,
        "pid": 42424,
    }
    store.save_document(PROGENITOR_STATE_PREFIX, "health", health_data)

    # Registry with multiple diverse agents
    agents_data = {
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
        ]
    }
    mcp_data = {
        "mcpServers": {
            "universal_translator": {
                "description": "Translates any language",
                "enabled": True,
                "transport": "http",
                "url": "https://example.com/universal_translator",
            },
            "spectrum_analyzer": {
                "description": "Analyzes signal spectrums",
                "enabled": True,
                "transport": "http",
                "url": "https://example.com/spectrum_analyzer",
            },
            "pattern_matcher": {
                "description": "Matches complex patterns",
                "enabled": True,
                "transport": "http",
                "url": "https://example.com/pattern_matcher",
            },
        }
    }
    store.save_document(PROGENITOR_STATE_PREFIX, "registry", agents_data)
    store.save_document(PROGENITOR_STATE_PREFIX, "mcp", mcp_data)

    # Build state - actively working on first contact
    build_state = {
        "build_in_progress": True,
        "build_phase": "execute",
        "build_task_description": "Establishing first contact protocol",
        "build_started_at": (datetime.now(UTC) - timedelta(minutes=15)).isoformat(),
        "iteration": {"current": 7},
        "verification": {"current_score": 0.89},
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
    store.save_document(PROGENITOR_STATE_PREFIX, "build_state", build_state)

    # Schedule
    schedule = {
        "sched-1": {
            "id": "sched-1",
            "name": "Signal scan",
            "schedule": "*/5 * * * *",
            "enabled": True,
            "next_run": None,
        },
        "sched-2": {
            "id": "sched-2",
            "name": "Protocol check",
            "schedule": "0 * * * *",
            "enabled": True,
            "next_run": None,
        },
    }
    store.save_document(PROGENITOR_STATE_PREFIX, "schedule", schedule)

    return SimpleNamespace(document_store=store)


class TestCriticalUIDataPipeline:
    """Test the complete data pipeline from state files to UI."""

    def test_state_reader_with_complete_state(self, complete_state_files):
        """StateReader must correctly read all state data for aliens."""
        reader = StateReader(document_store=complete_state_files.document_store)
        snapshot = reader.get_snapshot()

        # Health
        assert snapshot.health.status == "running"
        assert snapshot.health.thread_id == "first-contact-mission-001"
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
        events_stream._ui_stream_subscribers.clear()

        # Create multiple subscriber queues (like multiple alien terminals)
        queues = [asyncio.Queue() for _ in range(5)]
        for q in queues:
            events_stream._ui_stream_subscribers[q] = None

        try:
            # Broadcast an important message
            events_stream.broadcast_ui_event(
                "first_contact",
                {
                    "status": "initiated",
                    "signal_strength": 0.98,
                    "decoded_message": "Greetings from Earth",
                },
                scope="chat",
            )

            # All subscribers must receive the event
            for i, q in enumerate(queues):
                assert not q.empty(), f"Subscriber {i} did not receive the event"
                event = q.get_nowait()
                assert event["type"] == "ui.event"
                envelope = event["envelope"]
                assert envelope.type == "first_contact"
                assert envelope.payload["status"] == "initiated"
        finally:
            for q in queues:
                events_stream._ui_stream_subscribers.pop(q, None)

    def test_multiple_broadcasts_in_sequence(self):
        """Multiple events must arrive in order."""
        events_stream._ui_stream_subscribers.clear()
        queue = asyncio.Queue()
        events_stream._ui_stream_subscribers[queue] = None

        try:
            events = [
                ("signal_detected", {"source": "Alpha Centauri"}),
                ("signal_analyzing", {"progress": 50}),
                ("signal_decoded", {"message": "Peace"}),
                ("response_sent", {"status": "success"}),
            ]

            for event_type, data in events:
                events_stream.broadcast_ui_event(event_type, data, scope="chat")

            # Verify all events arrived in order
            received = []
            while not queue.empty():
                received.append(queue.get_nowait())

            assert len(received) == 4
            assert received[0]["envelope"].type == "signal_detected"
            assert received[1]["envelope"].type == "signal_analyzing"
            assert received[2]["envelope"].type == "signal_decoded"
            assert received[3]["envelope"].type == "response_sent"
        finally:
            events_stream._ui_stream_subscribers.pop(queue, None)

    def test_status_changes_detected(self, complete_state_files):
        """Status changes must be detected between reads."""
        reader = StateReader(document_store=complete_state_files.document_store)

        # Initial read
        snapshot1 = reader.get_snapshot()
        assert snapshot1.health.cycle_count == 1337

        # Simulate progress
        store = complete_state_files.document_store
        health_doc = store.get_document(PROGENITOR_STATE_PREFIX, "health", {})
        health = dict(health_doc.payload) if health_doc is not None else {}
        health["cycle_count"] = 1338
        health["last_active"] = datetime.now(UTC).isoformat()
        store.save_document(PROGENITOR_STATE_PREFIX, "health", health)

        # New read should see the change (no cache to clear - reads from store directly)

        # New read should see the change
        snapshot2 = reader.get_snapshot()
        assert snapshot2.health.cycle_count == 1338

        # Verify change is detected
        assert reader._state_changed(snapshot2)


class TestErrorRecoveryAndGracefulDegradation:
    """Test graceful handling of errors - aliens must never see crashes."""

    def test_missing_health_file(self):
        """Missing health file should return unknown status, not crash."""

        reader = StateReader(document_store=InMemoryDocumentStore())
        health = reader.get_health()

        assert health.status == "unknown"
        assert not health.is_running()
        assert health.pid is None

    def test_partial_state_files(self):
        """Partial state should be handled - show what we have."""

        # Only seed health (Postgres document, no file)
        health_data = {
            "status": "running",
            "last_active": datetime.now(UTC).isoformat(),
            "thread_id": "partial-run",
            "cycle_count": 10,
            "pid": 1234,
        }
        store = InMemoryDocumentStore()
        store.save_document(PROGENITOR_STATE_PREFIX, "health", health_data)

        reader = StateReader(document_store=store)
        snapshot = reader.get_snapshot()

        # Health should be correct
        assert snapshot.health.status == "running"
        assert snapshot.health.is_running()

        # Other data should be empty but valid
        assert snapshot.agents == []
        assert snapshot.tools == []
        assert snapshot.backlog == []
        assert snapshot.schedule == []

    def test_stale_health_detection(self):
        """Stale health (last_active too old) should report not running."""

        # Seed health with old last_active (2 minutes ago)
        old_time = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
        health_data = {
            "status": "running",
            "last_active": old_time,
            "thread_id": "stale-run",
            "cycle_count": 100,
            "pid": 5678,
        }
        store = InMemoryDocumentStore()
        store.save_document(PROGENITOR_STATE_PREFIX, "health", health_data)

        reader = StateReader(document_store=store)
        health = reader.get_health()

        # Status says running, but is_running() should be False
        assert health.status == "running"
        assert not health.is_running()  # Because last_active is too old


class TestDataIntegrityUnderAllConditions:
    """Test data integrity - aliens will scrutinize every detail."""

    def test_all_build_phases_valid(self, complete_state_files):
        """All build phases must be recognized."""
        reader = StateReader(document_store=complete_state_files.document_store)

        phases = ["observe", "think", "plan", "build", "execute", "verify", "learn"]

        for phase in phases:
            build_doc = complete_state_files.document_store.get_document(
                PROGENITOR_STATE_PREFIX, "build_state", {}
            )
            build_state = dict(build_doc.payload) if build_doc is not None else {}
            build_state["build_phase"] = phase
            complete_state_files.document_store.save_document(
                PROGENITOR_STATE_PREFIX, "build_state", build_state
            )

            snapshot = reader.get_snapshot()
            assert snapshot.build is not None
            assert snapshot.build.phase == phase

    def test_agent_status_values(self, complete_state_files):
        """All agent statuses must be valid."""
        reader = StateReader(document_store=complete_state_files.document_store)

        valid_statuses = ["ready", "busy", "offline", "stopped", "error", "starting"]

        for status in valid_statuses:
            registry_doc = complete_state_files.document_store.get_document(
                PROGENITOR_STATE_PREFIX, "registry", {}
            )
            registry = dict(registry_doc.payload) if registry_doc is not None else {}
            registry["agents"][0]["status"] = status
            complete_state_files.document_store.save_document(
                PROGENITOR_STATE_PREFIX, "registry", registry
            )

            agents, _ = reader.get_registry()
            assert agents[0].status == status

    def test_snapshot_serialization_roundtrip(self, complete_state_files):
        """Snapshot must survive JSON serialization (for SSE transmission)."""
        reader = StateReader(document_store=complete_state_files.document_store)
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
            thread_id="thread-test",
            cycle_count=1,
            pid=1,
        )
        # is_running will be False because timestamp is old, but should not crash
        assert isinstance(health1.is_running(), bool)

        # ISO format with Z
        health2 = HealthInfo(
            status="running",
            last_active="2024-01-31T12:00:00Z",
            thread_id="thread-test",
            cycle_count=1,
            pid=1,
        )
        assert isinstance(health2.is_running(), bool)

        # Recent timestamp should show running
        health3 = HealthInfo(
            status="running",
            last_active=datetime.now(UTC).isoformat(),
            thread_id="thread-test",
            cycle_count=1,
            pid=1,
        )
        assert health3.is_running() is True


class TestSubAgentStatusVisibility:
    """Test that sub-agent status is properly visible to aliens."""

    def test_loops_reflect_activity(self, complete_state_files):
        """Loops must reflect current activity."""
        reader = StateReader(document_store=complete_state_files.document_store)

        # With active build, loops should show activity
        health = reader.get_health()
        loops = reader.get_loops(health)

        assert len(loops) == 2

        loop_dict = {loop_item.name: loop_item for loop_item in loops}
        assert "Planner" in loop_dict
        assert "Executor" in loop_dict

        # With actions_this_cycle=3, Executor should be active
        assert loop_dict["Executor"].status == "active"


class TestCriticalChatFlow:
    """Test the critical chat flow that aliens will use."""

    def test_responses_endpoint_acknowledges_without_generated_text(self, client):
        """Foreground generation data is stream-only, including on the critical path."""
        from app.backend.chat.models import AgentResponse

        original_client = lifecycle_state.client
        request_id = "first-contact-generation"

        class _MockClient:
            async def send_message(self, *args, **kwargs):
                return AgentResponse(response="Peace", reasoning=None, reasoning_tokens=None)

        lifecycle_state.client = _MockClient()
        try:
            response = client.post(
                "/api/ui/responses",
                json={
                    "model": "progenitor-v1",
                    "input": "Greetings, we come in peace.",
                    "stream": False,
                },
                headers={"X-Request-ID": request_id},
            )
        finally:
            lifecycle_state.client = original_client

        assert response.status_code == 202
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["chat.response.started"],
        }
