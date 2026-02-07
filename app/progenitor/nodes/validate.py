"""
Validate Node - Comprehensive Python validation with LLM severity assessment.

Analyzes execution results, invokes Python validation tools (pytest, pyright,
mypy, ruff), deduplicates findings, detects patterns, compares with history,
and uses LLM structured output for severity assessment.

All external tool invocations are recorded as ValidationAction for auditability.
"""

import asyncio
import json
import logging
import re
import time
from collections import Counter
from typing import Any, Callable, Coroutine, Optional, TYPE_CHECKING, Mapping

from ..state.models import ProgenitorState
from ..state.execution_schemas import (
    ExecutionResult,
    ExecutionStatus,
    ValidationResultV2,
    ValidationFinding,
    ValidationAction,
)
from ..runtime.time_utils import utcnow

if TYPE_CHECKING:
    from ..config import ProgenitorConfig
    from ..llm.client import LLMClient
    from ..state.manager import StateManager

logger = logging.getLogger(__name__)


# =============================================================================
# File/line extraction patterns for Python tools
# =============================================================================

# pytest: "path/to/file.py:123: AssertionError"
_PYTEST_LOCATION_RE = re.compile(r"([^\s:]+\.py):(\d+):")

# pyright output format: "  path/to/file.py:12:5 - error: ..."
_PYRIGHT_LOCATION_RE = re.compile(r"^\s*(.+\.py):(\d+):\d+\s*-\s*(error|warning|information):", re.MULTILINE)

# mypy: "path/to/file.py:12: error: ..."
_MYPY_LOCATION_RE = re.compile(r"^(.+\.py):(\d+):\s*(error|warning|note):", re.MULTILINE)

# ruff: "path/to/file.py:12:5: E501 ..."
_RUFF_LOCATION_RE = re.compile(r"^(.+\.py):(\d+):\d+:\s+([A-Z]\d+)", re.MULTILINE)

# Python traceback: "  File "path/to/file.py", line 123, in func"
_TRACEBACK_FILE_RE = re.compile(r'File "([^"]+\.py)", line (\d+)')

# Generic Python error with file reference
_PYTHON_ERROR_RE = re.compile(r"([^\s:]+\.py):(\d+)")


def _extract_file_location(text: str) -> tuple[Optional[str], Optional[int]]:
    """Extract file path and line number from Python error text."""
    for pattern in [_PYTEST_LOCATION_RE, _MYPY_LOCATION_RE, _PYRIGHT_LOCATION_RE, _TRACEBACK_FILE_RE, _PYTHON_ERROR_RE]:
        match = pattern.search(text)
        if match:
            try:
                return match.group(1), int(match.group(2))
            except (IndexError, ValueError):
                continue
    return None, None


def _parse_python_traceback(text: str) -> Optional[list[str]]:
    """Parse a Python traceback into structured frames."""
    if "Traceback (most recent call last):" not in text:
        return None

    frames = []
    for match in _TRACEBACK_FILE_RE.finditer(text):
        file_path = match.group(1)
        line_num = match.group(2)
        frames.append(f"{file_path}:{line_num}")

    return frames if frames else None


# =============================================================================
# Python build output parsers
# =============================================================================

