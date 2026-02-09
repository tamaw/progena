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
    BUILD_TOOL = "build_tool"  # Build FastMCP tool
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
    # Build-specific fields (populated when action is build_agent, build_tool, or upgrade)
    project_name: Optional[str] = Field(
        default=None,
        description="For build actions: the agent/project name in snake_case (e.g., 'weather_agent'). "
        "Should be a concise, descriptive name based on what the user wants to build. "
        "Do NOT include filler words like 'for_me', 'please', 'a', 'the'."
    )
    build_type: Optional[str] = Field(
        default=None,
        description="For build actions: 'agent' for A2A agents, 'mcp' for MCP servers/tools"
    )
    build_description: Optional[str] = Field(
        default=None,
        description="For build actions: a clear, refined description of what the user wants to build. "
        "This should capture the core intent and requirements from the user's request."
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
            "mcp": "build_tool",  # Map mcp to build_tool
            "tool": "build_tool",  # Map tool to build_tool
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
            "build_tool": "mcp",
            "mcp_server": "mcp",
            "tool": "mcp",
            "mcp": "mcp",
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


# ============================================================================
# Ideal State Response Models (structured LLM output)
# ============================================================================


class SuccessCriterionSpec(BaseModel):
    """LLM-generated success criterion specification."""

    id: str = Field(default="criterion_1", description="Unique identifier like 'criterion_1'")
    description: str = Field(default="Task completed", description="What success looks like")
    criterion_type: str = Field(
        default="functional",
        description="One of: functional, quality, performance, security, maintainability, user_experience"
    )
    rank: int = Field(default=1, description="Priority rank (1=highest)")
    measurement: str = Field(default="Manual verification", description="How to verify this criterion")
    target_score: float = Field(default=0.8, description="Minimum score to pass (0.0 to 1.0)")
    weight: float = Field(default=1.0, description="Weight in overall score calculation")

    @field_validator('target_score', mode='before')
    @classmethod
    def clamp_target_score(cls, v):
        if v is None:
            return 0.8
        try:
            return max(0.0, min(1.0, float(v)))
        except (ValueError, TypeError):
            return 0.8

    @field_validator('weight', mode='before')
    @classmethod
    def clamp_weight(cls, v):
        if v is None:
            return 1.0
        try:
            return max(0.0, float(v))
        except (ValueError, TypeError):
            return 1.0


class AntiCriterionSpec(BaseModel):
    """LLM-generated anti-criterion specification."""

    id: str = Field(default="anti_1", description="Unique identifier")
    description: str = Field(default="", description="What to avoid")
    severity: float = Field(default=1.0, description="Penalty multiplier (0.0 to 2.0)")
    detection: str = Field(default="Manual check", description="How to detect violations")

    @field_validator('severity', mode='before')
    @classmethod
    def clamp_severity(cls, v):
        if v is None:
            return 1.0
        try:
            return max(0.0, min(2.0, float(v)))
        except (ValueError, TypeError):
            return 1.0


class QualityBarSpec(BaseModel):
    """LLM-generated quality bar specification."""

    description: str = Field(default="High quality output", description="What quality means for this task")
    examples: list[str] = Field(default_factory=list, description="Examples of quality")
    anti_examples: list[str] = Field(default_factory=list, description="Examples of what is NOT quality")


class VerificationMethodSpec(BaseModel):
    """LLM-generated verification method specification."""

    method_type: str = Field(
        default="heuristic",
        description="One of: test, heuristic, llm_judge, user_feedback"
    )
    description: str = Field(default="", description="What this verification checks")
    automated: bool = Field(default=True, description="Whether this can run automatically")


class StopConditionSpec(BaseModel):
    """LLM-generated stop condition specification."""

    condition_type: str = Field(
        default="max_iterations",
        description="One of: min_score, max_iterations, time_limit, tool_budget"
    )
    threshold: float = Field(default=5.0, description="Threshold value for the condition")

    @field_validator('threshold', mode='before')
    @classmethod
    def validate_threshold(cls, v):
        if v is None:
            return 5.0
        try:
            return max(0.0, float(v))
        except (ValueError, TypeError):
            return 5.0


class TightenedCriterionSpec(BaseModel):
    """Specification for tightening an existing criterion."""

    criterion_id: str = Field(description="ID of the existing criterion to tighten")
    updated_measurement: str = Field(default="", description="New, more specific measurement approach")
    updated_target_score: float = Field(default=0.8, description="New, higher target score (0.0 to 1.0)")
    reason: str = Field(default="", description="Why this criterion was tightened")

    @field_validator('updated_target_score', mode='before')
    @classmethod
    def clamp_score(cls, v):
        if v is None:
            return 0.8
        try:
            return max(0.0, min(1.0, float(v)))
        except (ValueError, TypeError):
            return 0.8


class CriteriaIssue(BaseModel):
    """An issue found during criteria validation."""

    criterion_id: str = Field(description="ID of the problematic criterion")
    issue_type: str = Field(
        default="vague",
        description="One of: vague, unmeasurable, conflicting, redundant, missing_detection"
    )
    description: str = Field(default="", description="Description of the issue")
    suggested_fix: str = Field(default="", description="How to fix this issue")


class IdealStateExtractionResponse(BaseModel):
    """Structured LLM response for extracting ideal state from a task."""

    success_criteria: list[SuccessCriterionSpec] = Field(
        default_factory=list,
        description="Ranked, measurable success criteria"
    )
    anti_criteria: list[AntiCriterionSpec] = Field(
        default_factory=list,
        description="Things to actively avoid and penalize"
    )
    quality_bar: QualityBarSpec = Field(
        default_factory=QualityBarSpec,
        description="Quality standard definition"
    )
    verification_methods: list[VerificationMethodSpec] = Field(
        default_factory=list,
        description="How to verify success"
    )
    stop_conditions: list[StopConditionSpec] = Field(
        default_factory=list,
        description="When to stop iterating"
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Hard constraints for this task"
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Working assumptions"
    )


class IdealStateRefinementResponse(BaseModel):
    """Structured LLM response for refining ideal state after an iteration."""

    new_success_criteria: list[SuccessCriterionSpec] = Field(
        default_factory=list,
        description="New criteria discovered from iteration results"
    )
    tightened_criteria: list[TightenedCriterionSpec] = Field(
        default_factory=list,
        description="Existing criteria that should be tightened"
    )
    new_anti_criteria: list[AntiCriterionSpec] = Field(
        default_factory=list,
        description="New anti-criteria from observed issues"
    )
    updated_quality_description: str = Field(
        default="",
        description="Updated quality bar description, empty string if no change needed"
    )
    updated_quality_examples: list[str] = Field(
        default_factory=list,
        description="Updated quality examples, empty list if no change"
    )
    updated_quality_anti_examples: list[str] = Field(
        default_factory=list,
        description="Updated anti-examples, empty list if no change"
    )
    reasoning: str = Field(default="", description="Why these refinements are needed")


class IdealStateClarificationResponse(BaseModel):
    """Structured LLM response for incorporating user clarification."""

    updated_task_description: str = Field(
        default="",
        description="Enhanced task description incorporating clarification, empty if no change"
    )
    new_success_criteria: list[SuccessCriterionSpec] = Field(
        default_factory=list,
        description="New criteria from clarification"
    )
    new_anti_criteria: list[AntiCriterionSpec] = Field(
        default_factory=list,
        description="New anti-criteria from clarification"
    )
    additional_constraints: list[str] = Field(
        default_factory=list,
        description="New constraints from clarification"
    )
    reasoning: str = Field(default="", description="How the clarification changes the task")


class AntiCriteriaExtractionResponse(BaseModel):
    """Structured LLM response for extracting anti-criteria from violations."""

    anti_criteria: list[AntiCriterionSpec] = Field(
        default_factory=list,
        description="Anti-criteria derived from observed violations"
    )
    reasoning: str = Field(default="", description="Pattern analysis of violations")


class CriteriaValidationResponse(BaseModel):
    """Structured LLM response for validating criteria quality."""

    is_valid: bool = Field(default=True, description="Whether the criteria set is valid overall")
    issues: list[CriteriaIssue] = Field(
        default_factory=list,
        description="Issues found with criteria"
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="General suggestions for improvement"
    )
    overall_quality_score: float = Field(
        default=0.5,
        description="Overall quality score of the criteria set (0.0 to 1.0)"
    )

    @field_validator('overall_quality_score', mode='before')
    @classmethod
    def clamp_quality_score(cls, v):
        if v is None:
            return 0.5
        try:
            return max(0.0, min(1.0, float(v)))
        except (ValueError, TypeError):
            return 0.5


class VerificationMethodSuggestionResponse(BaseModel):
    """Structured LLM response for suggesting verification methods."""

    verification_methods: list[VerificationMethodSpec] = Field(
        default_factory=list,
        description="Suggested verification methods for the criteria"
    )
    reasoning: str = Field(default="", description="Why these methods were chosen")


# ============================================================================
# Validation Severity Assessment (LLM structured output)
# ============================================================================


class FindingSeveritySpec(BaseModel):
    """LLM-assessed severity for a single validation finding."""

    finding_id: str = Field(description="The finding_id being assessed")
    severity: str = Field(
        default="medium",
        description="Assessed severity: low, medium, high, critical"
    )
    reasoning: str = Field(default="", description="Why this severity was assigned")
    suggested_fix: str = Field(default="", description="Suggested remediation")
    is_root_cause: bool = Field(
        default=False,
        description="Whether this finding is a root cause of other issues"
    )

    @field_validator('severity', mode='before')
    @classmethod
    def validate_severity(cls, v):
        valid = {"low", "medium", "high", "critical"}
        if isinstance(v, str) and v.lower().strip() in valid:
            return v.lower().strip()
        return "medium"


class ValidationSeverityResponse(BaseModel):
    """Structured LLM response for assessing validation finding severities."""

    assessments: list[FindingSeveritySpec] = Field(
        default_factory=list,
        description="Severity assessment for each finding"
    )
    overall_assessment: str = Field(
        default="",
        description="Overall assessment of the validation findings"
    )
    blocking_finding_ids: list[str] = Field(
        default_factory=list,
        description="IDs of findings that should block progress"
    )


# ============================================================================
# Learning Phase Response Models (LEARN node structured output)
# ============================================================================


class LessonConfidence(BaseModel):
    """A single lesson with confidence scoring."""

    description: str = Field(description="The lesson learned")
    confidence: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Confidence in this lesson (0.0-1.0)"
    )
    evidence: str = Field(
        default="",
        description="Concrete evidence supporting this lesson"
    )

    @field_validator('confidence', mode='before')
    @classmethod
    def clamp_confidence(cls, v):
        if v is None:
            return 0.7
        try:
            return max(0.0, min(1.0, float(v)))
        except (ValueError, TypeError):
            return 0.7


