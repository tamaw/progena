"""State models for Progenitor using Pydantic v2."""

import logging
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, TypedDict

from pydantic import BaseModel, Field, field_validator

from ..runtime.time_utils import utcnow

logger = logging.getLogger(__name__)


def generate_run_id() -> str:
    """Generate a unique run ID for task tracking."""
    return f"run_{uuid.uuid4().hex[:12]}"


class WorkItemStatus(StrEnum):
    """Status of a work item in the backlog."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class WorkItemType(StrEnum):
    """Type of work item."""
    BUILD_AGENT = "build_agent"
    BUILD_MCP = "build_mcp"  # Build FastMCP tool in app/mcp/
    BUILD_PROGENITOR = "build_progenitor"  # Improve Progenitor in app/progenitor/
    UPGRADE = "upgrade"
    A2A_CALL = "a2a_call"
    MCP_CALL = "mcp_call"
    SCHEDULED = "scheduled"
    DIRECT_ANSWER = "direct_answer"
    SYSTEM_TOOL = "system_tool"  # Internal system tools like agent discovery


class DecisionAction(StrEnum):
    """Actions that can be decided."""
    ANSWER_DIRECTLY = "answer_directly"
    BUILD_AGENT = "build_agent"
    BUILD_MCP = "build_mcp"  # Build FastMCP tool in app/mcp/
    BUILD_PROGENITOR = "build_progenitor"  # Improve Progenitor in app/progenitor/


class BuildPhase(StrEnum):
    """Phases of the inner build loop."""
    OBSERVE = "observe"
    THINK = "think"
    PLAN = "plan"
    BUILD = "build"
    EXECUTE = "execute"
    VERIFY = "verify"
    LEARN = "learn"

    def __str__(self) -> str:
        return f"{self.__class__.__name__}.{self.name}"


class IterationStopReason(StrEnum):
    """Reasons why the inner loop iteration stopped."""
    MAX_ITERATIONS = "max_iterations"
    VERIFICATION_PASSED = "verification_passed"
    USER_INTERVENTION = "user_intervention"
    BUILD_FAILURE = "build_failure"
    TIMEOUT = "timeout"
    STAGNATING = "stagnating"  # No progress across iterations
    MANUAL_STOP = "manual_stop"


# Patterns for secret redaction - (pattern, replacement_or_handler)
# Order matters: more specific patterns should come first
# NOTE: Patterns are carefully crafted to avoid false positives (e.g., UUIDs, hashes)
SECRET_PATTERNS = [
    # OpenAI keys (multiple formats) - very specific prefixes
    (r'sk-proj-[A-Za-z0-9\-_]{20,}', '[OPENAI_PROJECT_KEY_REDACTED]'),  # OpenAI project keys
    (r'sk-[A-Za-z0-9]{20,}', '[OPENAI_KEY_REDACTED]'),  # OpenAI keys (sk- prefix)

    # Azure keys - context-aware patterns only (avoid matching UUIDs/hashes)
    # Match "key": "value" or key=value patterns with Azure context
    (r'(?i)(?:azure|az)[_-]?(?:openai)?[_-]?(?:api)?[_-]?key["\']?\s*[:=]\s*["\']?([A-Za-z0-9]{32,})', '[AZURE_KEY_REDACTED]'),
    # Match environment variable style AZURE_*_KEY=value
    (r'(?i)(AZURE_[A-Z_]*KEY)\s*=\s*([A-Za-z0-9]{32,})', r'\1=[AZURE_KEY_REDACTED]'),
    # Only match 32-char hex strings in key/secret/credential contexts (not standalone)
    (r'(?i)(?:key|secret|credential|token)["\']?\s*[:=]\s*["\']?([A-Fa-f0-9]{32})(?![A-Za-z0-9])', '[AZURE_KEY_REDACTED]'),

    # GitHub tokens - very specific prefixes
    (r'ghp_[A-Za-z0-9]{36,}', '[GITHUB_TOKEN_REDACTED]'),  # GitHub personal access tokens
    (r'gho_[A-Za-z0-9]{36,}', '[GITHUB_OAUTH_REDACTED]'),  # GitHub OAuth tokens
    (r'ghs_[A-Za-z0-9]{36,}', '[GITHUB_SERVER_REDACTED]'),  # GitHub server tokens
    (r'ghu_[A-Za-z0-9]{36,}', '[GITHUB_USER_REDACTED]'),  # GitHub user tokens
    (r'github_pat_[A-Za-z0-9_]{22,}', '[GITHUB_PAT_REDACTED]'),  # Fine-grained PATs

    # Anthropic keys
    (r'sk-ant-[A-Za-z0-9\-_]{20,}', '[ANTHROPIC_KEY_REDACTED]'),

    # Generic patterns - require context (key/value assignment)
    (r'(?i)(api[_-]?key|apikey)["\']?\s*[:=]\s*["\']?([^"\',\s]{8,})', r'\1=[REDACTED]'),
    (r'(?i)(secret|password|passwd|pwd|credential)["\']?\s*[:=]\s*["\']?([^"\',\s]{8,})', r'\1=[REDACTED]'),
    (r'(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*', r'\1[REDACTED]'),

    # AWS patterns - specific prefixes
    (r'AKIA[A-Z0-9]{16}', '[AWS_ACCESS_KEY_REDACTED]'),
    (r'(?i)(aws[_-]?secret[_-]?access[_-]?key)["\']?\s*[:=]\s*["\']?[A-Za-z0-9/+=]{40}', r'\1=[REDACTED]'),
]


def redact_secrets(text: str) -> str:
    """Redact sensitive information from text before logging.

    This function is designed to minimize false positives while catching
    real secrets. It requires context (key=value patterns) for generic
    patterns and uses specific prefixes for known API key formats.

    UUIDs, hashes, and work item IDs should NOT be redacted.
    """
    import re
    result = text
    for pattern, replacement in SECRET_PATTERNS:
        result = re.sub(pattern, replacement, result)
    return result


class WorkItem(BaseModel):
    """A single work item in the backlog.

    Work items represent discrete units of work that Progenitor processes.
    Each item has a type (build, a2a call, etc.) and progresses through
    status states from pending to completed/failed/blocked.
    """
    model_config = {"use_enum_values": True}

    id: str
    item_type: WorkItemType = WorkItemType.DIRECT_ANSWER
    status: WorkItemStatus = WorkItemStatus.PENDING
    description: str
    context: dict[str, Any] = Field(default_factory=dict)
    target_agent: str | None = None
    target_tool: str | None = None
    worktree_path: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    retry_count: int = 0
    max_retries: int = 3
    error_message: str | None = None
    result: dict[str, Any] | None = None

    @field_validator('item_type', mode='before')
    @classmethod
    def validate_item_type(cls, v):
        """Convert string to enum if needed."""
        if isinstance(v, WorkItemType):
            return v
        if isinstance(v, str):
            try:
                return WorkItemType(v)
            except ValueError:
                logger.warning("Unknown WorkItemType %r, defaulting to DIRECT_ANSWER", v)
                return WorkItemType.DIRECT_ANSWER
        logger.warning("Unexpected WorkItem item_type type %s, defaulting to DIRECT_ANSWER", type(v).__name__)
        return WorkItemType.DIRECT_ANSWER

    @field_validator('status', mode='before')
    @classmethod
    def validate_status(cls, v):
        """Convert string to enum if needed."""
        if isinstance(v, WorkItemStatus):
            return v
        if isinstance(v, str):
            try:
                return WorkItemStatus(v)
            except ValueError:
                logger.warning("Unknown WorkItemStatus %r, defaulting to PENDING", v)
                return WorkItemStatus.PENDING
        logger.warning("Unexpected WorkItem status type %s, defaulting to PENDING", type(v).__name__)
        return WorkItemStatus.PENDING

    @property
    def status_value(self) -> str:
        """Get status as string value for comparisons."""
        return self.status

    @property
    def item_type_value(self) -> str:
        """Get item_type as string value for comparisons."""
        return self.item_type

    @property
    def is_pending(self) -> bool:
        """Check if work item is pending."""
        return self.status_value == WorkItemStatus.PENDING.value

    @property
    def is_in_progress(self) -> bool:
        """Check if work item is in progress."""
        return self.status_value == WorkItemStatus.IN_PROGRESS.value

    @property
    def is_actionable(self) -> bool:
        """Check if work item can be acted on (pending or in_progress)."""
        return self.status_value in (WorkItemStatus.PENDING.value, WorkItemStatus.IN_PROGRESS.value)

    @property
    def is_build_type(self) -> bool:
        """Check if this is a build-related work item."""
        return self.item_type_value in (
            WorkItemType.BUILD_AGENT.value,
            WorkItemType.BUILD_MCP.value,
            WorkItemType.BUILD_PROGENITOR.value,
            WorkItemType.UPGRADE.value,
        )

    def mark_in_progress(self) -> None:
        """Mark the item as in progress."""
        self.status = WorkItemStatus.IN_PROGRESS
        self.updated_at = utcnow()

    def mark_completed(self, result: dict[str, Any] | None = None) -> None:
        """Mark the item as completed."""
        self.status = WorkItemStatus.COMPLETED
        self.result = result
        self.updated_at = utcnow()

    def mark_failed(self, error: str) -> None:
        """Mark the item as failed."""
        self.status = WorkItemStatus.FAILED
        self.error_message = error
        self.retry_count += 1
        self.updated_at = utcnow()

        if self.retry_count >= self.max_retries:
            self.status = WorkItemStatus.BLOCKED


class Decision(BaseModel):
    """A routing decision made by the Decide node.

    Captures the LLM's decision about how to handle user input,
    including whether to answer directly, delegate, or build.
    """
    model_config = {"use_enum_values": True}

    action: DecisionAction
    reasoning: str
    requires_planning: bool = False
    target_capabilities: list[str] = Field(default_factory=list)
    direct_response: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ScheduleEntry(BaseModel):
    """A scheduled task entry.

    Simplified model:
    - One-off tasks: Just set `run_at`, leave `repeat_minutes` as None
    - Recurring tasks: Set `run_at` for first run, `repeat_minutes` for interval

    After execution, `last_run` is updated. For one-off tasks, having
    `last_run` set means it's complete (won't re-trigger).
    For recurring tasks, `run_at` is updated to the next occurrence.
    """
    model_config = {"use_enum_values": True}

    id: str
    description: str
    instruction: str  # What the agent should do at the scheduled time
    run_at: datetime  # When to execute (updated for recurring tasks)
    repeat_minutes: int | None = None  # If set, repeat every N minutes
    last_run: datetime | None = None  # When it last executed
    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class AgentStatus(StrEnum):
    """Runtime status of an A2A agent."""
    STOPPED = "stopped"      # Not running
    STARTING = "starting"    # Process spawned, waiting for ready
    RUNNING = "running"      # Healthy and accepting requests
    EXECUTING = "executing"  # Currently processing a request
    WAITING = "waiting"      # Idle, waiting for requests
    FAILED = "failed"        # Failed to start or crashed
    STOPPING = "stopping"    # Graceful shutdown in progress


class AgentRegistryEntry(BaseModel):
    """An agent in the registry.

    Agents are HTTP-based and expose an agent card endpoint per A2A standard.
    The registry tracks runtime status derived from stdout/stderr streams.
    """
    name: str
    kind: str = "a2a_agent"  # a2a_agent | mcp_server
    version: str = "0.1.0"
    description: str
    capabilities: list[str] = Field(default_factory=list)
    entrypoint: str  # File path to agent module

    # HTTP endpoint configuration
    transport: str = "http"  # http only
    port: int | None = None  # Assigned HTTP port (e.g., 8100-8199)
    endpoint: str | None = None  # Base URL (e.g., http://localhost:8100)
    agent_card_url: str | None = None  # Agent card endpoint (e.g., http://localhost:8100/.well-known/agent.json)

    # Runtime status (updated from process stdout/stderr)
    status: AgentStatus = AgentStatus.STOPPED
    status_message: str | None = None  # Human-readable status detail
    pid: int | None = None  # Process ID when running
    startup_time: datetime | None = None  # When agent was last started
    last_heartbeat: datetime | None = None  # Last health check
    last_error: str | None = None  # Last error message if failed

    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ConversationMessage(BaseModel):
    """A message in the conversation history."""
    id: str
    role: str  # user | assistant | system | tool
    content: str
    timestamp: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(BaseModel):
    """Result of a validation run."""
    success: bool
    work_item_id: str
    checks_run: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utcnow)


# ============================================================================
# Composable State Types for LangGraph
# Split into focused substates for better organization and testing
# ============================================================================

class ConversationState(TypedDict, total=False):
    """Conversation-related state."""
    messages: list[ConversationMessage]
    current_input: str | None
    current_input_role: str | None
    current_input_metadata: dict[str, Any] | None
    pending_response: str | None
    attachments: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]


class WorkState(TypedDict, total=False):
    """Work item and backlog state."""
    backlog: list[WorkItem]
    current_work_item: WorkItem | None
    current_decision: Decision | None
    build_settings: dict[str, Any] | None  # BL-007: BuildSettings from structured LLM output
    current_build_request: str | None


class SchedulingState(TypedDict, total=False):
    """Scheduling and registry state."""
    schedule: list[ScheduleEntry]
    new_schedule_entry: ScheduleEntry | None  # Pending entry to be added
    current_time: datetime | None
    due_items: list[str]  # IDs of due schedule entries
    agents: list[AgentRegistryEntry]


class BackgroundJobState(TypedDict, total=False):
    """Background job execution state.

    Used by the dedicated background job graph.
    """
    bg_had_work: bool
    bg_schedule_id: str | None
    bg_work_type: str | None  # "scheduled" | "backlog" | None
    last_cycle_response: str | None


class LoopControlState(TypedDict, total=False):
    """Loop control and metadata."""
    cycle_count: int
    actions_this_cycle: int
    shutdown_requested: bool
    last_commit_time: datetime | None
    run_id: str
    last_validation_result: dict[str, Any] | None
    validation_result_v2: dict[str, Any] | None  # ValidationResultV2 as dict
    last_checkpoint: str | None


class TaskInfo(TypedDict, total=False):
    """Build task information."""
    description: str  # Human-readable build description (summary)
    target_capabilities: list[str]  # Capabilities being built
    created_at: str | None  # ISO timestamp when task was created


class IterationInfo(TypedDict, total=False):
    """Iteration tracking."""
    current: int  # Current iteration number
    max: int  # Maximum iterations allowed
    start_time: float  # Timestamp when iteration started


class SpecInfo(TypedDict, total=False):
    """Spec document reference (content read from file, not stored here)."""
    file_path: str | None  # Path to spec.md file
    revision: int  # Current revision number
    created_at: str | None  # ISO timestamp
    updated_at: str | None  # ISO timestamp


class ThinkPhase(TypedDict, total=False):
    """THINK phase results."""
    approaches: list[dict[str, Any]]  # Structured approaches
    recommended_id: str | None  # Selected approach ID
    gaps_analyzed: list[str]  # Gaps from previous iteration
    completed_at: str | None  # ISO timestamp when thinking completed


class ObservePhase(TypedDict, total=False):
    """OBSERVE phase results."""
    ideal_state: dict[str, Any] | None  # IdealState as dict for JSON serialization
    completed_at: str | None  # ISO timestamp when observe completed


class PlanPhase(TypedDict, total=False):
    """PLAN phase results."""
    experiment_plan: dict[str, Any] | None  # ExperimentPlan
    completed_at: str | None  # ISO timestamp when planning completed


class ExecutePhase(TypedDict, total=False):
    """EXECUTE phase results."""
    completed: bool  # Whether execution finished
    result: dict[str, Any] | None  # Compact execution result (tests, duration, status)
    completed_at: str | None  # ISO timestamp when execution completed


class VerifyPhase(TypedDict, total=False):
    """VERIFY phase results."""
    passed: bool  # Whether verification passed
    score: float  # Verification score
    target_score: float  # Target score to pass
    gaps: list[str]  # Unmet criteria
    completed_at: str | None  # ISO timestamp when verification completed


class BuildPhases(TypedDict, total=False):
    """Phase-grouped state - single source of truth for each phase output.

    Each phase includes optional completion timestamps for traceability.
    """
    observe: ObservePhase
    observe_completed_at: str | None  # ISO timestamp
    think: ThinkPhase
    think_completed_at: str | None  # ISO timestamp
    plan: PlanPhase
    plan_completed_at: str | None  # ISO timestamp
    execute: ExecutePhase
    execute_completed_at: str | None  # ISO timestamp
    verify: VerifyPhase
    verify_completed_at: str | None  # ISO timestamp


class VerificationState(TypedDict, total=False):
    """Current verification state (consolidated for routing and access).

    This provides a single source of truth for verification status,
    avoiding duplication across phases.verify and top-level fields.
    """
    current_score: float | None  # Latest verification score
    current_passed: bool | None  # Whether latest verification passed
    target_score: float  # Target score needed to pass (default 0.8)
    current_report: dict[str, Any] | None  # Latest VerificationReportV2 as dict
    gaps: list[str]  # Current unmet criteria


class BuildHistory(TypedDict, total=False):
    """Historical data accumulated across iterations."""
    artefacts: list[dict[str, Any]]  # Per-iteration results (iteration, score, passed)
    verification_reports: list[dict[str, Any]]  # All verification reports
    lessons_learned: list[dict[str, Any]]  # Lessons from LEARN phase
    messages: list[dict[str, Any]]  # Phase messages with timestamps


class BestResult(TypedDict, total=False):
    """Best result tracking for early-stop scenarios."""
    iteration: int  # Iteration that produced best result
    score: float  # Best score achieved
    passed: bool  # Whether that iteration passed
    achieved_at: str | None  # ISO timestamp when best result was achieved
    verification_report_ref: str | None  # Reference to report in history (e.g., index or ID)


class ClarificationQA(TypedDict, total=False):
    """A single clarification question-answer pair."""
    question: str
    response: str
    asked_at: str  # ISO timestamp
    answered_at: str | None  # ISO timestamp


class ClarificationState(TypedDict, total=False):
    """Clarification handling (user interaction during build)."""
    needed: bool  # Whether clarification is needed
    question: str | None  # Current question to ask user
    context: str | None  # Raw context indicating need
    awaiting: bool  # Currently waiting for response
    response: str | None  # User's response to current question
    history: list[ClarificationQA]  # Previous Q&As for context


class ConcurrentBuildInfo(TypedDict, total=False):
    """Tracks a single concurrent build."""
    work_item_id: str
    worktree_path: str
    branch_name: str
    status: str  # "building" | "completed" | "failed"
    build_result: dict[str, Any] | None
    started_at: str
    completed_at: str | None
    error_message: str | None


class MergeQueueItem(TypedDict, total=False):
    """Tracks a pending merge operation."""
    work_item_id: str
    worktree_path: str
    branch_name: str
    merge_status: str  # "pending" | "merged" | "conflict" | "failed"
    merge_commit_hash: str | None
    conflict_files: list[str]
    merge_attempt_count: int
    error_message: str | None


class InnerLoopState(TypedDict, total=False):
    """Inner loop (OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN) state.

    Clean nested structure with phase objects. Each phase has its own
    namespace to avoid field collision and improve readability.

    Structure:
    - build_*: Top-level build control (uses BuildPhase enum)
    - task: What we're building (description, user_request, target_capabilities)
    - iteration: Iteration tracking (single source of truth for iteration number)
    - spec: Spec document reference (content read from file)
    - phases: Phase-grouped outputs with timestamps
    - verification: Consolidated verification state (single source of truth)
    - history: Accumulated data across iterations
    - best: Best result tracking with timestamps
    - clarification: User interaction state with history

    Canonical locations for build metadata:
    - build_id: build_settings["build_id"] (BuildSettings is single source of truth)
    - user_request: build_settings["user_request"] (BuildSettings is single source of truth)
    - target_capabilities: task["target_capabilities"] (TaskInfo is single source of truth)
    """
    # Build control (top-level for quick access)
    build_in_progress: bool  # True while building, blocks new builds
    build_started_at: str | None  # ISO timestamp when build started
    build_phase: str | None  # Current phase (use BuildPhase enum values)
    task_id: str | None  # Unique task identifier
    build_task_description: str | None  # Backward-compatible top-level build description
    build_messages: list[dict[str, Any]]  # Build progress messages
    build_cancelled: bool  # Set when a running build is cancelled
    build_error: str | None  # Set when a build fails with an error message
    integration_branch: str | None  # Target integration branch for merges/PRs
    worktrees_to_cleanup: list[str]  # Best-effort cleanup list for git worktrees

    # Nested objects for organization
    task: TaskInfo  # What we're building (description + target_capabilities)
    iteration: IterationInfo  # Iteration tracking (iteration.current is the source of truth)
    spec: SpecInfo  # Spec document reference
    phases: BuildPhases  # Phase-grouped outputs with timestamps
    history: BuildHistory  # Historical data
    best: BestResult  # Best result tracking with timestamps
    clarification: ClarificationState  # User interaction with history
    awaiting_clarification: bool  # Top-level compatibility flag
    clarification_response: str | None  # Top-level compatibility field

    # Consolidated verification state (single source of truth)
    verification: VerificationState  # All verification data in one place

    # Stop conditions
    should_iterate: bool  # Whether to continue iterating
    iteration_stop_reason: str | None  # Why iteration stopped (use IterationStopReason enum values)

    # Learning summary (from LEARN phase, consumed by OBSERVE for ideal state refinement)
    learning_summary: dict[str, Any] | None  # Latest learning summary for observe node

    # Concurrent build tracking
    concurrent_builds: list[ConcurrentBuildInfo]  # Track multiple concurrent builds
    merge_queue: list[MergeQueueItem]  # Sequential merge queue after builds
    max_concurrency: int  # Max concurrent builds from config
    merged_items: list[str]  # IDs of successfully merged work items
    failed_merge_items: list[str]  # IDs of work items that failed to merge


class ExecutionState(TypedDict, total=False):
    """Execution phase state."""
    pending_tool_calls: list[dict[str, Any]]  # Tools pending execution
    work_item_completed: bool  # Whether work item is complete
    execution_result: dict[str, Any] | None  # BL-006: Structured execution result for verify


# TypedDict for LangGraph state (allows partial updates)
# Composed from focused substates for better organization
class ProgenitorState(
    ConversationState,
    WorkState,
    SchedulingState,
    BackgroundJobState,
    LoopControlState,
    InnerLoopState,
    ExecutionState,
    total=False
):
    """Main state for the LangGraph.

    Composed from focused substates:
    - ConversationState: messages, input, responses
    - WorkState: backlog, work items, decisions
    - SchedulingState: schedule, agents, registry
    - LoopControlState: cycle control, metadata
    - InnerLoopState: observe/think/plan/build phases, verification, learning
    - ExecutionState: tool execution, completion

    All fields are optional (total=False) to allow partial updates.
    """
    pass  # All fields inherited from substates
