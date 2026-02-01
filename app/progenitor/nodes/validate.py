"""
Validate Node - Data-only validation of execution results.

BL-005: VALIDATE is now data-only and non-mutating:
- Reads ExecutionResult
- Produces ValidationResultV2 that is purely findings
- Does NOT invoke external commands (no lint/tests, no code changes)
- If external commands are needed in future, records them explicitly
  as "validation actions" in the result

Previous behavior (running pytest, syntax checks) has been removed
to maintain purity of the validation phase.
"""

import logging
from datetime import datetime
from typing import Any, Callable, Coroutine, TYPE_CHECKING

from ..state.models import ProgenitorState, ValidationResult
from ..state.execution_schemas import (
    ExecutionResult,
    ExecutionStatus,
    ValidationResultV2,
    ValidationFinding,
)
from ..runtime.time_utils import utcnow

if TYPE_CHECKING:
    from ..config import ProgenitorConfig

logger = logging.getLogger(__name__)


def create_node(config: "ProgenitorConfig") -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the validate node function.

    Args:
        config: ProgenitorConfig with validation settings.
    """

    async def validate_node(state: ProgenitorState) -> ProgenitorState:
        """
        BL-005: Data-only validation of execution results.

        Reads ExecutionResult and produces ValidationResultV2 with findings.
        Does NOT invoke external commands or make code changes.
        """
        current_item = state.get("current_work_item")
        iteration = state.get("current_iteration", 0)
        execution_result_data = state.get("execution_result")

        if not current_item:
            return {}

        # Get item info
        if isinstance(current_item, dict):
            item_id = current_item.get("id", "unknown")
        else:
            item_id = getattr(current_item, "id", "unknown")

        # BL-005: Create data-only ValidationResultV2
        validation_result = ValidationResultV2(
            iteration=iteration,
            validated_at=utcnow(),
            execution_result_ref=item_id,
            findings=[],
            validation_actions=[],  # BL-005: Empty - we don't invoke external commands
        )

        # Analyze ExecutionResult if available
        if execution_result_data:
            findings = _analyze_execution_result(execution_result_data, item_id)
            validation_result.findings.extend(findings)

        # Analyze build output if available
        last_output = state.get("last_iteration_output")
        if last_output:
            findings = _analyze_build_output(last_output, item_id)
            validation_result.findings.extend(findings)

        # Compute counts and determine if there are blocking issues
        validation_result.compute_counts()

        # Log validation (but don't invoke external commands per BL-005)
        logger.info(
            f"Validation complete: {validation_result.error_count} errors, "
            f"{validation_result.warning_count} warnings, "
            f"{validation_result.info_count} info"
        )

        # Also produce legacy ValidationResult for backward compatibility
        legacy_result = ValidationResult(
            success=not validation_result.has_blocking_issues,
            work_item_id=item_id,
            checks_run=["data_analysis"],  # BL-005: Only data analysis, no external checks
            errors=[f.message for f in validation_result.findings if f.category == "error"],
            warnings=[f.message for f in validation_result.findings if f.category == "warning"],
        )

        return {
            "last_validation_result": legacy_result.model_dump(),
            "validation_result_v2": validation_result.model_dump(),  # BL-005: New structured result
        }

    return validate_node


def _analyze_execution_result(
    execution_result_data: dict[str, Any],
    item_id: str,
) -> list[ValidationFinding]:
    """
    Analyze ExecutionResult data and generate findings.

    BL-005: This is purely observational - no external commands.
    """
    findings = []
    finding_id = 0

    def make_id() -> str:
        nonlocal finding_id
        finding_id += 1
        return f"finding_{item_id}_{finding_id}"

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

    # Check for errors in execution
    all_errors = execution_result_data.get("all_errors", [])
    for error in all_errors[:10]:  # Limit to first 10
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="error",
            source="execution_error",
            message=str(error)[:500],
            severity="high",
            evidence=str(error),
        ))

    # Check smoke test
    smoke_passed = execution_result_data.get("smoke_test_passed", True)
    if not smoke_passed:
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="error",
            source="smoke_test",
            message="Smoke test failed - agent may not be functional",
            severity="critical",
        ))

    # Analyze runs if present
    runs = execution_result_data.get("runs", [])
    for run_data in runs:
        run_findings = _analyze_execution_run(run_data, make_id)
        findings.extend(run_findings)

    # Check timing
    total_duration = execution_result_data.get("total_duration_ms")
    if total_duration and total_duration > 60000:  # > 60 seconds
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="warning",
            source="timing",
            message=f"Execution took {total_duration/1000:.1f}s - may indicate performance issues",
            severity="medium",
        ))

    return findings


def _analyze_execution_run(
    run_data: dict[str, Any],
    make_id,
) -> list[ValidationFinding]:
    """Analyze a single execution run."""
    findings = []

    # Check prompt results
    prompt_results = run_data.get("prompt_results", [])
    failed_prompts = [p for p in prompt_results if not p.get("success", False)]

    if failed_prompts:
        for prompt in failed_prompts[:5]:  # Limit to first 5
            error = prompt.get("error", "Unknown error")
            error_type = prompt.get("error_type", "unknown")
            findings.append(ValidationFinding(
                finding_id=make_id(),
                category="error" if not prompt.get("is_optional") else "warning",
                source=f"prompt_{prompt.get('prompt_id', 'unknown')}",
                message=f"Prompt failed ({error_type}): {error[:200]}",
                severity="high" if prompt.get("is_smoke_test") else "medium",
                evidence=prompt.get("prompt_text", ""),
            ))

    # Check for slow responses
    for prompt in prompt_results:
        duration = prompt.get("duration_ms")
        if duration and duration > 10000:  # > 10 seconds
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
) -> list[ValidationFinding]:
    """
    Analyze build output for issues.

    BL-005: Purely observational analysis.
    """
    findings = []
    finding_id = 100  # Offset from execution findings

    def make_id() -> str:
        nonlocal finding_id
        finding_id += 1
        return f"finding_{item_id}_{finding_id}"

    if not isinstance(build_output, dict):
        return findings

    # Check for explicit errors in build output
    if build_output.get("error"):
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="error",
            source="build_output",
            message=f"Build error: {str(build_output['error'])[:500]}",
            severity="high",
        ))

    # Check for errors list
    errors = build_output.get("errors", [])
    for error in errors[:5]:
        findings.append(ValidationFinding(
            finding_id=make_id(),
            category="error",
            source="build_errors",
            message=str(error)[:500],
            severity="high",
        ))

    # Check tool results
    tool_results = build_output.get("tool_results", [])
    for tool_result in tool_results:
        if not tool_result.get("success", True):
            findings.append(ValidationFinding(
                finding_id=make_id(),
                category="warning",
                source=f"tool_{tool_result.get('tool', 'unknown')}",
                message=f"Tool failed: {tool_result.get('error', 'Unknown error')[:200]}",
                severity="medium",
            ))

    return findings
