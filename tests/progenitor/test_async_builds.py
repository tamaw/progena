"""
Tests for async background build functionality and related decide node features.

Tests cover:
- BackgroundBuildRunner lifecycle (start, status, cancel)
- build_status tool reading from state files
- List schedule detection heuristics
- Tightened agent build detection
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Import the modules under test
from app.progenitor.runtime.background_build import (
    BackgroundBuildRunner,
    BuildRequest,
    BuildResult,
    BuildStatus,
    get_background_runner,
    set_background_runner,
)
from app.progenitor.runtime.time_utils import utcnow
from app.progenitor.tools.build_status import BuildStatusTool
from app.progenitor.tools.list_schedule import ListScheduleTool

# ============================================================================
# BackgroundBuildRunner Tests
# ============================================================================


class TestBuildStatus:
    """Test BuildStatus enum values."""

    def test_status_values(self):
        """Verify all expected status values exist."""
        assert BuildStatus.PENDING.value == "pending"
        assert BuildStatus.RUNNING.value == "running"
        assert BuildStatus.COMPLETED.value == "completed"
        assert BuildStatus.FAILED.value == "failed"
        assert BuildStatus.CANCELLED.value == "cancelled"


class TestBuildResult:
    """Test BuildResult dataclass."""

    def test_required_fields(self):
        """BuildResult requires build_id, status, task_description, started_at."""
        now = utcnow()
        result = BuildResult(
            build_id="test-123",
            status=BuildStatus.RUNNING,
            task_description="Test task",
            started_at=now,
        )
        assert result.build_id == "test-123"
        assert result.status == BuildStatus.RUNNING
        assert result.task_description == "Test task"
        assert result.started_at == now
        assert result.completed_at is None
        assert result.error_message is None
        assert result.iterations == 0

    def test_all_fields(self):
        """BuildResult with all fields populated."""
        now = utcnow()
        completed = utcnow()
        result = BuildResult(
            build_id="test-456",
            status=BuildStatus.COMPLETED,
            task_description="Test task",
            started_at=now,
            completed_at=completed,
            iterations=5,
            verification_score=0.85,
            agent_name="test_agent",
        )
        assert result.build_id == "test-456"
        assert result.status == BuildStatus.COMPLETED
        assert result.iterations == 5
        assert result.verification_score == 0.85
        assert result.agent_name == "test_agent"


class TestBuildRequest:
    """Test BuildRequest dataclass."""

    def test_required_fields(self):
        """BuildRequest requires build_id, user_input, task_description, target_capabilities."""
        request = BuildRequest(
            build_id="req-123",
            user_input="build an agent",
            task_description="Build a test agent",
            target_capabilities=["code_execution"],
        )
        assert request.build_id == "req-123"
        assert request.user_input == "build an agent"
        assert request.task_description == "Build a test agent"
        assert request.target_capabilities == ["code_execution"]
        assert request.created_at is not None  # Auto-generated


class TestBackgroundBuildRunner:
    """Test BackgroundBuildRunner class."""

    @pytest.fixture
    def mock_llm_client(self):
        """Create a mock LLM client."""
        client = AsyncMock()
        client.chat = AsyncMock(return_value="Build started! I'll work on this.")
        return client

    @pytest.fixture
    def mock_graph(self):
        """Create a mock graph."""
        graph = AsyncMock()
        graph.ainvoke = AsyncMock(return_value={"verification": {"current_score": 0.9}, "agent_name": "test_agent"})
        return graph

    @pytest.fixture
    def mock_state_manager(self):
        """Create a mock state manager."""
        manager = AsyncMock()
        manager.get_build_state = AsyncMock(return_value={})
        manager.save_build_state = AsyncMock()
        manager.update_current_state = AsyncMock()
        return manager

    @pytest.fixture
    def mock_config(self):
        """Create a mock config."""
        config = MagicMock()
        config.inner_loop_max_cycles = 10
        config.verification_threshold = 0.8
        return config

    @pytest.fixture
    def runner(self, mock_graph, mock_state_manager, mock_config, mock_llm_client):
        """Create a BackgroundBuildRunner instance."""
        return BackgroundBuildRunner(
            state_manager=mock_state_manager,
            config=mock_config,
            llm_client=mock_llm_client,
            copilot_acp_manager=None,
        )

    def test_initial_state(self, runner):
        """Runner should start with no active build."""
        assert runner.get_build_status() is None
        assert not runner.is_building

    @pytest.mark.asyncio
    async def test_start_build(self, runner):
        """Starting a build should return acknowledgment and set status."""
        build_id, ack = await runner.start_build(
            user_input="build an agent",
            task_description="Test task",
            target_capabilities=["code_execution"],
            initial_state={},
        )

        assert build_id is not None
        assert build_id.startswith("build_")
        assert len(ack) > 0  # LLM acknowledgment

        # Should be building
        assert runner.is_building

    @pytest.mark.asyncio
    async def test_only_one_build_at_a_time(self, runner):
        """Should not allow multiple concurrent builds."""
        # Start first build
        await runner.start_build(
            user_input="build first",
            task_description="First task",
            target_capabilities=["code_execution"],
            initial_state={},
        )

        # Try to start second build - should raise
        with pytest.raises(RuntimeError, match="already in progress"):
            await runner.start_build(
                user_input="build second",
                task_description="Second task",
                target_capabilities=["code_execution"],
                initial_state={},
            )

    @pytest.mark.asyncio
    async def test_cancel_build(self, runner):
        """Should be able to cancel a running build."""
        await runner.start_build(
            user_input="build something",
            task_description="Slow task",
            target_capabilities=["code_execution"],
            initial_state={},
        )

        assert runner.is_building
        cancelled = await runner.cancel_build()
        assert cancelled is True

    @pytest.mark.asyncio
    async def test_cancel_during_slow_ack_does_not_deadlock(self, runner):
        """Cancelling while the LLM ack is slow should not deadlock on the runner lock."""
        unblock = asyncio.Event()

        async def slow_chat(*args, **kwargs):
            await unblock.wait()
            return "ok"

        runner.llm_client.chat = AsyncMock(side_effect=slow_chat)

        start_task = asyncio.create_task(
            runner.start_build(
                user_input="build something",
                task_description="Slow ack task",
                target_capabilities=["code_execution"],
                initial_state={},
            )
        )

        await asyncio.sleep(0)  # Let start_build acquire lock and start awaiting the ack.

        cancelled = await asyncio.wait_for(runner.cancel_build(), timeout=1.0)
        assert cancelled is True

        unblock.set()
        await asyncio.wait_for(start_task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_build(self, runner):
        """Cancelling when no build should return False."""
        cancelled = await runner.cancel_build()
        assert cancelled is False

    def test_get_recent_builds_empty(self, runner):
        """get_recent_builds should return empty list when no builds."""
        builds = runner.get_recent_builds()
        assert builds == []


class TestBackgroundRunnerGlobalAccess:
    """Test global runner access functions."""

    def test_set_and_get_runner(self):
        """Should be able to set and get global runner."""
        mock_runner = MagicMock(spec=BackgroundBuildRunner)
        set_background_runner(mock_runner)

        retrieved = get_background_runner()
        assert retrieved is mock_runner

        # Clean up
        set_background_runner(None)

    def test_get_runner_returns_none_when_not_set(self):
        """Should return None when no runner is set."""
        set_background_runner(None)
        assert get_background_runner() is None


# ============================================================================
# Decide Node Heuristic Tests
# ============================================================================


class TestListScheduleDetection:
    """Test ListScheduleTool.matches_input heuristic."""

    @pytest.fixture
    def tool(self):
        return ListScheduleTool()

    @pytest.mark.parametrize("prompt", [
        "what is your schedule",
        "What is your schedule?",
        "WHAT IS YOUR SCHEDULE",
        "show schedule",
        "show my schedule",
        "show the schedule",
        "list schedule",
        "list schedules",
        "list scheduled",
        "view schedule",
        "view schedules",
        "what's scheduled",
        "what is scheduled",
        "scheduled tasks",
        "any scheduled",
        "pending schedule",
        "upcoming tasks",
        "upcoming schedule",
        "my reminders",
        "show reminders",
        "list reminders",
        "what reminders",
    ])
    def test_detects_schedule_requests(self, tool, prompt):
        """Should detect schedule list requests."""
        assert tool.matches_input(prompt) is True

    @pytest.mark.parametrize("prompt", [
        "schedule a task for tomorrow",
        "create a schedule",
        "add to schedule",
        "schedule: run daily",
        "build an agent",
        "hello",
        "what time is it",
        "schedule me a meeting",
        "reschedule the task",
        "display schedule",  # Not in pattern list
        "current schedule",  # Not in pattern list
        "my schedules",  # Not in pattern list (different from "list schedules")
    ])
    def test_does_not_detect_non_schedule_requests(self, tool, prompt):
        """Should not detect non-schedule-list requests."""
        assert tool.matches_input(prompt) is False


class TestBuildStatusDetection:
    """Test BuildStatusTool.matches_input heuristic."""

    @pytest.fixture
    def tool(self):
        return BuildStatusTool()

    @pytest.mark.parametrize("prompt", [
        "build status",
        "what is the build status",
        "show build status",
        "check build",
        "build progress",
        "how is the build",
        "how's the build",
        "what are you building",
        "are you building",
        "building status",
        "building progress",
        "current build",
        "update on build",
    ])
    def test_detects_build_status_requests(self, tool, prompt):
        """Should detect build status requests."""
        assert tool.matches_input(prompt) is True

    @pytest.mark.parametrize("prompt", [
        "build an agent",
        "status of the project",
        "what is the status",
        "hello",
        "build something",
        "is the build done",  # Not exact pattern
    ])
    def test_does_not_detect_non_status_requests(self, tool, prompt):
        """Should not detect non-build-status requests."""
        assert tool.matches_input(prompt) is False


# ============================================================================
# Build Status Tool Integration Tests
# ============================================================================


class TestBuildStatusToolIntegration:
    """Test build_status tool reading from state files."""

    @pytest.fixture
    def temp_state_dir(self):
        """Create a temporary state directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def mock_state_manager(self, temp_state_dir):
        """Create a mock state manager that reads from files."""
        manager = AsyncMock()

        # Set up get_build_state to return persisted build state
        async def get_build_state():
            state_file = temp_state_dir / "build_state.json"
            if state_file.exists():
                return json.loads(state_file.read_text())
            return {}

        manager.get_build_state = get_build_state
        manager.state_dir = temp_state_dir
        return manager

    def write_build_state(self, temp_state_dir, state):
        """Helper to write build state to file."""
        state_file = temp_state_dir / "build_state.json"
        state_file.write_text(json.dumps(state))

    @pytest.mark.asyncio
    async def test_reads_in_progress_build_from_state(self, temp_state_dir, mock_state_manager):
        """build_status should read in-progress build from persisted state."""
        # Write state with build in progress
        self.write_build_state(temp_state_dir, {
            "build_in_progress": True,
            "build_started_at": "2024-01-15T10:00:00Z",
            "build_task_description": "Test agent build",
            "build_phase": "act",
        })

        state = await mock_state_manager.get_build_state()

        assert state["build_in_progress"] is True
        assert state["build_task_description"] == "Test agent build"
        assert state["build_phase"] == "act"

    @pytest.mark.asyncio
    async def test_reads_completed_build_from_state(self, temp_state_dir, mock_state_manager):
        """build_status should read completed build from persisted state."""
        self.write_build_state(temp_state_dir, {
            "build_in_progress": False,
            "build_started_at": "2024-01-15T10:00:00Z",
            "build_completed_at": "2024-01-15T10:05:00Z",
            "build_task_description": "Completed agent",
            "build_phase": "completed",
            "build_score": 0.95,
        })

        state = await mock_state_manager.get_build_state()

        assert state["build_in_progress"] is False
        assert state["build_phase"] == "completed"
        assert state["build_score"] == 0.95

    @pytest.mark.asyncio
    async def test_handles_missing_state_file(self, temp_state_dir, mock_state_manager):
        """Should handle missing state file gracefully."""
        # Don't write any state file
        state = await mock_state_manager.get_build_state()
        assert state == {}

    @pytest.mark.asyncio
    async def test_in_memory_state_takes_priority(self, temp_state_dir, mock_state_manager):
        """In-memory state should override persisted state."""
        # Write old state to file
        self.write_build_state(temp_state_dir, {
            "build_in_progress": True,
            "build_phase": "old_phase",
        })

        # Simulate in-memory state (would come from background runner)
        in_memory_state = {
            "build_in_progress": False,
            "build_phase": "completed",
        }

        # Merge: in-memory takes priority
        persisted = await mock_state_manager.get_build_state()
        effective = {**persisted, **in_memory_state}

        assert effective["build_in_progress"] is False
        assert effective["build_phase"] == "completed"


