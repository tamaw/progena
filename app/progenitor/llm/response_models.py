"""
Pydantic models for LLM response validation.

These models ensure that LLM responses are properly structured
and validated before being used in the application.
"""

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


class DecisionAction(str, Enum):
    """Valid actions for the decide endpoint."""
    ANSWER_DIRECTLY = "answer_directly"
    ANSWER = "answer"  # Alias for answer_directly
    DELEGATE_A2A = "delegate_a2a"
    USE_MCP = "use_mcp"
    BUILD_AGENT = "build_agent"
    BUILD_MCP = "build_mcp"
    UPGRADE = "upgrade"
    MIXED = "mixed"
    # Note: SCHEDULE removed - scheduling is handled via system tool fast-path


class DecisionResponse(BaseModel):
    """Validated response from the decide endpoint."""

    action: str = Field(
        default="answer_directly",
        description="The action to take for this request"
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Confidence score (0.0-1.0) that this is the correct action. BUILD actions require >= 0.95"
    )
    reasoning: str = Field(
        default="",
        description="Reasoning behind the decision"
    )
    requires_planning: bool = Field(
        default=False,
        description="Whether this task requires planning"
    )
    target_capabilities: list[str] = Field(
        default_factory=list,
        description="List of capabilities needed for this task"
    )
    user_response: Optional[str] = Field(
        default=None,
        description="A helpful, specific response to show the user about what action will be taken"
    )
    direct_response: Optional[str] = Field(
        default=None,
        description="Direct response content if action is answer_directly (deprecated, use user_response)"
    )

    @field_validator('confidence', mode='before')
    @classmethod
    def validate_confidence(cls, v):
        """Ensure confidence is a valid float between 0 and 1."""
        if v is None:
            return 0.5
        try:
            val = float(v)
            return max(0.0, min(1.0, val))
        except (ValueError, TypeError):
            return 0.5

    @field_validator('action', mode='before')
    @classmethod
    def validate_action(cls, v):
        """Normalize and validate action value."""
        if not v:
            return "answer_directly"

        v_lower = str(v).lower().strip()

        # Normalize common variations
        if v_lower in ("answer", "direct", "direct_answer"):
            return "answer_directly"

        # Check if it's a valid action
        valid_actions = {e.value for e in DecisionAction}
        if v_lower not in valid_actions:
            # Fall back to answer_directly for unknown actions
            return "answer_directly"

        return v_lower

    @field_validator('reasoning', mode='before')
    @classmethod
    def validate_reasoning(cls, v):
        """Ensure reasoning is a string."""
        if v is None:
            return ""
        return str(v)

    @field_validator('target_capabilities', mode='before')
    @classmethod
    def validate_capabilities(cls, v):
        """Ensure capabilities is a list of strings."""
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            return [str(c) for c in v]
        return []


class WorkItemSpec(BaseModel):
    """A work item specification from the planner."""

    model_config = {"populate_by_name": True}

    id: str = Field(default="", description="Work item ID")
    item_type: str = Field(
        default="direct_answer",
        alias="type",
        description="Type of work item"
    )
    description: str = Field(default="", description="Work item description")
    target_agent: Optional[str] = Field(default=None, description="Target agent for delegation")
    target_tool: Optional[str] = Field(default=None, description="Target MCP tool")
    priority: int = Field(default=0, description="Priority (higher = more urgent)")
    context: dict[str, Any] = Field(default_factory=dict, description="Additional context")

    @field_validator('description', mode='before')
    @classmethod
    def validate_description(cls, v):
        """Ensure description is a string."""
        if v is None:
            return ""
        if isinstance(v, list):
            return " ".join(str(item) for item in v)
        return str(v)

    @field_validator('item_type', mode='before')
    @classmethod
    def validate_item_type(cls, v):
        """Normalize work item type."""
        if not v:
            return "direct_answer"

        v_str = str(v).lower().strip()

        # Map common variations
        type_mapping = {
            "build": "build_agent",
            "agent": "build_agent",
            "mcp": "build_mcp",
            "tool": "mcp_call",
            "a2a": "a2a_call",
            "schedule": "scheduled",
            "answer": "direct_answer",
            "direct": "direct_answer",
        }

        return type_mapping.get(v_str, v_str)