class AdjustmentPriority(BaseModel):
    """A prioritized adjustment recommendation."""

    description: str = Field(description="The adjustment to make")
    priority: str = Field(
        default="medium",
        description="Priority level: high, medium, or low"
    )
    impact_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Expected impact of making this adjustment (0.0-1.0)"
    )
    category: str = Field(
        default="general",
        description="Category: architecture, implementation, testing, configuration, general"
    )

    @field_validator('priority', mode='before')
    @classmethod
    def validate_priority(cls, v):
        valid = {"high", "medium", "low"}
        if isinstance(v, str) and v.lower().strip() in valid:
            return v.lower().strip()
        return "medium"

    @field_validator('impact_score', mode='before')
    @classmethod
    def clamp_impact(cls, v):
        if v is None:
            return 0.5
        try:
            return max(0.0, min(1.0, float(v)))
        except (ValueError, TypeError):
            return 0.5

    @field_validator('category', mode='before')
    @classmethod
    def validate_category(cls, v):
        valid = {"architecture", "implementation", "testing", "configuration", "general"}
        if isinstance(v, str) and v.lower().strip() in valid:
            return v.lower().strip()
        return "general"


class PatternDetection(BaseModel):
    """Detected pattern across iterations."""

    pattern_type: str = Field(
        default="recurring_issue",
        description="Type: recurring_issue, improvement_trend, stagnation_signal, regression_pattern"
    )
    description: str = Field(description="Description of the pattern")
    affected_iterations: list[int] = Field(
        default_factory=list,
        description="Which iterations this pattern spans"
    )
    recommended_action: str = Field(
        default="",
        description="What to do about this pattern"
    )

    @field_validator('pattern_type', mode='before')
    @classmethod
    def validate_pattern_type(cls, v):
        valid = {"recurring_issue", "improvement_trend", "stagnation_signal", "regression_pattern"}
        if isinstance(v, str) and v.lower().strip() in valid:
            return v.lower().strip()
        return "recurring_issue"