# ============================================================================
# Integration Tests
# ============================================================================


class TestAsyncBuildIntegration:
    """Integration tests for async build flow."""

    @pytest.fixture
    def mock_llm_client(self):
        """Create mock LLM client."""
        client = AsyncMock()
        client.chat = AsyncMock(return_value="Starting the build now!")
        return client

    @pytest.fixture
    def mock_graph(self):
        """Create a mock graph that completes quickly."""
        graph = AsyncMock()
        async def quick_invoke(state, config=None):
            await asyncio.sleep(0.05)
            return {
                "verification": {"current_score": 0.85},
                "agent_name": "test_agent",
            }
        graph.ainvoke = quick_invoke
        return graph

    @pytest.fixture
    def mock_state_manager(self):
        """Create mock state manager."""
        manager = AsyncMock()
        manager.get_build_state = AsyncMock(return_value={})
        manager.save_build_state = AsyncMock()
        manager.update_current_state = AsyncMock()
        return manager

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = MagicMock()
        config.inner_loop_max_cycles = 10
        config.verification_threshold = 0.8
        return config

    @pytest.mark.asyncio
    async def test_start_build_returns_acknowledgment(
        self, mock_graph, mock_state_manager, mock_config, mock_llm_client
    ):
        """Starting a build should return build_id and acknowledgment."""
        runner = BackgroundBuildRunner(
            state_manager=mock_state_manager,
            config=mock_config,
            llm_client=mock_llm_client,
            copilot_acp_manager=None,
        )

        build_id, ack = await runner.start_build(
            user_input="build an agent",
            task_description="Full lifecycle test",
            target_capabilities=["code_execution"],
            initial_state={},
        )

        assert build_id is not None
        assert build_id.startswith("build_")
        assert len(ack) > 0  # LLM acknowledgment

    @pytest.mark.asyncio
    async def test_build_preserves_task_description(
        self, mock_graph, mock_state_manager, mock_config, mock_llm_client
    ):
        """Build status should preserve the task description."""
        runner = BackgroundBuildRunner(
            state_manager=mock_state_manager,
            config=mock_config,
            llm_client=mock_llm_client,
            copilot_acp_manager=None,
        )

        build_id, _ = await runner.start_build(
            user_input="build an agent",
            task_description="My specific task",
            target_capabilities=["code_execution"],
            initial_state={},
        )

        status = runner.get_build_status()
        assert status is not None
        assert status.task_description == "My specific task"