def _parse_pytest_output(output: str, make_id) -> list[ValidationFinding]:
    """Parse pytest output into structured findings."""
    findings = []

    # Match FAILED lines: "FAILED tests/test_foo.py::test_bar - AssertionError: ..."
    failed_re = re.compile(r"FAILED\s+([^\s]+)\s*-?\s*(.*)", re.MULTILINE)
    for match in failed_re.finditer(output):
        test_path = match.group(1)
        error_msg = match.group(2).strip() or "Test failed"
        file_path, line_number = _extract_file_location(test_path)
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="error",
            source="pytest",
            message=f"Test failed: {test_path}: {error_msg[:300]}",
            severity="high",
            file_path=file_path,
            line_number=line_number,
            evidence=error_msg,
        ))

    # Match ERROR lines: "ERROR tests/test_foo.py - ..."
    error_re = re.compile(r"^ERROR\s+([^\s]+)\s*-?\s*(.*)", re.MULTILINE)
    for match in error_re.finditer(output):
        test_path = match.group(1)
        error_msg = match.group(2).strip() or "Collection/setup error"
        file_path, line_number = _extract_file_location(test_path)
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="error",
            source="pytest",
            message=f"Test error: {test_path}: {error_msg[:300]}",
            severity="high",
            file_path=file_path,
            line_number=line_number,
            evidence=error_msg,
            stack_trace=_parse_python_traceback(output),
        ))

    # Match summary line: "X failed, Y passed, Z errors"
    summary_re = re.compile(r"=+\s*(.+?)\s*=+\s*$", re.MULTILINE)
    summary_match = summary_re.search(output)
    if summary_match and not findings:
        summary = summary_match.group(1)
        if "failed" in summary.lower() or "error" in summary.lower():
            findings.append(ValidationFinding(
                finding_id=make_id(),
                category="error",
                source="pytest",
                message=f"pytest summary: {summary}",
                severity="high",
            ))

    # Check for no tests collected
    if "no tests ran" in output.lower() or "collected 0 items" in output.lower():
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="warning",
            source="pytest",
            message="No tests were collected or ran",
            severity="medium",
        ))

    return findings


def _parse_pyright_output(output: str, make_id) -> list[ValidationFinding]:
    """Parse pyright output into structured findings."""
    findings = []

    for match in _PYRIGHT_LOCATION_RE.finditer(output):
        file_path = match.group(1)
        line_num = int(match.group(2))
        level = match.group(3)

        # Extract the rest of the line as the message
        line_start = match.end()
        line_end = output.find("\n", line_start)
        message = output[line_start:line_end].strip() if line_end != -1 else output[line_start:].strip()

        category = "error" if level == "error" else "warning" if level == "warning" else "info"
        severity = "high" if level == "error" else "medium" if level == "warning" else "low"

        findings.append(ValidationFinding(
            finding_id=make_id(),
            category=category,
            source="pyright",
            message=f"pyright {level}: {message[:300]}",
            severity=severity,
            file_path=file_path,
            line_number=line_num,
        ))

    # Summary line: "X errors, Y warnings, Z informations"
    summary_re = re.compile(r"(\d+)\s+error", re.IGNORECASE)
    summary_match = summary_re.search(output)
    if summary_match and not findings:
        error_count = int(summary_match.group(1))
        if error_count > 0:
            findings.append(ValidationFinding(
                finding_id=make_id(),
                category="error",
                source="pyright",
                message=f"pyright found {error_count} error(s)",
                severity="high",
            ))

    return findings


def _parse_mypy_output(output: str, make_id) -> list[ValidationFinding]:
    """Parse mypy output into structured findings."""
    findings = []

    for match in _MYPY_LOCATION_RE.finditer(output):
        file_path = match.group(1)
        line_num = int(match.group(2))
        level = match.group(3)

        line_start = match.end()
        line_end = output.find("\n", line_start)
        message = output[line_start:line_end].strip() if line_end != -1 else output[line_start:].strip()

        category = "error" if level == "error" else "warning" if level == "warning" else "info"
        severity = "high" if level == "error" else "medium" if level == "warning" else "low"

        findings.append(ValidationFinding(
            finding_id=make_id(),
            category=category,
            source="mypy",
            message=f"mypy {level}: {message[:300]}",
            severity=severity,
            file_path=file_path,
            line_number=line_num,
        ))

    return findings


def _parse_ruff_output(output: str, make_id) -> list[ValidationFinding]:
    """Parse ruff output into structured findings."""
    findings = []

    for match in _RUFF_LOCATION_RE.finditer(output):
        file_path = match.group(1)
        line_num = int(match.group(2))
        rule_code = match.group(3)

        line_start = match.end()
        line_end = output.find("\n", line_start)
        message = output[line_start:line_end].strip() if line_end != -1 else output[line_start:].strip()

        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="warning",
            source="ruff",
            message=f"ruff {rule_code}: {message[:300]}",
            severity="low",
            file_path=file_path,
            line_number=line_num,
        ))

    return findings