class ActionOutcomeCorrelation(BaseModel):
    """Correlation between a planned action and its observed outcome."""

    action: str = Field(description="The planned action from the experiment plan")
    outcome: str = Field(description="The observed outcome from verification")
    correlation: str = Field(
        default="neutral",
        description="Correlation: positive, negative, neutral"
    )
    explanation: str = Field(
        default="",
        description="Why this correlation exists"
    )

    @field_validator('correlation', mode='before')
    @classmethod
    def validate_correlation(cls, v):
        valid = {"positive", "negative", "neutral"}
        if isinstance(v, str) and v.lower().strip() in valid:
            return v.lower().strip()
        return "neutral"


class LearningResult(BaseModel):
    """Structured LLM response for the LEARN phase analysis."""

    # Core fields (Phase 1)
    plan_achieved: bool = Field(
        default=False,
        description="Whether the experiment plan was achieved this iteration"
    )
    plan_achievement_reasoning: str = Field(
        default="",
        description="Why the plan was or was not achieved"
    )
    ideal_state_progress: str = Field(
        default="unknown",
        description="Progress toward ideal state: improving, stagnating, or regressing"
    )
    ideal_state_reasoning: str = Field(
        default="",
        description="Assessment of progress toward the ideal state"
    )
    what_worked: list[str] = Field(
        default_factory=list,
        description="List of things that worked well this iteration"
    )
    what_failed: list[str] = Field(
        default_factory=list,
        description="List of things that failed or need improvement"
    )
    root_cause: str = Field(
        default="",
        description="Primary root cause if there was a failure"
    )
    adjustments: list[str] = Field(
        default_factory=list,
        description="Specific actionable adjustments for next iteration"
    )
    insights: list[str] = Field(
        default_factory=list,
        description="Key insights and learnings from this iteration"
    )

    # Enhanced analysis fields (Phase 2)
    detailed_lessons: list[LessonConfidence] = Field(
        default_factory=list,
        description="Lessons with confidence scoring"
    )
    prioritized_adjustments: list[AdjustmentPriority] = Field(
        default_factory=list,
        description="Adjustments ranked by priority and impact"
    )
    patterns_detected: list[PatternDetection] = Field(
        default_factory=list,
        description="Patterns observed across iteration history"
    )
    action_outcome_correlations: list[ActionOutcomeCorrelation] = Field(
        default_factory=list,
        description="Correlations between planned actions and observed outcomes"
    )

    reasoning_trace: Optional[str] = Field(
        default=None,
        description="Reasoning trace from the LLM, if available"
    )

    @field_validator('ideal_state_progress', mode='before')
    @classmethod
    def validate_progress(cls, v):
        valid = {"improving", "stagnating", "regressing", "unknown"}
        if isinstance(v, str) and v.lower().strip() in valid:
            return v.lower().strip()
        return "unknown"


