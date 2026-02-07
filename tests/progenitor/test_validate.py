"""
Comprehensive tests for the enhanced Validate node.

Tests cover:
- Schema changes (ValidationFinding, ValidationAction, ValidationStatistics, ValidationResultV2)
- File/line extraction from Python errors
- Python traceback parsing
- Build output parsers (pytest, pyright, mypy, ruff)
- Finding deduplication
- Pattern detection
- Finding prioritization
- Historical comparison
- LLM severity assessment
- Validation actions (Python tool runner)
- Full validate_node integration
- ValidationConfig from env
"""

import json
import os
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.progenitor.nodes.validate import (
    _extract_file_location,
    _parse_python_traceback,
    _parse_pytest_output,
    _parse_pyright_output,
    _parse_mypy_output,
    _parse_ruff_output,
    _deduplicate_findings,
    _detect_patterns,
    _prioritize_findings,
    _analyze_execution_result,
    _analyze_execution_run,
    _analyze_build_output,
    _get_project_directory,
    create_node,
)
from app.progenitor.state.execution_schemas import (
    ValidationFinding,
    ValidationAction,
    ValidationStatistics,
    ValidationResultV2,
)
from app.progenitor.config import ValidationConfig, ProgenitorConfig
from app.progenitor.llm.response_models import (
    FindingSeveritySpec,
    ValidationSeverityResponse,
)


# ============================================================================
# Helpers
# ============================================================================

def _make_id_gen():
    """Create a simple finding ID generator for tests."""
    counter = 0
    def make_id():
        nonlocal counter
        counter += 1
        return f"test_{counter}"
    return make_id


def _make_config(**overrides) -> ProgenitorConfig:
    """Create a ProgenitorConfig with defaults suitable for testing."""
    config = ProgenitorConfig.__new__(ProgenitorConfig)
    config.llm = MagicMock()
    config.runtime = MagicMock()
    config.runtime.timezone = "UTC"
    config.storage = MagicMock()
    config.copilot = MagicMock()
    config.scheduler = MagicMock()
    config.telemetry = MagicMock()

    validation_config = ValidationConfig()
    for k, v in overrides.items():
        setattr(validation_config, k, v)
    config.validation = validation_config

    return config


# ============================================================================
# Schema Tests
# ============================================================================


class TestValidationFinding:
    """Tests for the enhanced ValidationFinding schema."""

    def test_basic_creation(self):
        f = ValidationFinding(
            finding_id="f1",
            category="error",
            source="pytest",
            message="test failed",
        )
        assert f.finding_id == "f1"
        assert f.severity == "medium"  # default
        assert f.suggested_fix is None
        assert f.stack_trace is None
        assert f.related_finding_ids == []
        assert f.is_root_cause is False
        assert f.duplicate_of is None
        assert f.duplicate_count == 0

    def test_full_creation(self):
        f = ValidationFinding(
            finding_id="f2",
            category="error",
            source="pyright",
            message="type error",
            file_path="app/main.py",
            line_number=42,
            severity="high",
            evidence="Full error text here",
            stack_trace=["app/main.py:42", "app/utils.py:10"],
            suggested_fix="Add type annotation",
            related_finding_ids=["f1"],
            is_root_cause=True,
            duplicate_count=2,
        )
        assert f.file_path == "app/main.py"
        assert f.line_number == 42
        assert f.stack_trace is not None
        assert len(f.stack_trace) == 2
        assert f.suggested_fix == "Add type annotation"
        assert f.is_root_cause is True
        assert f.duplicate_count == 2


class TestValidationAction:
    """Tests for the ValidationAction schema."""

    def test_basic_creation(self):
        a = ValidationAction(
            action_id="act_1",
            tool="pytest",
            command="python -m pytest app/",
            project_directory="app/agents/weather",
        )
        assert a.tool == "pytest"
        assert a.success is False
        assert a.error_count == 0

    def test_full_creation(self):
        a = ValidationAction(
            action_id="act_2",
            tool="pyright",
            command="python -m pyright app/",
            project_directory="app/agents/weather",
            exit_code=0,
            stdout="0 errors",
            stderr="",
            duration_ms=1500.0,
            success=True,
            error_count=0,
            warning_count=3,
        )
        assert a.success is True
        assert a.warning_count == 3
        assert a.duration_ms == 1500.0