# =============================================================================
# Deduplication
# =============================================================================

def _deduplicate_findings(findings: list[ValidationFinding]) -> tuple[list[ValidationFinding], int]:
    """Remove duplicate findings. Returns (unique_findings, duplicates_suppressed)."""
    seen: dict[tuple, str] = {}  # key -> first finding_id
    unique = []
    suppressed = 0

    for finding in findings:
        key = (finding.category, finding.source, finding.message[:100])
        if key not in seen:
            seen[key] = finding.finding_id
            unique.append(finding)
        else:
            suppressed += 1
            # Increment duplicate count on the original
            for f in unique:
                if f.finding_id == seen[key]:
                    f.duplicate_count += 1
                    break

    return unique, suppressed


# =============================================================================
# Pattern detection
# =============================================================================

def _detect_patterns(findings: list[ValidationFinding], make_id) -> tuple[list[ValidationFinding], int]:
    """Detect meta-patterns across findings. Returns (pattern_findings, patterns_detected)."""
    pattern_findings = []
    patterns_detected = 0

    # Repeated error messages
    error_msgs = [f.message[:80] for f in findings if f.category == "error"]
    msg_counts = Counter(error_msgs)
    for msg, count in msg_counts.items():
        if count >= 3:
            patterns_detected += 1
            pattern_findings.append(ValidationFinding(
                finding_id=make_id(),
                category="warning",
                source="pattern_detection",
                message=f"Repeated error pattern ({count}x): {msg}",
                severity="medium",
                is_root_cause=True,
            ))

    # Same file appears in multiple errors
    error_files = [f.file_path for f in findings if f.file_path and f.category == "error"]
    file_counts = Counter(error_files)
    for file_path, count in file_counts.items():
        if count >= 3:
            patterns_detected += 1
            pattern_findings.append(ValidationFinding(
                finding_id=make_id(),
                category="warning",
                source="pattern_detection",
                message=f"File has {count} errors: {file_path}",
                severity="medium",
                file_path=file_path,
            ))

    # Multiple tools flagging same file
    tool_file_map: dict[str, set[str]] = {}
    for f in findings:
        if f.file_path and f.source in ("pytest", "pyright", "mypy", "ruff"):
            tool_file_map.setdefault(f.file_path, set()).add(f.source)
    for file_path, tools in tool_file_map.items():
        if len(tools) >= 2:
            patterns_detected += 1
            pattern_findings.append(ValidationFinding(
                finding_id=make_id(),
                category="info",
                source="pattern_detection",
                message=f"Multiple tools flagged {file_path}: {', '.join(sorted(tools))}",
                severity="medium",
                file_path=file_path,
            ))

    return pattern_findings, patterns_detected


# =============================================================================
# Finding prioritization
# =============================================================================

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_CATEGORY_ORDER = {"error": 0, "warning": 1, "suggestion": 2, "info": 3}


def _prioritize_findings(findings: list[ValidationFinding]) -> list[ValidationFinding]:
    """Sort findings by severity and category."""
    return sorted(findings, key=lambda f: (
        _SEVERITY_ORDER.get(f.severity, 4),
        _CATEGORY_ORDER.get(f.category, 4),
        f.source,
    ))


# =============================================================================
# Python tool runner
# =============================================================================

