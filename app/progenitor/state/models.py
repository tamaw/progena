"""State models for Progenitor using Pydantic v2."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any, TypedDict, Union
import uuid
from pydantic import BaseModel, Field, field_validator

from ..runtime.time_utils import utcnow


def generate_run_id() -> str:
    """Generate a unique run ID for task tracking."""
    return f"run_{uuid.uuid4().hex[:12]}"


class WorkItemStatus(str, Enum):
    """Status of a work item in the backlog."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class WorkItemType(str, Enum):
    """Type of work item."""
    BUILD_AGENT = "build_agent"
    BUILD_MCP = "build_mcp"
    UPGRADE = "upgrade"
    A2A_CALL = "a2a_call"
    MCP_CALL = "mcp_call"
    SCHEDULED = "scheduled"
    DIRECT_ANSWER = "direct_answer"


class DecisionAction(str, Enum):
    """Actions that can be decided."""
    ANSWER_DIRECTLY = "answer_directly"
    DELEGATE_A2A = "delegate_a2a"
    USE_MCP = "use_mcp"
    BUILD_AGENT = "build_agent"
    BUILD_MCP = "build_mcp"
    UPGRADE = "upgrade"
    MIXED = "mixed"
    SCHEDULE = "schedule"


# Patterns for secret redaction - (pattern, replacement_or_handler)
SECRET_PATTERNS = [
    (r'(?i)(api[_-]?key|apikey)["\']?\s*[:=]\s*["\']?([^"\',\s]+)', r'\1=[REDACTED]'),
    (r'(?i)(secret|password|passwd|pwd|token|credential|auth)["\']?\s*[:=]\s*["\']?([^"\',\s]+)', r'\1=[REDACTED]'),
    (r'(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*', r'\1[REDACTED]'),
    (r'sk-[A-Za-z0-9]{20,}', '[OPENAI_KEY_REDACTED]'),  # OpenAI keys
    (r'sk-proj-[A-Za-z0-9\-_]{20,}', '[OPENAI_PROJECT_KEY_REDACTED]'),  # OpenAI project keys
    (r'ghp_[A-Za-z0-9]{36,}', '[GITHUB_TOKEN_REDACTED]'),  # GitHub personal access tokens
    (r'gho_[A-Za-z0-9]{36,}', '[GITHUB_OAUTH_REDACTED]'),  # GitHub OAuth tokens
]


def redact_secrets(text: str) -> str:
    """Redact sensitive information from text before logging."""
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
    target_agent: Optional[str] = None
    target_tool: Optional[str] = None
    worktree_path: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    retry_count: int = 0
    max_retries: int = 3
    error_message: Optional[str] = None
    result: Optional[dict[str, Any]] = None

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
                return WorkItemType.DIRECT_ANSWER
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
                return WorkItemStatus.PENDING
        return WorkItemStatus.PENDING

    @property
    def status_value(self) -> str:
        """Get status as string value for comparisons."""
        return self.status if isinstance(self.status, str) else self.status.value

    @property
    def item_type_value(self) -> str:
        """Get item_type as string value for comparisons."""
        return self.item_type if isinstance(self.item_type, str) else self.item_type.value

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
            WorkItemType.UPGRADE.value
        )

    def mark_in_progress(self) -> None:
        """Mark the item as in progress."""
        self.status = WorkItemStatus.IN_PROGRESS
        self.updated_at = utcnow()

    def mark_completed(self, result: Optional[dict[str, Any]] = None) -> None:
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
    direct_response: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class ScheduleEntry(BaseModel):
    """A scheduled task entry.

    Supports both recurring tasks (via cron_expression or interval_minutes)
    and one-off tasks (via run_at). One-off tasks are automatically disabled
    after execution.
    """
    model_config = {"use_enum_values": True}

    id: str
    description: str
    cron_expression: Optional[str] = None  # For recurring (e.g., "0 9 * * *")
    interval_minutes: Optional[int] = None  # Simple recurring interval
    run_at: Optional[datetime] = None  # For one-off
    enabled: bool = True
    action_type: DecisionAction
    action_config: dict[str, Any] = Field(default_factory=dict)
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class AgentRegistryEntry(BaseModel):
    """An agent in the registry."""
    name: str
    kind: str = "a2a_agent"  # a2a_agent | mcp_server
    version: str = "0.1.0"
    description: str
    capabilities: list[str] = Field(default_factory=list)
    entrypoint: str  # File path or command
    endpoint: Optional[str] = None  # For HTTP-based agents
    transport: str = "stdio"  # stdio | http
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


# TypedDict for LangGraph state (allows partial updates)
class ProgenitorState(TypedDict, total=False):
    """Main state for the LangGraph."""
    # Conversation
    messages: list[ConversationMessage]
    current_input: Optional[str]
    pending_response: Optional[str]

    # Backlog and work
    backlog: list[WorkItem]
    current_work_item: Optional[WorkItem]

    # Decision state
    current_decision: Optional[Decision]

    # Scheduling
    schedule: list[ScheduleEntry]
    current_time: Optional[datetime]
    due_items: list[str]  # IDs of due schedule entries

    # Registry
    agents: list[AgentRegistryEntry]

    # Validation
    last_validation_result: Optional[dict[str, Any]]

    # Loop control
    cycle_count: int
    actions_this_cycle: int
    shutdown_requested: bool

    # Metadata
    last_commit_time: Optional[datetime]
    run_id: str

    # Ideal State Loop (loop.spec.md) - NEW
    ideal_state: Optional[Any]  # IdealState object
    current_iteration: int  # Inner loop iteration count
    iteration_start_time: float  # Timestamp when iteration started

    # Inner Loop Phases
    current_approaches: list[dict[str, Any]]  # From THINK phase
    thinking_output: Optional[str]  # THINK phase output
    last_iteration_output: Optional[Any]  # Output from BUILD phase

    # Verification
    last_verification_report: Optional[Any]  # VerificationReport object
    verification_passed: bool
    verification_score: float

    # Learning
    lessons_learned: list[dict[str, Any]]  # Accumulated lessons
    current_lesson: Optional[dict[str, Any]]  # Current iteration lesson

    # Stop condition tracking
    should_iterate: bool  # Whether to continue iterating
    iteration_stop_reason: Optional[str]  # Why iteration stopped

    # Task State (loop.spec.md persisted state)
    task_id: Optional[str]  # Unique task identifier
    artefacts: list[dict[str, Any]]  # Produced artefacts per iteration
    best_artefact: Optional[dict[str, Any]]  # Best artefact by verification score
    best_artefact_score: float  # Score of best artefact
    verification_reports: list[dict[str, Any]]  # All verification reports

    # Execute Phase (loop.spec.md BUILD → EXECUTE → VERIFY)
    execution_result: Optional[Any]  # Result from EXECUTE phase
    execution_completed: bool  # Whether execution finished
    pending_tool_calls: list[dict[str, Any]]  # Tools pending execution
    work_item_completed: bool  # Whether work item is complete