class PlannerResponse(BaseModel):
    """Validated response from the planner endpoint."""

    work_items: list[WorkItemSpec] = Field(
        default_factory=list,
        description="List of work items to execute"
    )
    summary: str = Field(
        default="",
        description="Summary of the plan"
    )

    @field_validator('work_items', mode='before')
    @classmethod
    def validate_work_items(cls, v):
        """Ensure work_items is a list."""
        if v is None:
            return []
        if isinstance(v, dict):
            # Single item wrapped
            return [v]
        if not isinstance(v, list):
            return []
        return v


class BuildSpec(BaseModel):
    """Validated build specification."""

    name: str = Field(default="unnamed", description="Component name")
    description: str = Field(default="", description="Component description")
    kind: str = Field(
        default="agent",
        description="Build type (agent, mcp, tool)"
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="Capabilities this component provides"
    )
    entrypoint: str = Field(
        default="main.py",
        description="Entry point file"
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="Required dependencies"
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Configuration options"
    )

    @field_validator('capabilities', mode='before')
    @classmethod
    def validate_capabilities(cls, v):
        """Ensure capabilities is a list of strings."""
        if v is None:
            return []
        if isinstance(v, dict):
            # LLM sometimes returns {"required": [...], "optional": [...]}
            # Flatten to a single list
            result = []
            if "required" in v and isinstance(v["required"], list):
                result.extend(str(item) for item in v["required"])
            if "optional" in v and isinstance(v["optional"], list):
                result.extend(str(item) for item in v["optional"])
            return result if result else []
        if isinstance(v, str):
            # Comma-separated string
            return [s.strip() for s in v.split(",") if s.strip()]
        if not isinstance(v, list):
            return []
        return [str(item) for item in v]

    @field_validator('name', mode='before')
    @classmethod
    def validate_name(cls, v):
        """Ensure name is valid."""
        if not v:
            return "unnamed"
        # Sanitize name for filesystem safety
        name = str(v).strip()
        # Remove or replace invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        return name or "unnamed"

    @field_validator('kind', mode='before')
    @classmethod
    def validate_kind(cls, v):
        """Normalize build kind."""
        if not v:
            return "agent"

        v_str = str(v).lower().strip()

        kind_mapping = {
            "build_agent": "agent",
            "build_mcp": "mcp",
            "mcp_server": "mcp",
            "tool": "mcp",
            "upgrade": "upgrade",
        }

        return kind_mapping.get(v_str, v_str)


class VerificationResult(BaseModel):
    """Result from verification operations."""

    passed: bool = Field(default=False, description="Whether verification passed")
    score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Verification score (0-1)"
    )
    criteria_scores: dict[str, float] = Field(
        default_factory=dict,
        description="Scores per criterion"
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Identified gaps"
    )
    next_actions: list[str] = Field(
        default_factory=list,
        description="Suggested next actions"
    )
    evidence: str = Field(
        default="",
        description="Evidence or notes"
    )

    @field_validator('score', mode='before')
    @classmethod
    def validate_score(cls, v):
        """Ensure score is in valid range."""
        if v is None:
            return 0.0
        try:
            score = float(v)
            return max(0.0, min(1.0, score))
        except (ValueError, TypeError):
            return 0.0


class IdealStateSpec(BaseModel):
    """Specification for an ideal state from LLM."""

    task_description: str = Field(default="", description="Description of the task")
    success_criteria: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of success criteria"
    )
    anti_criteria: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of anti-criteria (things to avoid)"
    )
    quality_bar: str = Field(
        default="production",
        description="Quality bar level"
    )
    stop_conditions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Conditions to stop iteration"
    )


def validate_decision_response(data: dict[str, Any]) -> DecisionResponse:
    """Validate and parse a decision response from LLM."""
    return DecisionResponse.model_validate(data)


def validate_planner_response(data: dict[str, Any]) -> PlannerResponse:
    """Validate and parse a planner response from LLM."""
    return PlannerResponse.model_validate(data)


def validate_build_spec(data: dict[str, Any]) -> BuildSpec:
    """Validate and parse a build specification from LLM."""
    return BuildSpec.model_validate(data)


def validate_verification_result(data: dict[str, Any]) -> VerificationResult:
    """Validate and parse a verification result."""
    return VerificationResult.model_validate(data)