# ============================================================================
# THINK phase response models (BL-003)
# ============================================================================


class ThinkTradeOffResponse(BaseModel):
    """LLM response for a single trade-off in an approach."""

    aspect: str = Field(
        default="General",
        description="What aspect this trade-off affects (e.g., Speed, Complexity, Coverage)"
    )
    positive: str = Field(
        default="",
        description="What we gain from this approach"
    )
    negative: str = Field(
        default="",
        description="What we give up or risk"
    )
    severity: str = Field(
        default="medium",
        description="Severity of the trade-off: low, medium, or high"
    )

    @field_validator('severity', mode='before')
    @classmethod
    def validate_severity(cls, v):
        valid = {"low", "medium", "high"}
        if isinstance(v, str) and v.lower().strip() in valid:
            return v.lower().strip()
        return "medium"


class ThinkRiskResponse(BaseModel):
    """LLM response for a single risk in an approach."""

    description: str = Field(
        default="",
        description="What could go wrong"
    )
    likelihood: str = Field(
        default="medium",
        description="Likelihood of this risk: low, medium, or high"
    )
    impact: str = Field(
        default="medium",
        description="Impact if this risk materializes: low, medium, or high"
    )

    @field_validator('likelihood', mode='before')
    @classmethod
    def validate_likelihood(cls, v):
        valid = {"low", "medium", "high"}
        if isinstance(v, str) and v.lower().strip() in valid:
            return v.lower().strip()
        return "medium"

    @field_validator('impact', mode='before')
    @classmethod
    def validate_impact(cls, v):
        valid = {"low", "medium", "high"}
        if isinstance(v, str) and v.lower().strip() in valid:
            return v.lower().strip()
        return "medium"