async def _run_python_tool(
    tool: str,
    project_directory: str,
    timeout_seconds: int,
    make_id,
) -> tuple[ValidationAction, list[ValidationFinding]]:
    """Run a Python validation tool and return action record + findings."""
    commands = {
        "pytest": ["python", "-m", "pytest", "--tb=short", "-q", project_directory],
        "pyright": ["python", "-m", "pyright", project_directory],
        "mypy": ["python", "-m", "mypy", project_directory, "--no-error-summary"],
        "ruff": ["python", "-m", "ruff", "check", project_directory],
    }

    parsers = {
        "pytest": _parse_pytest_output,
        "pyright": _parse_pyright_output,
        "mypy": _parse_mypy_output,
        "ruff": _parse_ruff_output,
    }

    cmd = commands.get(tool)
    parser = parsers.get(tool)
    if not cmd or not parser:
        logger.warning(f"Unknown Python tool: {tool}")
        return ValidationAction(
            action_id=make_id(),
            tool=tool,
            command=f"unknown: {tool}",
            project_directory=project_directory,
            success=False,
        ), []

    command_str = " ".join(cmd)
    start_time = time.perf_counter()
    action = ValidationAction(
        action_id=make_id(),
        tool=tool,
        command=command_str,
        project_directory=project_directory,
        started_at=utcnow(),
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_directory,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        stdout = stdout_bytes.decode("utf-8", errors="replace")[:50000]
        stderr = stderr_bytes.decode("utf-8", errors="replace")[:10000]

        action.exit_code = proc.returncode
        action.stdout = stdout
        action.stderr = stderr
        action.duration_ms = elapsed_ms
        action.completed_at = utcnow()
        action.success = proc.returncode == 0

        # Parse output into findings
        combined = stdout + "\n" + stderr
        findings = parser(combined, make_id)

        action.error_count = sum(1 for f in findings if f.category == "error")
        action.warning_count = sum(1 for f in findings if f.category == "warning")

        return action, findings

    except asyncio.TimeoutError:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        action.duration_ms = elapsed_ms
        action.completed_at = utcnow()
        action.success = False
        action.stderr = f"{tool} timed out after {timeout_seconds}s"

        finding = ValidationFinding(
            finding_id=make_id(),
            category="warning",
            source=tool,
            message=f"{tool} timed out after {timeout_seconds}s",
            severity="medium",
        )
        return action, [finding]

    except FileNotFoundError:
        action.completed_at = utcnow()
        action.success = False
        action.stderr = f"{tool} not found (not installed)"

        finding = ValidationFinding(
            finding_id=make_id(),
            category="info",
            source=tool,
            message=f"{tool} not available (not installed)",
            severity="low",
        )
        return action, [finding]

    except Exception as e:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        action.duration_ms = elapsed_ms
        action.completed_at = utcnow()
        action.success = False
        action.stderr = str(e)[:1000]

        finding = ValidationFinding(
            finding_id=make_id(),
            category="warning",
            source=tool,
            message=f"{tool} execution error: {str(e)[:200]}",
            severity="medium",
        )
        return action, [finding]


# =============================================================================
# LLM severity assessment
# =============================================================================

async def _assess_severity_with_llm(
    findings: list[ValidationFinding],
    llm_client: "LLMClient",
    project_directory: str,
    iteration: int,
) -> list[ValidationFinding]:
    """Use LLM structured output to assess finding severities."""
    if not findings:
        return findings

    from ..llm.prompts import PromptTemplates
    from ..llm.response_models import ValidationSeverityResponse

    prompts = PromptTemplates()

    # Build findings summary for LLM
    findings_data = [
        {
            "finding_id": f.finding_id,
            "category": f.category,
            "source": f.source,
            "message": f.message[:500],
            "file_path": f.file_path,
            "line_number": f.line_number,
            "current_severity": f.severity,
        }
        for f in findings[:30]  # Limit to 30 findings for LLM context
    ]

    system_prompt = prompts.get_validation_severity_prompt(
        findings_json=json.dumps(findings_data, indent=2),
        project_directory=project_directory,
        iteration=iteration,
    )

    response_format = llm_client._get_json_response_format(
        ValidationSeverityResponse, "validation_severity"
    )
    if response_format.get("type") == "json_object":
        system_prompt += "\n\nReturn a single JSON object and nothing else (valid json)."

    try:
        response = await llm_client.chat(
            messages=[{"role": "user", "content": "Assess the severity of these validation findings."}],
            system_prompt=system_prompt,
            temperature=0.2,
            response_format=response_format,
        )

        raw_data = json.loads(response)
        validated = ValidationSeverityResponse.model_validate(raw_data)

        # Apply LLM assessments back to findings
        assessment_map = {a.finding_id: a for a in validated.assessments}
        for finding in findings:
            assessment = assessment_map.get(finding.finding_id)
            if assessment:
                finding.severity = assessment.severity
                finding.suggested_fix = assessment.suggested_fix or finding.suggested_fix
                finding.is_root_cause = assessment.is_root_cause

        logger.info(f"LLM severity assessment applied to {len(validated.assessments)} findings")
        return findings

    except Exception as e:
        logger.warning(f"LLM severity assessment failed (using heuristic fallback): {e}")
        return findings


# =============================================================================
# Historical comparison
# =============================================================================

async def _get_previous_validation(state_manager: "StateManager") -> Optional[dict[str, Any]]:
    """Get the previous validation result from build_state.json history."""
    try:
        build_state = await state_manager.get_build_state()
        history = build_state.get("history", {})
        verification_reports = history.get("verification_reports", [])
        if verification_reports:
            return verification_reports[-1]
    except Exception as e:
        logger.debug(f"Could not load previous validation: {e}")
    return None


async def _save_validation_to_history(
    state_manager: "StateManager",
    validation_result: dict[str, Any],
) -> None:
    """Persist validation result to build_state.json history."""
    try:
        build_state = await state_manager.get_build_state()
        history = build_state.setdefault("history", {})
        reports = history.setdefault("verification_reports", [])

        # Keep last 10 validation results
        reports.append(validation_result)
        if len(reports) > 10:
            reports[:] = reports[-10:]

        await state_manager.save_build_state(build_state)
        logger.debug("Saved validation result to build_state.json history")
    except Exception as e:
        logger.warning(f"Failed to save validation to history: {e}")


# =============================================================================
# Execution result analysis (preserved + enhanced)
# =============================================================================

def _analyze_execution_result(
    execution_result_data: dict[str, Any],
    item_id: str,
    make_id,
    config: "ProgenitorConfig",
) -> tuple[list[ValidationFinding], bool]:
    """Analyze ExecutionResult data and generate findings.

    Returns (findings, smoke_test_passed).
    """
    findings = []
    smoke_test_passed = True

    # Check overall status
    status = execution_result_data.get("status", "unknown")
    if status == ExecutionStatus.FAILED.value or status == "failed":
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="error",
            source="execution_status",
            message="Execution failed",
            severity="high",
        ))
    elif status == ExecutionStatus.PARTIAL.value or status == "partial":
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="warning",
            source="execution_status",
            message="Execution partially succeeded",
            severity="medium",
        ))

    # Check for errors in execution (full text preserved)
    all_errors = execution_result_data.get("all_errors", [])
    for error in all_errors[:config.validation.max_errors_to_show]:
        error_str = str(error)
        file_path, line_number = _extract_file_location(error_str)
        stack_trace = _parse_python_traceback(error_str)
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="error",
            source="execution_error",
            message=error_str[:500],
            severity="high",
            evidence=error_str,  # Full text
            file_path=file_path,
            line_number=line_number,
            stack_trace=stack_trace,
        ))

    # Check smoke test
    smoke_passed = execution_result_data.get("smoke_test_passed", True)
    if not smoke_passed:
        smoke_test_passed = False
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="error",
            source="smoke_test",
            message="Smoke test failed - agent may not be functional",
            severity="critical",
        ))

    # Analyze runs
    runs = execution_result_data.get("runs", [])
    for run_data in runs:
        run_findings = _analyze_execution_run(run_data, make_id, config)
        findings.extend(run_findings)

    # Check timing
    total_duration = execution_result_data.get("total_duration_ms")
    if total_duration and total_duration > config.validation.execution_warning_ms:
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="warning",
            source="timing",
            message=f"Execution took {total_duration/1000:.1f}s - may indicate performance issues",
            severity="medium",
        ))

    return findings, smoke_test_passed