class TestValidationResultV2:
    """Tests for the enhanced ValidationResultV2 schema."""

    def test_compute_counts(self):
        result = ValidationResultV2(
            iteration=1,
            findings=[
                ValidationFinding(finding_id="f1", category="error", source="x", message="e1"),
                ValidationFinding(finding_id="f2", category="error", source="x", message="e2"),
                ValidationFinding(finding_id="f3", category="warning", source="x", message="w1"),
                ValidationFinding(finding_id="f4", category="info", source="x", message="i1"),
            ],
        )
        result.compute_counts()
        assert result.error_count == 2
        assert result.warning_count == 1
        assert result.info_count == 1
        assert result.has_blocking_issues is True

    def test_compute_counts_no_errors(self):
        result = ValidationResultV2(
            iteration=1,
            findings=[
                ValidationFinding(finding_id="f1", category="warning", source="x", message="w1"),
            ],
        )
        result.compute_counts()
        assert result.has_blocking_issues is False

    def test_compute_statistics(self):
        result = ValidationResultV2(
            iteration=2,
            findings=[
                ValidationFinding(finding_id="f1", category="error", source="pytest", message="e", severity="high"),
                ValidationFinding(finding_id="f2", category="warning", source="ruff", message="w", severity="low"),
            ],
        )
        result.compute_counts()
        result.compute_statistics(
            previous_error_count=3,
            duplicates_suppressed=2,
            patterns_detected=1,
            smoke_test_passed=True,
        )
        assert result.statistics is not None
        assert result.statistics.total_findings == 2
        assert result.statistics.by_category == {"error": 1, "warning": 1}
        assert result.statistics.by_severity == {"high": 1, "low": 1}
        assert result.statistics.by_source == {"pytest": 1, "ruff": 1}
        assert result.statistics.trend == "improving"  # 1 error < 3 previous
        assert result.statistics.smoke_test_passed is True
        assert result.statistics.duplicates_suppressed == 2
        assert result.statistics.patterns_detected == 1

    def test_trend_degrading(self):
        result = ValidationResultV2(
            iteration=3,
            findings=[
                ValidationFinding(finding_id="f1", category="error", source="x", message="e1"),
                ValidationFinding(finding_id="f2", category="error", source="x", message="e2"),
                ValidationFinding(finding_id="f3", category="error", source="x", message="e3"),
            ],
        )
        result.compute_counts()
        result.compute_statistics(previous_error_count=1)
        assert result.statistics is not None
        assert result.statistics.trend == "degrading"

    def test_trend_stable(self):
        result = ValidationResultV2(
            iteration=3,
            findings=[
                ValidationFinding(finding_id="f1", category="error", source="x", message="e1"),
            ],
        )
        result.compute_counts()
        result.compute_statistics(previous_error_count=1)
        assert result.statistics is not None
        assert result.statistics.trend == "stable"


# ============================================================================
# File/Line Extraction Tests
# ============================================================================


class TestFileLocationExtraction:
    """Tests for _extract_file_location."""

    def test_pytest_format(self):
        fp, ln = _extract_file_location("tests/test_foo.py:42: AssertionError")
        assert fp == "tests/test_foo.py"
        assert ln == 42

    def test_mypy_format(self):
        fp, ln = _extract_file_location("app/main.py:15: error: Incompatible types")
        assert fp == "app/main.py"
        assert ln == 15

    def test_traceback_format(self):
        fp, ln = _extract_file_location('  File "app/utils.py", line 99, in process')
        assert fp == "app/utils.py"
        assert ln == 99

    def test_generic_python_format(self):
        fp, ln = _extract_file_location("Error in app/server.py:200 something")
        assert fp == "app/server.py"
        assert ln == 200

    def test_no_match(self):
        fp, ln = _extract_file_location("Some random error message")
        assert fp is None
        assert ln is None


class TestPythonTracebackParsing:
    """Tests for _parse_python_traceback."""

    def test_valid_traceback(self):
        tb = '''Traceback (most recent call last):
  File "app/main.py", line 10, in run
    result = process()
  File "app/utils.py", line 42, in process
    return compute()
ValueError: invalid value'''
        frames = _parse_python_traceback(tb)
        assert frames is not None
        assert len(frames) == 2
        assert frames[0] == "app/main.py:10"
        assert frames[1] == "app/utils.py:42"

    def test_no_traceback(self):
        assert _parse_python_traceback("Just an error message") is None

    def test_empty_traceback(self):
        tb = "Traceback (most recent call last):\nSomeError: oops"
        frames = _parse_python_traceback(tb)
        assert frames is None  # No File lines found


