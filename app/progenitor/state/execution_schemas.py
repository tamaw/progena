"""
Execution Schemas - Structured output types for the inner loop.

BL-001: ExecutionRun/ExecutionResult schema
BL-002: ExperimentPlan schema
BL-003: StructuredApproach schema
BL-007: BuildSettings schema - Structured LLM output for build configuration

These schemas ensure consistent data flow between nodes and enable
deterministic processing without guessing what data is available.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

from ..runtime.time_utils import utcnow


# =============================================================================
# BL-007: BuildSettings Schema - Structured output from LLM for build config
# =============================================================================

class BuildType(str, Enum):
    """Type of build - determines directory structure."""
    AGENT = "agent"  # app/agents/{project_name}/
    MCP = "mcp"      # app/mcp/{project_name}/


class BuildStatus(str, Enum):
    """Status of a build operation."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SUSPENDED = "suspended"  # Graceful halt on cancellation
    ERROR = "error"          # Unrecoverable error


class BuildSettings(BaseModel):
    """Structured build configuration from LLM decision.

    BL-007: Structured output LLM call defines build settings including:
    - Type (agent/mcp) determines base directory
    - Project name for repeatability and directory derivation
    - Allowable directory derived from type + project_name

    Stored in build_state.json for reference throughout build lifecycle.
    """
    model_config = {"use_enum_values": True}

    # Core identification
    build_id: str = Field(description="Unique build identifier")
    project_name: str = Field(description="Name of the project (used for directory and repeatability)")
    build_type: BuildType = Field(description="Type of build: agent or mcp")

    # Derived paths (computed from type + project_name)
    # Note: base_directory removed - focus only on project_directory
    project_directory: str = Field(description="Full project directory: app/agents/{project_name}/ or app/mcp/{project_name}/")
    allowable_directories: list[str] = Field(
        default_factory=list,
        description="Directories Copilot is allowed to modify (defaults to project_directory)"
    )

    # Build metadata
    description: str = Field(default="", description="Human-readable description of what's being built")
    user_request: str = Field(default="", description="Original user request that triggered the build")

    # Status tracking
    status: BuildStatus = Field(default=BuildStatus.PENDING, description="Current build status")
    error_message: Optional[str] = Field(default=None, description="Error message if status is ERROR/FAILED")

    # Git submodule info (for isolated commits)
    submodule_path: Optional[str] = Field(default=None, description="Path to git submodule if initialized")
    submodule_initialized: bool = Field(default=False, description="Whether submodule was created")

    # Timestamps
    created_at: datetime = Field(default_factory=utcnow, description="When build was initiated")
    updated_at: datetime = Field(default_factory=utcnow, description="Last update time")
    completed_at: Optional[datetime] = Field(default=None, description="When build completed/failed")

    @classmethod
    def create_from_llm_output(
        cls,
        build_id: str,
        project_name: str,
        build_type: str | BuildType,
        user_request: str,
        description: str = "",
    ) -> "BuildSettings":
        """Factory method to create BuildSettings from LLM structured output.

        Derives directories from type and project_name automatically.
        """
        # Normalize build type
        if isinstance(build_type, str):
            build_type = BuildType(build_type.lower())

        # Sanitize project name for filesystem
        safe_name = project_name.replace(" ", "_").replace("-", "_").lower()

        # Derive directories based on type
        if build_type == BuildType.AGENT:
            base_dir = "app/agents"
        else:
            base_dir = "app/mcp"

        project_dir = f"{base_dir}/{safe_name}"

        return cls(
            build_id=build_id,
            project_name=safe_name,
            build_type=build_type,
            project_directory=project_dir,
            allowable_directories=[project_dir],  # Restrict Copilot to this directory
            description=description,
            user_request=user_request,
            status=BuildStatus.PENDING,
        )

    def mark_in_progress(self) -> "BuildSettings":
        """Mark build as in progress."""
        self.status = BuildStatus.IN_PROGRESS
        self.updated_at = utcnow()
        return self

    def mark_completed(self) -> "BuildSettings":
        """Mark build as completed."""
        self.status = BuildStatus.COMPLETED
        self.updated_at = utcnow()
        self.completed_at = utcnow()
        return self

    def mark_failed(self, error: str) -> "BuildSettings":
        """Mark build as failed with error message."""
        self.status = BuildStatus.FAILED
        self.error_message = error
        self.updated_at = utcnow()
        self.completed_at = utcnow()
        return self

    def mark_error(self, error: str) -> "BuildSettings":
        """Mark build as error (unrecoverable) with error message."""
        self.status = BuildStatus.ERROR
        self.error_message = error
        self.updated_at = utcnow()
        self.completed_at = utcnow()
        return self

    def mark_suspended(self, reason: str = "Cancelled by signal") -> "BuildSettings":
        """Mark build as suspended (graceful halt)."""
        self.status = BuildStatus.SUSPENDED
        self.error_message = reason
        self.updated_at = utcnow()
        return self

    def set_submodule(self, submodule_path: str) -> "BuildSettings":
        """Set submodule path after initialization."""
        self.submodule_path = submodule_path
        self.submodule_initialized = True
        self.updated_at = utcnow()
        return self