class ThinkApproachResponse(BaseModel):
    """LLM response for a single approach."""

    name: str = Field(
        default="",
        description="Short descriptive name for this approach (e.g., 'Direct Implementation')"
    )
    strategy: str = Field(
        default="",
        description="Core strategy or technique to use"
    )
    implementation_steps: list[str] = Field(
        default_factory=list,
        description="Concrete ordered steps to implement this approach"
    )
    trade_offs: list[ThinkTradeOffResponse] = Field(
        default_factory=list,
        description="Explicit trade-offs of this approach"
    )
    risks: list[ThinkRiskResponse] = Field(
        default_factory=list,
        description="Known risks with likelihood and impact"
    )
    expected_success_probability: float = Field(
        default=0.5,
        description="Estimated probability of success (0.0 to 1.0)"
    )
    addresses_gaps: list[str] = Field(
        default_factory=list,
        description="Which gaps from the previous iteration this approach addresses"
    )

    @field_validator('expected_success_probability', mode='before')
    @classmethod
    def validate_probability(cls, v):
        try:
            val = float(v)
            return max(0.0, min(1.0, val))
        except (ValueError, TypeError):
            return 0.5


class ThinkResponse(BaseModel):
    """Structured LLM response for the THINK phase.

    Contains 2-3 distinct approaches with trade-offs and risks.
    Used with structured output (json_schema response format).
    """

    approaches: list[ThinkApproachResponse] = Field(
        default_factory=list,
        description="2-3 distinct approaches to reach the ideal state"
    )
    recommended_approach_index: int = Field(
        default=0,
        description="0-based index of the recommended approach in the approaches list"
    )
    recommendation_reasoning: str = Field(
        default="",
        description="Why the recommended approach is preferred over others"
    )

    reasoning_trace: Optional[str] = Field(
        default=None,
        description="Reasoning trace from the LLM, if available"
    )

    @field_validator('recommended_approach_index', mode='before')
    @classmethod
    def validate_index(cls, v):
        try:
            return max(0, int(v))
        except (ValueError, TypeError):
            return 0


# ============================================================================
# Validation helpers
# ============================================================================


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


def validate_learning_result(data: dict[str, Any]) -> LearningResult:
    """Validate and parse a learning result from LLM."""
    return LearningResult.model_validate(data)