# ============================================================================
# Python Output Parser Tests
# ============================================================================


class TestParsePytestOutput:
    """Tests for _parse_pytest_output."""

    def test_failed_tests(self):
        output = """FAILED tests/test_foo.py::test_bar - AssertionError: 1 != 2
FAILED tests/test_baz.py::test_qux - ValueError: bad value
"""
        findings = _parse_pytest_output(output, _make_id_gen())
        assert len(findings) == 2
        assert all(f.category == "error" for f in findings)
        assert all(f.source == "pytest" for f in findings)

    def test_error_collection(self):
        output = "ERROR tests/test_broken.py - ImportError: No module named 'foo'\n"
        findings = _parse_pytest_output(output, _make_id_gen())
        assert len(findings) == 1
        assert findings[0].category == "error"
        assert "ImportError" in findings[0].message

    def test_no_tests_ran(self):
        output = "collected 0 items\nno tests ran"
        findings = _parse_pytest_output(output, _make_id_gen())
        assert len(findings) == 1
        assert findings[0].category == "warning"
        assert "No tests" in findings[0].message

    def test_summary_fallback(self):
        output = "========================= 3 failed, 2 passed =========================\n"
        findings = _parse_pytest_output(output, _make_id_gen())
        assert len(findings) == 1
        assert findings[0].category == "error"

    def test_clean_output(self):
        output = "========================= 5 passed =========================\n"
        findings = _parse_pytest_output(output, _make_id_gen())
        assert len(findings) == 0


class TestParsePyrightOutput:
    """Tests for _parse_pyright_output."""

    def test_errors_and_warnings(self):
        output = """  app/main.py:10:5 - error: Cannot assign type "str" to "int"
  app/utils.py:20:1 - warning: Variable is unused
  app/config.py:5:3 - information: Type could be narrowed
"""
        findings = _parse_pyright_output(output, _make_id_gen())
        assert len(findings) == 3
        assert findings[0].category == "error"
        assert findings[0].file_path == "app/main.py"
        assert findings[0].line_number == 10
        assert findings[1].category == "warning"
        assert findings[2].category == "info"

    def test_summary_fallback(self):
        output = "5 errors, 3 warnings, 1 information\n"
        findings = _parse_pyright_output(output, _make_id_gen())
        assert len(findings) == 1
        assert findings[0].category == "error"

    def test_clean(self):
        output = "0 errors, 0 warnings, 0 informations\n"
        findings = _parse_pyright_output(output, _make_id_gen())
        assert len(findings) == 0


class TestParseMypyOutput:
    """Tests for _parse_mypy_output."""

    def test_errors(self):
        output = """app/main.py:12: error: Incompatible return value type
app/utils.py:5: warning: Unused import
app/config.py:8: note: See docs for more info
"""
        findings = _parse_mypy_output(output, _make_id_gen())
        assert len(findings) == 3
        assert findings[0].category == "error"
        assert findings[0].file_path == "app/main.py"
        assert findings[1].category == "warning"
        assert findings[2].category == "info"


class TestParseRuffOutput:
    """Tests for _parse_ruff_output."""

    def test_findings(self):
        output = """app/main.py:10:1: E501 Line too long (120 > 88)
app/utils.py:5:1: F401 'os' imported but unused
"""
        findings = _parse_ruff_output(output, _make_id_gen())
        assert len(findings) == 2
        assert all(f.category == "warning" for f in findings)
        assert all(f.source == "ruff" for f in findings)
        assert findings[0].file_path == "app/main.py"
        assert findings[0].line_number == 10
        assert "E501" in findings[0].message


# ============================================================================
# Deduplication Tests
# ============================================================================