def _analyze_execution_run(
    run_data: dict[str, Any],
    make_id,
    config: "ProgenitorConfig",
) -> list[ValidationFinding]:
    """Analyze a single execution run."""
    findings = []

    prompt_results = run_data.get("prompt_results", [])
    failed_prompts = [p for p in prompt_results if not p.get("success", False)]

    if failed_prompts:
        for prompt in failed_prompts[:config.validation.max_failed_prompts_to_show]:
            error = prompt.get("error", "Unknown error")
            error_type = prompt.get("error_type", "unknown")
            file_path, line_number = _extract_file_location(str(error))
            stack_trace = _parse_python_traceback(str(error))
            findings.append(ValidationFinding(
                finding_id=make_id(),
                category="error" if not prompt.get("is_optional") else "warning",
                source=f"prompt_{prompt.get('prompt_id', 'unknown')}",
                message=f"Prompt failed ({error_type}): {str(error)[:300]}",
                severity="high" if prompt.get("is_smoke_test") else "medium",
                evidence=str(error),
                file_path=file_path,
                line_number=line_number,
                stack_trace=stack_trace,
            ))

    # Check for slow responses
    for prompt in prompt_results:
        duration = prompt.get("duration_ms")
        if duration and duration > config.validation.response_warning_ms:
            findings.append(ValidationFinding(
                finding_id=make_id(),
                category="info",
                source=f"prompt_{prompt.get('prompt_id', 'unknown')}",
                message=f"Slow response: {duration/1000:.1f}s",
                severity="low",
            ))

    return findings