# =============================================================================
# BL-001: ExecutionRun / ExecutionResult Schema
# =============================================================================

class ExecutionStatus(str, Enum):
    """Status of an execution run."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"  # Some prompts succeeded, some failed
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class PromptResult(BaseModel):
    """Result of running a single prompt against an agent."""

    prompt_id: str = Field(description="Identifier for this prompt")
    prompt_text: str = Field(description="The prompt that was sent")

    # Response data
    response_text: Optional[str] = Field(default=None, description="Agent's response text")
    response_data: Optional[dict[str, Any]] = Field(default=None, description="Structured response if applicable")

    # Timing
    started_at: Optional[datetime] = Field(default=None, description="When prompt was sent")
    completed_at: Optional[datetime] = Field(default=None, description="When response was received")
    duration_ms: Optional[float] = Field(default=None, description="Response time in milliseconds")

    # Status
    success: bool = Field(default=False, description="Whether the prompt executed successfully")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    error_type: Optional[str] = Field(default=None, description="Error classification")

    # Transcript for debugging
    transcript: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Full conversation transcript if multi-turn"
    )


class ExecutionRun(BaseModel):
    """A single execution run against a built agent.

    Captures everything needed to reproduce and analyze the run:
    - Agent identification
    - Prompts sent and responses received
    - Timing and performance data
    - Errors and their classification
    """

    run_id: str = Field(description="Unique identifier for this run")
    iteration: int = Field(description="Inner loop iteration number")

    # Agent identification
    agent_name: str = Field(description="Name of the agent being exercised")
    agent_endpoint: Optional[str] = Field(default=None, description="HTTP endpoint if applicable")
    agent_transport: str = Field(default="http", description="Transport: http or stdio")

    # Execution details
    started_at: datetime = Field(default_factory=utcnow, description="When run started")
    completed_at: Optional[datetime] = Field(default=None, description="When run completed")
    duration_ms: Optional[float] = Field(default=None, description="Total duration in ms")

    # Prompt results
    prompt_results: list[PromptResult] = Field(
        default_factory=list,
        description="Results for each prompt in the experiment"
    )

    # Overall status
    status: ExecutionStatus = Field(default=ExecutionStatus.PENDING)

    # Aggregated metrics
    prompts_total: int = Field(default=0, description="Total prompts attempted")
    prompts_succeeded: int = Field(default=0, description="Prompts that succeeded")
    prompts_failed: int = Field(default=0, description="Prompts that failed")

    avg_response_time_ms: Optional[float] = Field(default=None, description="Average response time")

    # Errors (aggregated)
    errors: list[str] = Field(default_factory=list, description="All error messages")

    def finalize(self) -> "ExecutionRun":
        """Compute aggregated metrics after all prompts are run."""
        self.completed_at = utcnow()
        if self.started_at and self.completed_at:
            delta = self.completed_at - self.started_at
            self.duration_ms = delta.total_seconds() * 1000

        self.prompts_total = len(self.prompt_results)
        self.prompts_succeeded = sum(1 for p in self.prompt_results if p.success)
        self.prompts_failed = self.prompts_total - self.prompts_succeeded

        # Compute average response time
        times = [p.duration_ms for p in self.prompt_results if p.duration_ms is not None]
        if times:
            self.avg_response_time_ms = sum(times) / len(times)

        # Aggregate errors
        self.errors = [p.error for p in self.prompt_results if p.error]

        # Determine overall status
        if self.prompts_total == 0:
            self.status = ExecutionStatus.SKIPPED
        elif self.prompts_failed == 0:
            self.status = ExecutionStatus.SUCCESS
        elif self.prompts_succeeded == 0:
            self.status = ExecutionStatus.FAILED
        else:
            self.status = ExecutionStatus.PARTIAL

        return self


class ExecutionResult(BaseModel):
    """Complete execution result for an iteration.

    This is the stable JSON structure stored in state.execution_result
    and/or embedded in last_iteration_output.

    Every iteration produces this payload, even on failures (with errors populated).
    """

    iteration: int = Field(description="Inner loop iteration number")
    task_id: Optional[str] = Field(default=None, description="Task identifier")

    # Execution runs (typically one, but could be multiple for retries)
    runs: list[ExecutionRun] = Field(
        default_factory=list,
        description="All execution runs for this iteration"
    )

    # Primary run (convenience accessor)
    @property
    def primary_run(self) -> Optional[ExecutionRun]:
        """Get the primary (last successful or last) execution run."""
        if not self.runs:
            return None
        # Prefer successful runs
        for run in reversed(self.runs):
            if run.status == ExecutionStatus.SUCCESS:
                return run
        return self.runs[-1]

    # Aggregated status
    status: ExecutionStatus = Field(default=ExecutionStatus.PENDING)

    # Build output (from BUILD phase, preserved for reference)
    build_output: Optional[dict[str, Any]] = Field(default=None, description="Output from BUILD phase")

    # Overall timing
    started_at: datetime = Field(default_factory=utcnow)
    completed_at: Optional[datetime] = Field(default=None)
    total_duration_ms: Optional[float] = Field(default=None)

    # Aggregated errors
    all_errors: list[str] = Field(default_factory=list, description="All errors across runs")

    # Success indicators
    smoke_test_passed: bool = Field(default=False, description="Did the smoke test pass?")
    all_prompts_passed: bool = Field(default=False, description="Did all experiment prompts pass?")

    # Raw data for downstream analysis
    raw_transcripts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="All conversation transcripts for analysis"
    )

    def finalize(self) -> "ExecutionResult":
        """Compute aggregated metrics after all runs complete."""
        self.completed_at = utcnow()
        if self.started_at and self.completed_at:
            delta = self.completed_at - self.started_at
            self.total_duration_ms = delta.total_seconds() * 1000

        # Aggregate errors from all runs
        self.all_errors = []
        for run in self.runs:
            self.all_errors.extend(run.errors)

        # Determine overall status
        if not self.runs:
            self.status = ExecutionStatus.SKIPPED
        else:
            statuses = [run.status for run in self.runs]
            if ExecutionStatus.SUCCESS in statuses:
                self.status = ExecutionStatus.SUCCESS
            elif ExecutionStatus.PARTIAL in statuses:
                self.status = ExecutionStatus.PARTIAL
            elif all(s == ExecutionStatus.FAILED for s in statuses):
                self.status = ExecutionStatus.FAILED
            else:
                self.status = ExecutionStatus.PARTIAL

        # Check smoke test (first prompt of first run)
        if self.runs and self.runs[0].prompt_results:
            self.smoke_test_passed = self.runs[0].prompt_results[0].success

        # Check if all prompts passed
        all_passed = all(
            p.success
            for run in self.runs
            for p in run.prompt_results
        )
        self.all_prompts_passed = all_passed and len(self.runs) > 0

        # Collect all transcripts
        self.raw_transcripts = []
        for run in self.runs:
            for prompt_result in run.prompt_results:
                if prompt_result.transcript:
                    self.raw_transcripts.append({
                        "run_id": run.run_id,
                        "prompt_id": prompt_result.prompt_id,
                        "transcript": prompt_result.transcript,
                    })

        return self


# =============================================================================
# BL-002: ExperimentPlan Schema
# =============================================================================

class PromptSpec(BaseModel):
    """Specification for a single prompt in the experiment."""

    id: str = Field(description="Unique prompt identifier")
    text: str = Field(description="The prompt text to send")

    # Expected behavior
    expected_response_contains: list[str] = Field(
        default_factory=list,
        description="Substrings that should appear in response"
    )
    expected_response_not_contains: list[str] = Field(
        default_factory=list,
        description="Substrings that should NOT appear in response"
    )
    expected_response_format: Optional[str] = Field(
        default=None,
        description="Expected format: 'json', 'text', 'markdown', etc."
    )

    # Scoring
    max_score: float = Field(default=1.0, description="Maximum score for this prompt")
    weight: float = Field(default=1.0, description="Weight in overall experiment score")

    # Timing
    timeout_ms: int = Field(default=30000, description="Timeout for this prompt in ms")

    # Flags
    is_smoke_test: bool = Field(default=False, description="Is this the required smoke test?")
    is_optional: bool = Field(default=False, description="Can experiment continue if this fails?")


class ScoringHook(BaseModel):
    """A scoring hook for evaluating responses."""

    id: str = Field(description="Hook identifier")
    name: str = Field(description="Human-readable name")
    hook_type: str = Field(description="Type: contains, regex, json_path, llm_judge, custom")

    # Configuration per type
    pattern: Optional[str] = Field(default=None, description="Pattern for regex/contains hooks")
    json_path: Optional[str] = Field(default=None, description="JSONPath for json_path hooks")
    llm_prompt: Optional[str] = Field(default=None, description="Prompt for llm_judge hooks")
    custom_function: Optional[str] = Field(default=None, description="Function name for custom hooks")

    # Scoring
    max_score: float = Field(default=1.0, description="Max score this hook can award")
    weight: float = Field(default=1.0, description="Weight in final calculation")


class StopEarlyRule(BaseModel):
    """Rule for stopping the experiment early."""

    id: str = Field(description="Rule identifier")
    rule_type: str = Field(
        description="Type: consecutive_failures, score_threshold, timeout"
    )

    # Parameters per type
    threshold: float = Field(default=0, description="Threshold value")
    description: str = Field(default="", description="Human-readable description")


class ExperimentPlan(BaseModel):
    """Structured experiment plan emitted by PLAN phase.

    This allows EXECUTE to run without guessing what prompts to send.
    Contains all information needed to exercise the built agent.
    """

    plan_id: str = Field(description="Unique plan identifier")
    iteration: int = Field(description="Target iteration number")

    # What we're testing
    agent_name: str = Field(description="Name of agent to exercise")
    experiment_description: str = Field(description="What this experiment tests")

    # Prompts to run
    prompts: list[PromptSpec] = Field(
        default_factory=list,
        description="Prompts to send, in order"
    )

    # Scoring configuration
    scoring_hooks: list[ScoringHook] = Field(
        default_factory=list,
        description="Hooks for scoring responses"
    )

    # Execution parameters
    num_trials: int = Field(default=1, description="Number of times to run the experiment")
    parallel_trials: bool = Field(default=False, description="Run trials in parallel?")

    # Stop-early rules
    stop_early_rules: list[StopEarlyRule] = Field(
        default_factory=list,
        description="Rules for stopping early"
    )

    # Timeouts
    total_timeout_ms: int = Field(default=120000, description="Total experiment timeout")
    per_prompt_timeout_ms: int = Field(default=30000, description="Default per-prompt timeout")

    # Expected measurable outcome (from loop.spec.md PLAN phase)
    expected_outcome: str = Field(
        default="",
        description="What success looks like for this experiment"
    )
    verification_method: str = Field(
        default="score_threshold",
        description="How to verify success"
    )

    # Optional reasoning trace from the LLM
    reasoning_trace: Optional[str] = Field(
        default=None,
        description="Reasoning trace from the LLM, if available"
    )

    @classmethod
    def create_smoke_test_plan(
        cls,
        agent_name: str,
        iteration: int,
        smoke_prompt: str = "Hello, are you working?",
    ) -> "ExperimentPlan":
        """Create a minimal smoke test plan."""
        import uuid
        return cls(
            plan_id=f"smoke_{uuid.uuid4().hex[:8]}",
            iteration=iteration,
            agent_name=agent_name,
            experiment_description="Smoke test - verify agent responds",
            prompts=[
                PromptSpec(
                    id="smoke_1",
                    text=smoke_prompt,
                    is_smoke_test=True,
                    timeout_ms=10000,
                )
            ],
            expected_outcome="Agent responds without error",
            verification_method="response_received",
        )


# =============================================================================
# BL-003: StructuredApproach Schema
# =============================================================================

class TradeOff(BaseModel):
    """A trade-off associated with an approach."""

    aspect: str = Field(description="What aspect this trade-off affects")
    positive: str = Field(description="What we gain")
    negative: str = Field(description="What we give up")
    severity: str = Field(default="medium", description="low, medium, high")


class Risk(BaseModel):
    """A risk associated with an approach."""

    id: str = Field(description="Risk identifier")
    description: str = Field(description="What could go wrong")
    likelihood: str = Field(default="medium", description="low, medium, high")
    impact: str = Field(default="medium", description="low, medium, high")
    mitigation: Optional[str] = Field(default=None, description="How to mitigate")


class ExperimentPlanDelta(BaseModel):
    """Recommended changes to the experiment plan for this approach."""

    additional_prompts: list[PromptSpec] = Field(
        default_factory=list,
        description="Additional prompts to add"
    )
    remove_prompt_ids: list[str] = Field(
        default_factory=list,
        description="Prompt IDs to remove"
    )
    modify_scoring: list[ScoringHook] = Field(
        default_factory=list,
        description="Scoring hooks to add/modify"
    )
    adjust_timeout_ms: Optional[int] = Field(
        default=None,
        description="New timeout if different"
    )


class StructuredApproach(BaseModel):
    """A structured approach from THINK phase.

    BL-003: Approaches become typed/JSON with explicit tradeoffs/risks
    and recommended experiment plan deltas.

    PLAN can select one approach deterministically (or with a simple policy).
    """

    id: str = Field(description="Unique approach identifier")
    name: str = Field(description="Short name for the approach")
    description: str = Field(description="Detailed description of what to do")

    # Strategy details
    strategy: str = Field(description="The core strategy/technique")
    implementation_steps: list[str] = Field(
        default_factory=list,
        description="Concrete steps to implement"
    )

    # Trade-offs and risks (BL-003 requirement)
    trade_offs: list[TradeOff] = Field(
        default_factory=list,
        description="Explicit trade-offs"
    )
    risks: list[Risk] = Field(
        default_factory=list,
        description="Known risks"
    )

    # Experiment plan recommendations (BL-003 requirement)
    recommended_plan_delta: Optional[ExperimentPlanDelta] = Field(
        default=None,
        description="Recommended changes to experiment plan"
    )

    # Scoring for approach selection
    expected_success_probability: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Estimated probability of success"
    )
    expected_effort: str = Field(
        default="medium",
        description="low, medium, high"
    )

    # Targeting gaps
    addresses_gaps: list[str] = Field(
        default_factory=list,
        description="Which gaps from last verification this addresses"
    )

    # Priority/ranking
    priority: int = Field(
        default=1,
        description="Priority rank (1=highest)"
    )


class ThinkOutput(BaseModel):
    """Structured output from THINK phase.

    Contains 2-3 structured approaches with explicit tradeoffs/risks.
    """

    iteration: int = Field(description="Current iteration")

    # The approaches (BL-003: 2-3 structured approaches)
    approaches: list[StructuredApproach] = Field(
        min_length=1,
        max_length=5,
        description="2-3 approaches with tradeoffs/risks"
    )

    # Analysis context
    gaps_analyzed: list[str] = Field(
        default_factory=list,
        description="Gaps from previous verification that were analyzed"
    )
    constraints_considered: list[str] = Field(
        default_factory=list,
        description="Constraints that influenced approach selection"
    )

    # Recommendation
    recommended_approach_id: Optional[str] = Field(
        default=None,
        description="ID of the recommended approach"
    )
    recommendation_reasoning: str = Field(
        default="",
        description="Why this approach is recommended"
    )

    # Optional reasoning trace from the LLM
    reasoning_trace: Optional[str] = Field(
        default=None,
        description="Reasoning trace from the LLM, if available"
    )


# =============================================================================
# BL-005: ValidationResult (data-only, non-mutating)
# =============================================================================

class ValidationFinding(BaseModel):
    """A single finding from validation."""

    finding_id: str = Field(description="Unique finding identifier")
    category: str = Field(description="Category: error, warning, info, suggestion")
    source: str = Field(description="What produced this finding")
    message: str = Field(description="Human-readable message")

    # Location (if applicable)
    file_path: Optional[str] = Field(default=None, description="File where issue was found")
    line_number: Optional[int] = Field(default=None, description="Line number")

    # Severity (may be set by LLM structured output)
    severity: str = Field(default="medium", description="low, medium, high, critical")

    # Evidence
    evidence: Optional[str] = Field(default=None, description="Supporting evidence (full text)")

    # Stack trace (parsed from Python tracebacks)
    stack_trace: Optional[list[str]] = Field(
        default=None,
        description="Parsed stack trace frames from Python errors"
    )

    # Suggested fix
    suggested_fix: Optional[str] = Field(
        default=None,
        description="Suggested remediation from LLM or heuristic"
    )

    # Relationships
    related_finding_ids: list[str] = Field(
        default_factory=list,
        description="IDs of related findings"
    )
    is_root_cause: bool = Field(
        default=False,
        description="Whether this finding is identified as a root cause"
    )
    duplicate_of: Optional[str] = Field(
        default=None,
        description="If this is a duplicate, the ID of the original"
    )
    duplicate_count: int = Field(
        default=0,
        description="Number of duplicates suppressed"
    )


class ValidationAction(BaseModel):
    """A recorded validation action (external command invocation).

    Records Python tool invocations (pytest, pyright, mypy, ruff)
    for auditability and result capture.
    """

    action_id: str = Field(description="Unique action identifier")
    tool: str = Field(description="Tool name: pytest, pyright, mypy, ruff")
    command: str = Field(description="Full command that was executed")
    project_directory: str = Field(description="Directory the command ran in")

    # Results
    exit_code: Optional[int] = Field(default=None, description="Process exit code")
    stdout: Optional[str] = Field(default=None, description="Standard output (truncated)")
    stderr: Optional[str] = Field(default=None, description="Standard error (truncated)")
    duration_ms: Optional[float] = Field(default=None, description="Execution time in ms")

    # Timing
    started_at: datetime = Field(default_factory=utcnow)
    completed_at: Optional[datetime] = Field(default=None)

    # Status
    success: bool = Field(default=False, description="Whether the tool succeeded")
    error_count: int = Field(default=0, description="Number of errors found by tool")
    warning_count: int = Field(default=0, description="Number of warnings found by tool")


class ValidationStatistics(BaseModel):
    """Extended validation statistics."""

    total_findings: int = Field(default=0)
    by_category: dict[str, int] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)

    # Quality indicators
    smoke_test_passed: bool = Field(default=False)
    all_critical_passed: bool = Field(default=True)

    # Trend (compared to previous iteration)
    previous_error_count: Optional[int] = Field(
        default=None,
        description="Error count from previous validation"
    )
    trend: Optional[str] = Field(
        default=None,
        description="improving, stable, or degrading"
    )

    # Deduplication stats
    duplicates_suppressed: int = Field(default=0)
    patterns_detected: int = Field(default=0)


class ValidationResultV2(BaseModel):
    """Validation result with Python tool actions, LLM severity, and analytics.

    VALIDATE analyzes execution results, invokes Python validation tools
    (pytest, pyright, mypy, ruff), and uses LLM structured output for
    severity assessment.

    Records all external actions for auditability.
    """

    iteration: int = Field(description="Iteration number")
    validated_at: datetime = Field(default_factory=utcnow)

    # What was validated
    execution_result_ref: Optional[str] = Field(
        default=None,
        description="Reference to the ExecutionResult being validated"
    )

    # Findings (from analysis + tool output)
    findings: list[ValidationFinding] = Field(
        default_factory=list,
        description="All validation findings"
    )

    # Aggregated counts
    error_count: int = Field(default=0)
    warning_count: int = Field(default=0)
    info_count: int = Field(default=0)

    # Overall assessment
    has_blocking_issues: bool = Field(
        default=False,
        description="Are there issues that block progress?"
    )

    # Validation actions (recorded Python tool invocations)
    validation_actions: list[ValidationAction] = Field(
        default_factory=list,
        description="Recorded external tool invocations (pytest, pyright, etc.)"
    )

    # Extended statistics
    statistics: Optional[ValidationStatistics] = Field(
        default=None,
        description="Extended validation statistics and trend data"
    )

    # LLM severity assessment
    llm_severity_assessed: bool = Field(
        default=False,
        description="Whether LLM was used for severity assessment"
    )

    def compute_counts(self) -> "ValidationResultV2":
        """Compute aggregated counts from findings."""
        self.error_count = sum(1 for f in self.findings if f.category == "error")
        self.warning_count = sum(1 for f in self.findings if f.category == "warning")
        self.info_count = sum(1 for f in self.findings if f.category == "info")
        self.has_blocking_issues = self.error_count > 0
        return self

    def compute_statistics(
        self,
        previous_error_count: Optional[int] = None,
        duplicates_suppressed: int = 0,
        patterns_detected: int = 0,
        smoke_test_passed: bool = False,
    ) -> "ValidationResultV2":
        """Compute extended statistics."""
        by_category: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        by_source: dict[str, int] = {}

        for f in self.findings:
            by_category[f.category] = by_category.get(f.category, 0) + 1
            by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
            by_source[f.source] = by_source.get(f.source, 0) + 1

        all_critical_passed = not any(
            f.severity == "critical" and f.category == "error"
            for f in self.findings
        )

        trend = None
        if previous_error_count is not None:
            if self.error_count < previous_error_count:
                trend = "improving"
            elif self.error_count == previous_error_count:
                trend = "stable"
            else:
                trend = "degrading"

        self.statistics = ValidationStatistics(
            total_findings=len(self.findings),
            by_category=by_category,
            by_severity=by_severity,
            by_source=by_source,
            smoke_test_passed=smoke_test_passed,
            all_critical_passed=all_critical_passed,
            previous_error_count=previous_error_count,
            trend=trend,
            duplicates_suppressed=duplicates_suppressed,
            patterns_detected=patterns_detected,
        )
        return self


# =============================================================================
# BL-006: VerificationEvidence for VERIFY scoring
# =============================================================================

class CriterionEvidence(BaseModel):
    """Evidence for scoring a criterion."""

    criterion_id: str = Field(description="Which criterion this is for")

    # Score
    score: float = Field(ge=0.0, le=1.0, description="Score 0-1")
    passed: bool = Field(description="Did it meet target?")

    # Evidence sources
    execution_evidence: Optional[str] = Field(
        default=None,
        description="Evidence from ExecutionResult"
    )
    validation_evidence: Optional[str] = Field(
        default=None,
        description="Evidence from ValidationResult"
    )

    # How score was computed
    scoring_method: str = Field(
        default="heuristic",
        description="heuristic, mechanical, llm_judge"
    )
    mechanical_check: Optional[str] = Field(
        default=None,
        description="Description of mechanical check if applicable"
    )

    # LLM assessment (only if needed for subjective criteria)
    llm_assessment: Optional[str] = Field(
        default=None,
        description="LLM's assessment if used"
    )

    reasoning_trace: Optional[str] = Field(
        default=None,
        description="Reasoning trace from the LLM, if available"
    )


class VerificationReportV2(BaseModel):
    """Enhanced verification report (BL-006).

    VERIFY consumes structured execution+validation data to compute
    as many criterion scores as possible mechanically, then optionally
    uses LLM for subjective criteria.

    Verification report cites concrete evidence from execution runs.
    """

    iteration: int = Field(description="Iteration number")
    verified_at: datetime = Field(default_factory=utcnow)

    # Data sources
    execution_result_ref: Optional[str] = Field(
        default=None,
        description="Reference to ExecutionResult"
    )
    validation_result_ref: Optional[str] = Field(
        default=None,
        description="Reference to ValidationResult"
    )

    # Per-criterion scoring with evidence
    criterion_scores: list[CriterionEvidence] = Field(
        default_factory=list,
        description="Score and evidence per criterion"
    )

    # Anti-criterion violations
    anti_criterion_violations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Anti-criteria that were violated"
    )

    # Overall score
    overall_score: float = Field(ge=0.0, le=1.0, description="Weighted overall score")
    passed: bool = Field(description="Did it pass minimum threshold?")

    # Mechanical vs LLM scoring breakdown
    mechanical_score_count: int = Field(
        default=0,
        description="How many criteria were scored mechanically"
    )
    llm_score_count: int = Field(
        default=0,
        description="How many criteria needed LLM judgment"
    )

    # Gaps and next actions
    gaps: list[str] = Field(default_factory=list, description="What's still missing")
    next_actions: list[str] = Field(default_factory=list, description="Suggested next steps")

    # Summary with evidence citations
    summary: str = Field(description="Human-readable summary")
    evidence_citations: list[str] = Field(
        default_factory=list,
        description="Citations to concrete evidence from execution runs"
    )

    def compute_score_breakdown(self) -> "VerificationReportV2":
        """Compute the mechanical vs LLM score breakdown."""
        self.mechanical_score_count = sum(
            1 for c in self.criterion_scores
            if c.scoring_method in ("heuristic", "mechanical")
        )
        self.llm_score_count = sum(
            1 for c in self.criterion_scores
            if c.scoring_method == "llm_judge"
        )
        return self


# =============================================================================
# SpecDoc: Persistent Spec Artifact (PLAN-owned, BUILD-consumed)
# =============================================================================

class SpecDocHistory(BaseModel):
    """A single history entry for spec document changes."""

    revision: int = Field(description="Revision number at this point")
    updated_at: datetime = Field(description="When this change was made")
    summary: str = Field(description="Brief summary of what changed")
    changed_by: str = Field(default="PLAN", description="Which node made the change")


class SpecDoc(BaseModel):
    """
    Persistent spec artifact owned by PLAN, consumed by BUILD.

    Stored in build_state.json under the key 'spec_doc'.
    Evolves each iteration based on THINK outputs and verification results.

    This makes the inner loop self-documenting:
    - First run: PLAN seeds from a canonical template (e.g., agent_structure.md)
    - Every iteration: PLAN updates based on THINK outputs
    - BUILD consumes the latest spec_doc.content in its prompt/context
    """

    # Core content
    content: str = Field(description="The markdown spec content")

    # Provenance
    source_seed_path: str = Field(
        description="Path to the original seed template (e.g., 'prompts/agent_structure.md')"
    )

    # Versioning
    revision: int = Field(default=1, description="Revision number, increments on each update")
    created_at: datetime = Field(default_factory=utcnow, description="When first created")
    updated_at: datetime = Field(default_factory=utcnow, description="When last updated")

    # Context for what/who this spec is for
    task_id: Optional[str] = Field(default=None, description="Work item ID this spec is for")
    agent_name: Optional[str] = Field(default=None, description="Target agent name if applicable")

    # File path for copilot CLI consumption (now in app/agents/)
    spec_file_path: Optional[str] = Field(
        default=None,
        description="Path to the spec file in app/agents/ for copilot to reference"
    )

    # The prompt that instructs Copilot to read and build from the spec file
    # This is separate from the spec content itself - the prompt references the spec file
    copilot_prompt: Optional[str] = Field(
        default=None,
        description="The prompt to send to Copilot that references the spec file"
    )

    # Auditability
    history: list[SpecDocHistory] = Field(
        default_factory=list,
        description="History of changes for auditability"
    )

    # Metadata about updates
    last_think_output_ref: Optional[str] = Field(
        default=None,
        description="Reference to THINK output that informed last update"
    )
    iteration_last_updated: int = Field(
        default=0,
        description="Inner loop iteration when last updated"
    )

    def update_from_think(
        self,
        think_outputs: list[dict],
        iteration: int,
        verification_gaps: list[str] | None = None,
        lessons: list[dict] | None = None,  # BL-BUG-002: Add lessons_learned
    ) -> "SpecDoc":
        """
        Update spec content based on THINK phase outputs.

        Appends relevant insights from approaches/tradeoffs/risks to the spec,
        notes any gaps that verification identified, and includes lessons learned.
        """
        additions = []

        # Extract key insights from THINK approaches
        for i, approach in enumerate(think_outputs[:3]):  # Max 3 approaches
            name = approach.get("name", f"Approach {i+1}")
            description = approach.get("description", "")
            if description:
                additions.append(f"- **{name}**: {description[:200]}")

            # Include tradeoffs
            tradeoffs = approach.get("tradeoffs", [])
            for tradeoff in tradeoffs[:2]:
                if isinstance(tradeoff, dict):
                    aspect = tradeoff.get("aspect", "")
                    gain = tradeoff.get("gain", "")
                    if aspect and gain:
                        additions.append(f"  - Trade-off ({aspect}): {gain[:100]}")

        # Include verification gaps if provided
        if verification_gaps:
            additions.append("\n### Gaps to Address")
            for gap in verification_gaps[:5]:
                additions.append(f"- {gap}")

        # BL-BUG-002: Include lessons learned if provided
        if lessons:
            recent_lessons = lessons[-2:] if len(lessons) > 2 else lessons
            additions.append("\n### Lessons Learned")
            for lesson in recent_lessons:
                iter_num = lesson.get("iteration", "?")
                what_worked = lesson.get("what_worked", [])
                what_failed = lesson.get("what_failed", [])
                adjustments = lesson.get("adjustments", [])

                if what_worked or what_failed or adjustments:
                    additions.append(f"#### Iteration {iter_num}")
                    if what_worked:
                        # Handle both string and dict items
                        worked_strs = [
                            item if isinstance(item, str) else item.get("criterion", str(item))
                            for item in what_worked[:3]
                        ]
                        additions.append("- **What worked:** " + "; ".join(worked_strs))
                    if what_failed:
                        # Handle both string and dict items
                        failed_strs = [
                            item if isinstance(item, str) else (item.get("criterion") or item.get("description") or str(item))
                            for item in what_failed[:3]
                        ]
                        additions.append("- **What failed:** " + "; ".join(failed_strs))
                    if adjustments:
                        # Handle both string and dict items
                        adj_strs = [
                            item if isinstance(item, str) else str(item)
                            for item in adjustments[:3]
                        ]
                        additions.append("- **Adjustments:** " + "; ".join(adj_strs))

        if additions:
            # Append to content
            update_section = f"\n\n## Iteration {iteration} Updates\n" + "\n".join(additions)
            self.content += update_section

            # Update metadata
            self.revision += 1
            self.updated_at = utcnow()
            self.iteration_last_updated = iteration

            # Add to history
            self.history.append(SpecDocHistory(
                revision=self.revision,
                updated_at=self.updated_at,
                summary=f"Updated from THINK outputs (iteration {iteration})",
                changed_by="PLAN",
            ))

        return self

    @classmethod
    def create_from_seed(
        cls,
        seed_content: str,
        seed_path: str,
        task_id: str | None = None,
        agent_name: str | None = None,
    ) -> "SpecDoc":
        """Create a new SpecDoc from a seed template."""
        now = utcnow()
        return cls(
            content=seed_content,
            source_seed_path=seed_path,
            revision=1,
            created_at=now,
            updated_at=now,
            task_id=task_id,
            agent_name=agent_name,
            history=[SpecDocHistory(
                revision=1,
                updated_at=now,
                summary=f"Seeded from {seed_path}",
                changed_by="PLAN",
            )],
        )