class TestDeduplication:
    """Tests for _deduplicate_findings."""

    def test_removes_exact_duplicates(self):
        findings = [
            ValidationFinding(finding_id="f1", category="error", source="pytest", message="test_foo failed"),
            ValidationFinding(finding_id="f2", category="error", source="pytest", message="test_foo failed"),
            ValidationFinding(finding_id="f3", category="error", source="pytest", message="test_foo failed"),
        ]
        unique, suppressed = _deduplicate_findings(findings)
        assert len(unique) == 1
        assert suppressed == 2
        assert unique[0].duplicate_count == 2

    def test_keeps_different_findings(self):
        findings = [
            ValidationFinding(finding_id="f1", category="error", source="pytest", message="test_foo failed"),
            ValidationFinding(finding_id="f2", category="error", source="pyright", message="type error"),
            ValidationFinding(finding_id="f3", category="warning", source="ruff", message="style issue"),
        ]
        unique, suppressed = _deduplicate_findings(findings)
        assert len(unique) == 3
        assert suppressed == 0

    def test_empty_list(self):
        unique, suppressed = _deduplicate_findings([])
        assert len(unique) == 0
        assert suppressed == 0


# ============================================================================
# Pattern Detection Tests
# ============================================================================


class TestPatternDetection:
    """Tests for _detect_patterns."""

    def test_detects_repeated_errors(self):
        findings = [
            ValidationFinding(finding_id=f"f{i}", category="error", source="pytest", message="ImportError: no module")
            for i in range(4)
        ]
        pattern_findings, count = _detect_patterns(findings, _make_id_gen())
        assert count >= 1
        assert any("Repeated error pattern" in f.message for f in pattern_findings)

    def test_detects_file_with_many_errors(self):
        findings = [
            ValidationFinding(
                finding_id=f"f{i}", category="error", source="pyright",
                message=f"error {i}", file_path="app/main.py"
            )
            for i in range(4)
        ]
        pattern_findings, count = _detect_patterns(findings, _make_id_gen())
        assert count >= 1
        assert any("app/main.py" in f.message for f in pattern_findings)

    def test_detects_multi_tool_file(self):
        findings = [
            ValidationFinding(finding_id="f1", category="error", source="pytest", message="fail", file_path="broke.py"),
            ValidationFinding(finding_id="f2", category="warning", source="ruff", message="lint", file_path="broke.py"),
        ]
        pattern_findings, count = _detect_patterns(findings, _make_id_gen())
        assert count == 1
        assert any("Multiple tools" in f.message for f in pattern_findings)

    def test_no_patterns(self):
        findings = [
            ValidationFinding(finding_id="f1", category="error", source="pytest", message="unique error 1"),
            ValidationFinding(finding_id="f2", category="warning", source="ruff", message="unique warning 1"),
        ]
        pattern_findings, count = _detect_patterns(findings, _make_id_gen())
        assert count == 0
        assert len(pattern_findings) == 0


# ============================================================================
# Prioritization Tests
# ============================================================================


class TestPrioritization:
    """Tests for _prioritize_findings."""

    def test_sorts_by_severity_then_category(self):
        findings = [
            ValidationFinding(finding_id="f1", category="info", source="x", message="info", severity="low"),
            ValidationFinding(finding_id="f2", category="error", source="x", message="critical", severity="critical"),
            ValidationFinding(finding_id="f3", category="warning", source="x", message="medium", severity="medium"),
            ValidationFinding(finding_id="f4", category="error", source="x", message="high", severity="high"),
        ]
        sorted_findings = _prioritize_findings(findings)
        assert sorted_findings[0].severity == "critical"
        assert sorted_findings[1].severity == "high"
        assert sorted_findings[2].severity == "medium"
        assert sorted_findings[3].severity == "low"


# ============================================================================
# Execution Result Analysis Tests
# ============================================================================