def _analyze_build_output(
    build_output: Any,
    item_id: str,
    make_id,
) -> list[ValidationFinding]:
    """Analyze build output for issues."""
    findings = []

    if not isinstance(build_output, dict):
        return findings

    # Check for explicit errors in build output
    if build_output.get("error"):
        error_str = str(build_output["error"])
        file_path, line_number = _extract_file_location(error_str)
        stack_trace = _parse_python_traceback(error_str)
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="error",
            source="build_output",
            message=f"Build error: {error_str[:500]}",
            severity="high",
            evidence=error_str,
            file_path=file_path,
            line_number=line_number,
            stack_trace=stack_trace,
        ))

    # Check for errors list
    errors = build_output.get("errors", [])
    for error in errors[:5]:
        error_str = str(error)
        file_path, line_number = _extract_file_location(error_str)
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="error",
            source="build_errors",
            message=error_str[:500],
            severity="high",
            evidence=error_str,
            file_path=file_path,
            line_number=line_number,
        ))

    # Check tool results
    tool_results = build_output.get("tool_results", [])
    for tool_result in tool_results:
        if not tool_result.get("success", True):
            error_str = str(tool_result.get("error", "Unknown error"))
            file_path, line_number = _extract_file_location(error_str)
            findings.append(ValidationFinding(
                finding_id=make_id(),
                category="warning",
                source=f"tool_{tool_result.get('tool', 'unknown')}",
                message=f"Tool failed: {error_str[:300]}",
                severity="medium",
                evidence=error_str,
                file_path=file_path,
                line_number=line_number,
            ))

    return findings


# =============================================================================
# Node factory
# =============================================================================

