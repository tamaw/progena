"""
Tests for app/progenitor/constants.py

Verifies enum values, constants, and backward compatibility.
"""

from app.progenitor.constants import (
    DEFAULT_BUILD_CONFIDENCE_THRESHOLD,
    DEFAULT_MAX_INNER_ITERATIONS,
    DEFAULT_MAX_OUTER_ITERATIONS,
    DEFAULT_VERIFICATION_TARGET_SCORE,
    BuildPhase,
    RouteTarget,
    SystemToolName,
)


class TestBuildPhaseEnum:
    """Tests for BuildPhase enum."""

    def test_all_phases_exist(self):
        """Verify all expected build phases are defined."""
        expected = ["OBSERVE", "THINK", "PLAN", "BUILD", "EXECUTE", "VERIFY", "LEARN"]
        for phase in expected:
            assert hasattr(BuildPhase, phase), f"Missing phase: {phase}"

    def test_phase_values_are_lowercase(self):
        """Phase values should be lowercase for graph node names."""
        assert BuildPhase.OBSERVE.value == "observe"
        assert BuildPhase.THINK.value == "think"
        assert BuildPhase.PLAN.value == "plan"
        assert BuildPhase.BUILD.value == "build"
        assert BuildPhase.EXECUTE.value == "execute"
        assert BuildPhase.VERIFY.value == "verify"
        assert BuildPhase.LEARN.value == "learn"

    def test_enum_string_representation(self):
        """Enum should have sensible string representation."""
        assert str(BuildPhase.OBSERVE) == "BuildPhase.OBSERVE"


class TestRouteTargetEnum:
    """Tests for RouteTarget enum."""

    def test_outer_loop_targets(self):
        """Verify outer loop routing targets."""
        assert RouteTarget.INTAKE.value == "intake"
        assert RouteTarget.DECIDE.value == "decide"
        assert RouteTarget.RESPOND.value == "respond"
        assert RouteTarget.IDLE.value == "idle"
        assert RouteTarget.END.value == "end"
        assert RouteTarget.PICK_NEXT.value == "pick_next"

    def test_inner_loop_targets(self):
        """Verify inner loop routing targets."""
        assert RouteTarget.OBSERVE.value == "observe"
        assert RouteTarget.DONE.value == "done"
        assert RouteTarget.LEARN.value == "learn"

    def test_action_targets(self):
        """Verify action-related targets."""
        assert RouteTarget.VALIDATE.value == "validate"
        assert RouteTarget.COMMIT_CORE.value == "commit_core"
        assert RouteTarget.WORKTREE_PREP.value == "worktree_prep"


class TestSystemToolNameEnum:
    """Tests for SystemToolName enum."""

    def test_all_system_tools_exist(self):
        """Verify all expected system tools are defined."""
        expected = [
            "LIST_TOOLS", "BUILD_STATUS", "REDISCOVER_AGENTS",
            "LIST_AGENTS", "SCHEDULE_TASK", "LIST_SCHEDULE",
            "CURRENT_TIME", "SELF_REFLECT"
        ]
        for tool in expected:
            assert hasattr(SystemToolName, tool), f"Missing tool: {tool}"

    def test_tool_names_match_usage(self):
        """Tool values should match how they're invoked."""
        assert SystemToolName.LIST_TOOLS.value == "list_tools"
        assert SystemToolName.BUILD_STATUS.value == "build_status"
        assert SystemToolName.LIST_SCHEDULE.value == "list_schedule"
        assert SystemToolName.LIST_AGENTS.value == "list_agents"
        assert SystemToolName.CURRENT_TIME.value == "current_time"
        assert SystemToolName.SELF_REFLECT.value == "self_reflect"


class TestDefaultConstants:
    """Tests for default constant values."""

    def test_iteration_limits(self):
        """Verify iteration limit defaults are sensible."""
        assert DEFAULT_MAX_INNER_ITERATIONS == 5
        assert DEFAULT_MAX_INNER_ITERATIONS > 0
        assert DEFAULT_MAX_INNER_ITERATIONS < 100  # Reasonable upper bound

    def test_outer_iteration_limits(self):
        """Verify outer loop iteration limits are sensible."""
        assert DEFAULT_MAX_OUTER_ITERATIONS == 10
        assert DEFAULT_MAX_OUTER_ITERATIONS > 0

    def test_verification_threshold(self):
        """Verify verification score defaults are sensible."""
        assert DEFAULT_VERIFICATION_TARGET_SCORE == 0.8
        assert 0.0 <= DEFAULT_VERIFICATION_TARGET_SCORE <= 1.0

    def test_build_confidence_threshold(self):
        """Verify build confidence threshold defaults."""
        assert DEFAULT_BUILD_CONFIDENCE_THRESHOLD == 0.95
        assert 0.0 <= DEFAULT_BUILD_CONFIDENCE_THRESHOLD <= 1.0

    def test_constants_are_immutable(self):
        """Constants should not be accidentally modifiable."""
        # These are module-level constants (not mutable objects)
        # Just verify they have the expected types
        assert isinstance(DEFAULT_MAX_INNER_ITERATIONS, int)
        assert isinstance(DEFAULT_VERIFICATION_TARGET_SCORE, float)
        assert isinstance(DEFAULT_BUILD_CONFIDENCE_THRESHOLD, float)