class TestAnalyzeExecutionResult:
    """Tests for _analyze_execution_result."""

    def test_failed_execution(self):
        config = _make_config()
        data = {"status": "failed", "all_errors": ["ImportError: no module"], "smoke_test_passed": False}
        findings, smoke_passed = _analyze_execution_result(data, "item_1", _make_id_gen(), config)
        assert not smoke_passed
        error_findings = [f for f in findings if f.category == "error"]
        assert len(error_findings) >= 2  # status + error + smoke

    def test_partial_execution(self):
        config = _make_config()
        data = {"status": "partial", "all_errors": [], "smoke_test_passed": True}
        findings, smoke_passed = _analyze_execution_result(data, "item_1", _make_id_gen(), config)
        assert smoke_passed
        assert any(f.source == "execution_status" for f in findings)

    def test_slow_execution(self):
        config = _make_config(execution_warning_ms=30000)
        data = {"status": "success", "total_duration_ms": 50000}
        findings, _ = _analyze_execution_result(data, "item_1", _make_id_gen(), config)
        assert any(f.source == "timing" for f in findings)

    def test_error_with_traceback(self):
        config = _make_config()
        error = '''Traceback (most recent call last):
  File "app/main.py", line 10, in run
    import broken_module
ModuleNotFoundError: No module named 'broken_module\''''
        data = {"status": "failed", "all_errors": [error]}
        findings, _ = _analyze_execution_result(data, "item_1", _make_id_gen(), config)
        error_findings = [f for f in findings if f.source == "execution_error"]
        assert len(error_findings) == 1
        assert error_findings[0].file_path == "app/main.py"
        assert error_findings[0].line_number == 10
        assert error_findings[0].stack_trace is not None


class TestAnalyzeBuildOutput:
    """Tests for _analyze_build_output."""

    def test_build_error(self):
        output = {"error": "Build failed: syntax error in app/main.py:5"}
        findings = _analyze_build_output(output, "item_1", _make_id_gen())
        assert len(findings) == 1
        assert findings[0].category == "error"
        assert findings[0].file_path == "app/main.py"

    def test_errors_list(self):
        output = {"errors": ["error 1", "error 2"]}
        findings = _analyze_build_output(output, "item_1", _make_id_gen())
        assert len(findings) == 2

    def test_tool_results(self):
        output = {"tool_results": [{"success": False, "tool": "pip", "error": "install failed"}]}
        findings = _analyze_build_output(output, "item_1", _make_id_gen())
        assert len(findings) == 1
        assert findings[0].source == "tool_pip"

    def test_non_dict_output(self):
        findings = _analyze_build_output("not a dict", "item_1", _make_id_gen())
        assert len(findings) == 0


# ============================================================================
# Get Project Directory Tests
# ============================================================================


class TestGetProjectDirectory:
    """Tests for _get_project_directory."""

    def test_from_build_settings(self):
        state = {"build_settings": {"project_directory": "app/agents/weather"}}
        result = _get_project_directory({}, state)
        assert result == "app/agents/weather"

    def test_from_work_item_context(self):
        item = {"context": {"project_directory": "app/mcp/tools"}}
        state = {}
        result = _get_project_directory(item, state)
        assert result == "app/mcp/tools"

    def test_no_directory_found(self):
        result = _get_project_directory({}, {})
        assert result is None


# ============================================================================
# ValidationConfig Tests
# ============================================================================


class TestValidationConfig:
    """Tests for ValidationConfig from .env."""

    def test_defaults(self):
        config = ValidationConfig()
        assert config.execution_warning_ms == 60000
        assert config.response_warning_ms == 10000
        assert config.enable_llm_severity is True
        assert config.enable_python_tool_actions is True
        assert config.pytest_timeout_seconds == 120

    def test_from_env(self):
        env = {
            "PROGENITOR_VALIDATION_EXECUTION_WARNING_MS": "30000",
            "PROGENITOR_VALIDATION_RESPONSE_WARNING_MS": "5000",
            "PROGENITOR_VALIDATION_LLM_SEVERITY": "false",
            "PROGENITOR_VALIDATION_PYTHON_TOOLS": "false",
            "PROGENITOR_VALIDATION_PYTEST_TIMEOUT": "60",
        }
        with patch.dict(os.environ, env, clear=False):
            config = ValidationConfig.from_env()
            assert config.execution_warning_ms == 30000
            assert config.response_warning_ms == 5000
            assert config.enable_llm_severity is False
            assert config.enable_python_tool_actions is False
            assert config.pytest_timeout_seconds == 60


# ============================================================================
# Response Model Tests
# ============================================================================


class TestValidationSeverityResponse:
    """Tests for the LLM severity response model."""

    def test_basic_validation(self):
        data = {
            "assessments": [
                {
                    "finding_id": "f1",
                    "severity": "critical",
                    "reasoning": "Smoke test failure",
                    "suggested_fix": "Fix the import",
                    "is_root_cause": True,
                }
            ],
            "overall_assessment": "Build is broken",
            "blocking_finding_ids": ["f1"],
        }
        resp = ValidationSeverityResponse.model_validate(data)
        assert len(resp.assessments) == 1
        assert resp.assessments[0].severity == "critical"
        assert resp.assessments[0].is_root_cause is True

    def test_severity_normalization(self):
        spec = FindingSeveritySpec(finding_id="f1", severity="HIGH")
        assert spec.severity == "high"

    def test_invalid_severity_defaults(self):
        spec = FindingSeveritySpec(finding_id="f1", severity="apocalyptic")
        assert spec.severity == "medium"