def create_node(
    config: "ProgenitorConfig",
    llm_client: Any = None,  # Accept protocol type
    state_manager: Any = None,  # Accept protocol type
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the validate node function.

    Args:
        config: ProgenitorConfig with validation settings.
        llm_client: LLM client for LLM-based severity assessment (accepts protocol).
        state_manager: State manager for historical comparison and persistence (accepts protocol).
    """

    async def validate_node(state: ProgenitorState) -> ProgenitorState:
        """Comprehensive Python validation with LLM severity assessment."""
        logger.info("VALIDATE: Starting validate phase")
        current_item = state.get("current_work_item")
        iteration = state.get("iteration", {}).get("current", 0)
        execution_result_data = state.get("execution_result")

        if not current_item:
            return {}

        # Get item info
        if isinstance(current_item, dict):
            item_id = current_item.get("id", "unknown")
        else:
            item_id = getattr(current_item, "id", "unknown")

        # Finding ID generator
        finding_counter = 0

        def make_id() -> str:
            nonlocal finding_counter
            finding_counter += 1
            return f"val_{item_id}_{finding_counter}"

        # Create validation result
        validation_result = ValidationResultV2(
            iteration=iteration or 0,
            validated_at=utcnow(),
            execution_result_ref=item_id,
            findings=[],
            validation_actions=[],
        )

        smoke_test_passed = True

        # === Phase 1: Analyze ExecutionResult ===
        if execution_result_data:
            exec_findings, smoke_passed = _analyze_execution_result(
                execution_result_data, item_id, make_id, config
            )
            validation_result.findings.extend(exec_findings)
            smoke_test_passed = smoke_passed

        # === Phase 2: Analyze build output ===
        last_output = state.get("last_iteration_output")
        if last_output:
            build_findings = _analyze_build_output(last_output, item_id, make_id)
            validation_result.findings.extend(build_findings)

        # === Phase 3: Run Python validation tools ===
        if config.validation.enable_python_tool_actions:
            # Determine project directory from build settings or work item
            project_dir = _get_project_directory(current_item, state)
            if project_dir:
                tool_configs = [
                    ("pytest", config.validation.pytest_timeout_seconds),
                    ("pyright", config.validation.pyright_timeout_seconds),
                    ("ruff", config.validation.ruff_timeout_seconds),
                ]

                for tool_name, timeout in tool_configs:
                    try:
                        action, tool_findings = await _run_python_tool(
                            tool_name, project_dir, timeout, make_id,
                        )
                        validation_result.validation_actions.append(action)
                        validation_result.findings.extend(tool_findings)
                    except Exception as e:
                        logger.warning(f"Failed to run {tool_name}: {e}")
            else:
                logger.debug("No project directory found, skipping Python tool actions")

        # === Phase 4: Deduplication ===
        duplicates_suppressed = 0
        if config.validation.enable_deduplication:
            validation_result.findings, duplicates_suppressed = _deduplicate_findings(
                validation_result.findings
            )

        # === Phase 5: Pattern detection ===
        patterns_detected = 0
        if config.validation.enable_pattern_detection:
            pattern_findings, patterns_detected = _detect_patterns(
                validation_result.findings, make_id
            )
            validation_result.findings.extend(pattern_findings)

        # === Phase 6: LLM severity assessment ===
        if config.validation.enable_llm_severity and llm_client and validation_result.findings:
            project_dir = _get_project_directory(current_item, state) or "unknown"
            validation_result.findings = await _assess_severity_with_llm(
                validation_result.findings,
                llm_client,
                project_dir,
                iteration or 0,
            )
            validation_result.llm_severity_assessed = True

        # === Phase 7: Prioritize findings ===
        validation_result.findings = _prioritize_findings(validation_result.findings)

        # === Phase 8: Compute counts and statistics ===
        validation_result.compute_counts()

        # Historical comparison
        previous_error_count = None
        if config.validation.enable_historical_comparison and state_manager:
            prev = await _get_previous_validation(state_manager)
            if prev:
                previous_error_count = prev.get("error_count")

        validation_result.compute_statistics(
            previous_error_count=previous_error_count,
            duplicates_suppressed=duplicates_suppressed,
            patterns_detected=patterns_detected,
            smoke_test_passed=smoke_test_passed,
        )

        # === Phase 9: Persist to history ===
        if state_manager:
            await _save_validation_to_history(
                state_manager,
                validation_result.model_dump(),
            )

        # Log results
        trend_str = ""
        if validation_result.statistics and validation_result.statistics.trend:
            trend_str = f", trend: {validation_result.statistics.trend}"

        logger.info(
            f"Validation complete: {validation_result.error_count} errors, "
            f"{validation_result.warning_count} warnings, "
            f"{validation_result.info_count} info, "
            f"{len(validation_result.validation_actions)} tool actions"
            f"{trend_str}"
        )

        return {
            "validation_result_v2": validation_result.model_dump(),
        }

    return validate_node


def _get_project_directory(current_item: Any, state: Mapping[str, Any]) -> Optional[str]:
    """Extract project directory from work item or build settings."""
    # Try build settings in state
    build_settings = state.get("build_settings")
    if isinstance(build_settings, dict):
        project_dir = build_settings.get("project_directory")
        if project_dir:
            return project_dir

    # Try work item context
    if isinstance(current_item, dict):
        context = current_item.get("context", {})
        if isinstance(context, dict):
            project_dir = context.get("project_directory")
            if project_dir:
                return project_dir

    return None