# ============================================================================
# Full Node Integration Tests
# ============================================================================


class TestValidateNodeIntegration:
    """Integration tests for the full validate node."""

    async def test_empty_state(self):
        """No current_work_item returns empty dict."""
        config = _make_config(enable_python_tool_actions=False, enable_llm_severity=False)
        node = create_node(config)
        result = await node({})
        assert result == {}

    async def test_basic_validation(self):
        """Basic validation with execution result."""
        config = _make_config(
            enable_python_tool_actions=False,
            enable_llm_severity=False,
            enable_historical_comparison=False,
        )
        node = create_node(config)

        state = {
            "current_work_item": {"id": "work_1"},
            "iteration": {"current": 1},
            "execution_result": {
                "status": "failed",
                "all_errors": ["ImportError: no module named foo"],
                "smoke_test_passed": False,
            },
        }

        result = await node(state)
        assert "validation_result_v2" in result
        vr = result["validation_result_v2"]
        assert vr["error_count"] >= 2  # status + error + smoke
        assert vr["has_blocking_issues"] is True
        stats = vr["statistics"]
        assert stats is not None
        assert stats["smoke_test_passed"] is False

    async def test_with_deduplication(self):
        """Deduplication removes exact duplicates."""
        config = _make_config(
            enable_python_tool_actions=False,
            enable_llm_severity=False,
            enable_historical_comparison=False,
        )
        node = create_node(config)

        # Create execution result with duplicate errors
        state = {
            "current_work_item": {"id": "work_2"},
            "iteration": {"current": 1},
            "execution_result": {
                "status": "failed",
                "all_errors": ["ImportError: no module named foo"] * 5,
            },
        }

        result = await node(state)
        vr = result["validation_result_v2"]
        # Should have fewer findings than 5 duplicate errors due to dedup
        error_findings = [f for f in vr["findings"] if f["source"] == "execution_error"]
        assert len(error_findings) < 5

    async def test_with_state_manager(self):
        """Validate persists to build_state.json via state_manager."""
        config = _make_config(
            enable_python_tool_actions=False,
            enable_llm_severity=False,
            enable_historical_comparison=True,
        )
        mock_sm = AsyncMock()
        mock_sm.get_build_state.return_value = {
            "history": {
                "verification_reports": [
                    {"error_count": 5}
                ]
            }
        }
        mock_sm.save_build_state = AsyncMock()

        node = create_node(config, state_manager=mock_sm)

        state = {
            "current_work_item": {"id": "work_3"},
            "iteration": {"current": 2},
            "execution_result": {
                "status": "success",
                "all_errors": [],
                "smoke_test_passed": True,
            },
        }

        result = await node(state)
        vr = result["validation_result_v2"]

        # Should show improving trend (0 errors vs 5 previous)
        stats = vr["statistics"]
        assert stats is not None
        assert stats["trend"] == "improving"
        assert stats["previous_error_count"] == 5

        # Should have called save
        mock_sm.save_build_state.assert_called_once()

    async def test_prioritization_order(self):
        """Findings are returned in severity order."""
        config = _make_config(
            enable_python_tool_actions=False,
            enable_llm_severity=False,
            enable_historical_comparison=False,
            enable_deduplication=False,
            enable_pattern_detection=False,
        )
        node = create_node(config)

        state = {
            "current_work_item": {"id": "work_4"},
            "iteration": {"current": 1},
            "execution_result": {
                "status": "failed",
                "all_errors": ["Error one"],
                "smoke_test_passed": False,
                "total_duration_ms": 100000,
            },
        }

        result = await node(state)
        vr = result["validation_result_v2"]
        findings = vr["findings"]

        # First finding should be critical (smoke test), then high, etc.
        severities = [f["severity"] for f in findings]
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        sorted_severities = sorted(severities, key=lambda s: severity_order.get(s, 4))
        assert severities == sorted_severities
