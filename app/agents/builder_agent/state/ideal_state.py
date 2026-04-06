"""
Ideal State Models - Core of loop.spec.md outer loop.

Defines the target state with success criteria, anti-criteria, quality bar,
verification methods, and stop conditions. The Ideal State is the primary
control surface for the agent loop.

All LLM interactions use structured output (Pydantic models) for reliable
parsing. Prompts are loaded from data/prompts/ via PromptTemplates.
"""

import inspect
import json
import logging
from datetime import datetime
from enum import StrEnum
from typing import Any, TypeVar, cast

from pydantic import BaseModel, Field

from ..llm.client import _build_json_schema_format
from ..llm.prompts import PromptTemplates
from ..llm.response_models import (
    AntiCriteriaExtractionResponse,
    AntiCriterionSpec,
    CriteriaValidationResponse,
    IdealStateClarificationResponse,
    IdealStateExtractionResponse,
    IdealStateRefinementResponse,
    SuccessCriterionSpec,
    VerificationMethodSuggestionResponse,
)
from ..runtime.time_utils import utcnow

logger = logging.getLogger(__name__)

# Type variable for generic structured LLM call
T = TypeVar("T", bound=BaseModel)


# ============================================================================
# Domain Models
# ============================================================================


class CriterionType(StrEnum):
    """Type of success criterion."""

    FUNCTIONAL = "functional"
    QUALITY = "quality"
    PERFORMANCE = "performance"
    SECURITY = "security"
    MAINTAINABILITY = "maintainability"
    USER_EXPERIENCE = "user_experience"


class SuccessCriterion(BaseModel):
    """A single success criterion with measurement approach."""

    id: str = Field(description="Unique identifier for this criterion")
    description: str = Field(description="What success looks like")
    criterion_type: CriterionType = Field(default=CriterionType.FUNCTIONAL)
    rank: int = Field(default=1, description="Priority rank (1=highest)")
    measurement: str = Field(description="How to verify this criterion")
    target_score: float = Field(default=0.8, description="Minimum score to pass (0-1)")
    weight: float = Field(default=1.0, description="Weight in overall score")


class AntiCriterion(BaseModel):
    """Something to explicitly avoid - actively penalizes violations."""

    id: str = Field(description="Unique identifier")
    description: str = Field(description="What to avoid")
    severity: float = Field(default=1.0, description="Penalty multiplier (0-2)")
    detection: str = Field(description="How to detect violations")


class QualityBar(BaseModel):
    """Defines the quality standard - 'euphoric surprise' for the user."""

    description: str = Field(description="What quality means for this task")
    examples: list[str] = Field(default_factory=list, description="Examples of quality")
    anti_examples: list[str] = Field(
        default_factory=list, description="Examples of what's NOT quality"
    )


class VerificationMethod(BaseModel):
    """How to verify the output meets criteria."""

    method_type: str = Field(description="Type: test, heuristic, llm_judge, user_feedback")
    description: str = Field(description="What this verification checks")
    automated: bool = Field(default=True, description="Can run automatically?")


class StopCondition(BaseModel):
    """When to stop iterating."""

    condition_type: str = Field(
        description="Type: min_score, max_iterations, time_limit, tool_budget"
    )
    threshold: float = Field(description="Threshold value")
    met: bool = Field(default=False, description="Has this condition been met?")


class IdealState(BaseModel):
    """
    The Ideal State - primary control surface for the agent loop.

    This represents the target state we're trying to reach.
    Gets refined continuously in the OBSERVE phase.
    """

    version: int = Field(default=1, description="Version number, increments on refinement")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    # Core components from loop.spec.md
    task_description: str = Field(description="Original task/request")
    success_criteria: list[SuccessCriterion] = Field(
        default_factory=list, description="Ranked, measurable success criteria"
    )
    anti_criteria: list[AntiCriterion] = Field(
        default_factory=list, description="Things to actively avoid/penalize"
    )
    quality_bar: QualityBar = Field(
        default_factory=lambda: QualityBar(description="High quality, correct, maintainable output")
    )
    verification_methods: list[VerificationMethod] = Field(
        default_factory=list, description="How to verify success"
    )
    stop_conditions: list[StopCondition] = Field(
        default_factory=list, description="When to stop iterating"
    )

    # Context
    constraints: list[str] = Field(default_factory=list, description="Hard constraints")
    assumptions: list[str] = Field(default_factory=list, description="Working assumptions")

    def refine(self, refinements: dict[str, Any]) -> "IdealState":
        """Create a refined version of this ideal state."""
        data = self.model_dump()
        data.update(refinements)
        data["version"] = self.version + 1
        data["updated_at"] = utcnow()
        return IdealState(**data)

    def add_success_criterion(self, criterion: SuccessCriterion) -> None:
        """Add a new success criterion."""
        self.success_criteria.append(criterion)
        self.updated_at = utcnow()

    def add_anti_criterion(self, anti_criterion: AntiCriterion) -> None:
        """Add a new anti-criterion."""
        self.anti_criteria.append(anti_criterion)
        self.updated_at = utcnow()

    def check_stop_conditions(
        self,
        current_score: float,
        iteration: int,
        elapsed_time: float,
        tool_calls: int,
    ) -> tuple[bool, list[str], list[str]]:
        """Check if any stop conditions are met (immutable - does NOT mutate state).

        This method is used in routing functions which must NOT mutate state.
        State updates should happen in nodes, not routing conditionals.

        Returns:
            (should_stop, reasons, met_condition_ids) - met_condition_ids can be used
            by nodes to update state if needed.
        """
        reasons = []
        met_condition_ids = []

        for condition in self.stop_conditions:
            if condition.met:
                continue

            condition_met = False

            if condition.condition_type == "min_score":
                if current_score >= condition.threshold:
                    condition_met = True
                    reasons.append(
                        f"Minimum score {condition.threshold} reached: {current_score:.2f}"
                    )

            elif condition.condition_type == "max_iterations":
                if iteration >= condition.threshold:
                    condition_met = True
                    reasons.append(f"Max iterations {condition.threshold} reached")

            elif condition.condition_type == "time_limit":
                if elapsed_time >= condition.threshold:
                    condition_met = True
                    reasons.append(
                        f"Time limit {condition.threshold}s exceeded: {elapsed_time:.1f}s"
                    )

            elif condition.condition_type == "tool_budget" and tool_calls >= condition.threshold:
                condition_met = True
                reasons.append(f"Tool budget {condition.threshold} exhausted: {tool_calls} calls")

            if condition_met:
                met_condition_ids.append(condition.condition_type)

        should_stop = len(reasons) > 0 or any(c.met for c in self.stop_conditions)
        return should_stop, reasons, met_condition_ids

    def mark_conditions_met(self, condition_types: list[str]) -> "IdealState":
        """Create a new IdealState with specified conditions marked as met.

        Returns a new immutable state rather than mutating in place.
        """
        new_conditions = []
        for condition in self.stop_conditions:
            if condition.condition_type in condition_types and not condition.met:
                new_condition = StopCondition(
                    condition_type=condition.condition_type,
                    threshold=condition.threshold,
                    met=True,
                )
                new_conditions.append(new_condition)
            else:
                new_conditions.append(condition)

        return self.model_copy(update={"stop_conditions": new_conditions})


# ============================================================================
# Conversion helpers: LLM response specs → domain models
# ============================================================================


def _parse_criterion_type(value: str) -> CriterionType:
    """Safely parse a criterion type string to enum."""
    try:
        return CriterionType(value)
    except (ValueError, KeyError, TypeError):
        return CriterionType.FUNCTIONAL


def _spec_to_success_criterion(spec: SuccessCriterionSpec) -> SuccessCriterion:
    """Convert an LLM response spec to a domain SuccessCriterion."""
    return SuccessCriterion(
        id=spec.id,
        description=spec.description,
        criterion_type=_parse_criterion_type(spec.criterion_type),
        rank=spec.rank,
        measurement=spec.measurement,
        target_score=spec.target_score,
        weight=spec.weight,
    )


def _spec_to_anti_criterion(spec: AntiCriterionSpec) -> AntiCriterion:
    """Convert an LLM response spec to a domain AntiCriterion."""
    return AntiCriterion(
        id=spec.id,
        description=spec.description,
        severity=spec.severity,
        detection=spec.detection,
    )


# ============================================================================
# IdealStateBuilder - uses structured LLM output
# ============================================================================


class IdealStateBuilder:
    """Builds and refines ideal state using structured LLM outputs.

    All LLM calls use Pydantic response models for guaranteed schema
    compliance. Prompts are loaded from data/prompts/ via PromptTemplates.
    """

    def __init__(self, llm_client):
        """Initialize with an LLM client that supports chat() with response_format."""
        self.llm_client = llm_client
        self.prompts = PromptTemplates()

    def _get_response_format(
        self,
        response_model: type,
        schema_name: str,
    ) -> dict[str, Any]:
        """Get structured output response format, using provider-aware method if available."""
        response_format_fn = getattr(self.llm_client, "_get_json_response_format", None)
        if callable(response_format_fn) and not inspect.iscoroutinefunction(response_format_fn):
            response_format = response_format_fn(response_model, schema_name)
            if not inspect.isawaitable(response_format) and isinstance(response_format, dict):
                return cast(dict[str, Any], response_format)
        return _build_json_schema_format(response_model, schema_name)

    async def _structured_call(
        self,
        prompt: str,
        system_prompt: str,
        response_model: type[T],
        schema_name: str,
        temperature: float = 0.3,
    ) -> T:
        """Make a structured LLM call returning a validated Pydantic model.

        Uses the LLM client's chat() method with response_format for
        guaranteed schema compliance. Falls back gracefully on parse errors.
        """
        if hasattr(self.llm_client, "chat_structured"):
            return await self.llm_client.chat_structured(  # type: ignore[no-any-return]
                messages=[{"role": "user", "content": prompt}],
                response_model=response_model,
                schema_name=schema_name,
                system_prompt=system_prompt,
                temperature=temperature,
            )

        response_format = self._get_response_format(response_model, schema_name)

        # If using json_object fallback (older Azure), add json instruction
        if response_format.get("type") == "json_object":
            system_prompt += "\n\nReturn a single JSON object and nothing else (valid json)."

        response = await self.llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            temperature=temperature,
            response_format=response_format,
        )

        raw_data = json.loads(response)
        return response_model.model_validate(raw_data)

    # ================================================================
    # Core methods: build, refine, clarify
    # ================================================================

    async def build_from_task(
        self,
        task_description: str,
        context: dict[str, Any] | None = None,
    ) -> IdealState:
        """Build an ideal state from a task description using structured LLM output.

        Uses LLM to extract success criteria, anti-criteria, quality bar,
        verification methods, and stop conditions. All via structured output.
        """
        prompt = self.prompts.get_ideal_state_extraction_prompt(task_description, context or {})

        try:
            extraction = await self._structured_call(
                prompt=prompt,
                system_prompt=self.prompts.get("builder_agent/ideal_state_extraction_system.md"),
                response_model=IdealStateExtractionResponse,
                schema_name="ideal_state_extraction",
            )
            return self._extraction_to_ideal_state(extraction, task_description)

        except Exception as e:
            logger.warning(
                "Failed to extract ideal state via structured output: %s. "
                "Task: %s. Falling back to default.",
                str(e),
                task_description[:100] + "..." if len(task_description) > 100 else task_description,
            )
            return self._create_default_ideal_state(task_description)

    async def refine_from_iteration(
        self,
        current_ideal_state: IdealState,
        iteration_results: dict[str, Any],
    ) -> IdealState:
        """Refine ideal state based on iteration results using structured LLM output.

        Called during OBSERVE phase per loop.spec.md:
        - Tighten criteria that were too loose
        - Add anti-criteria for observed issues
        - Update quality bar based on what's achievable
        """
        prompt = self.prompts.get_ideal_state_refinement_prompt(
            current_ideal_state.model_dump_json(indent=2),
            json.dumps(iteration_results, indent=2, default=str),
        )

        try:
            refinement = await self._structured_call(
                prompt=prompt,
                system_prompt=self.prompts.get("builder_agent/ideal_state_refinement_system.md"),
                response_model=IdealStateRefinementResponse,
                schema_name="ideal_state_refinement",
            )
            return self._apply_refinement(current_ideal_state, refinement)

        except Exception as e:
            logger.warning("Failed to refine ideal state: %s. Incrementing version.", e)
            return current_ideal_state.refine({})

    async def refine_with_clarification(
        self,
        current_ideal_state: IdealState,
        clarification_context: dict[str, Any],
    ) -> IdealState:
        """Refine ideal state with user's clarification using structured LLM output.

        Called when the user provides additional context after the agent
        indicated it needed more information.
        """
        user_clarification = clarification_context.get("user_clarification", "")
        original_context = clarification_context.get("original_context", "")

        if not user_clarification:
            return current_ideal_state

        prompt = self.prompts.get_ideal_state_clarification_prompt(
            original_task=current_ideal_state.task_description,
            trigger=str(original_context),
            clarification=user_clarification,
            current_state_json=current_ideal_state.model_dump_json(indent=2),
        )

        try:
            response = await self._structured_call(
                prompt=prompt,
                system_prompt=self.prompts.get("builder_agent/ideal_state_clarification_system.md"),
                response_model=IdealStateClarificationResponse,
                schema_name="ideal_state_clarification",
            )
            return self._apply_clarification(current_ideal_state, response, user_clarification)

        except Exception as e:
            logger.warning("Failed to incorporate clarification: %s", e)
            current_ideal_state.constraints.append(
                f"User clarification: {user_clarification[:200]}"
            )
            return current_ideal_state.refine({})

    # ================================================================
    # New LLM-powered methods
    # ================================================================

    async def extract_anti_criteria_from_violations(
        self,
        violations: list[dict[str, Any]],
        current_ideal_state: IdealState,
    ) -> list[AntiCriterion]:
        """Use LLM to extract generalized anti-criteria from observed violations.

        Goes beyond simple 1:1 mapping - identifies patterns across violations
        and creates anti-criteria that prevent entire classes of issues.
        """
        if not violations:
            return []

        existing_anti = [
            {"id": ac.id, "description": ac.description} for ac in current_ideal_state.anti_criteria
        ]

        prompt = self.prompts.get_ideal_state_anti_criteria_prompt(
            violations_str=json.dumps(violations, indent=2, default=str),
            existing_anti_criteria_str=json.dumps(existing_anti, indent=2),
            task_description=current_ideal_state.task_description,
        )

        try:
            response = await self._structured_call(
                prompt=prompt,
                system_prompt=self.prompts.get("builder_agent/ideal_state_anti_criteria_system.md"),
                response_model=AntiCriteriaExtractionResponse,
                schema_name="anti_criteria_extraction",
            )

            existing_ids = {ac.id for ac in current_ideal_state.anti_criteria}
            return [
                _spec_to_anti_criterion(spec)
                for spec in response.anti_criteria
                if spec.id not in existing_ids
            ]

        except Exception as e:
            logger.warning("Failed to extract anti-criteria from violations: %s", e)
            return []

    async def validate_criteria(
        self,
        ideal_state: IdealState,
    ) -> CriteriaValidationResponse:
        """Use LLM to validate whether criteria are well-formed and measurable.

        Checks for vagueness, unmeasurability, conflicts, and redundancy.
        Returns validation result with issues and suggestions.
        """
        criteria_data = [
            {
                "id": sc.id,
                "description": sc.description,
                "measurement": sc.measurement,
                "type": sc.criterion_type.value,
                "target_score": sc.target_score,
            }
            for sc in ideal_state.success_criteria
        ]
        anti_data = [
            {"id": ac.id, "description": ac.description, "detection": ac.detection}
            for ac in ideal_state.anti_criteria
        ]

        prompt = self.prompts.get_ideal_state_validation_prompt(
            criteria_str=json.dumps(criteria_data, indent=2),
            anti_criteria_str=json.dumps(anti_data, indent=2),
            task_description=ideal_state.task_description,
        )

        try:
            return await self._structured_call(
                prompt=prompt,
                system_prompt=self.prompts.get("builder_agent/ideal_state_validation_system.md"),
                response_model=CriteriaValidationResponse,
                schema_name="criteria_validation",
            )

        except Exception as e:
            logger.warning("Failed to validate criteria: %s", e)
            return CriteriaValidationResponse(
                is_valid=True,
                issues=[],
                suggestions=["Validation unavailable - proceeding with current criteria"],
                overall_quality_score=0.5,
            )

    async def suggest_verification_methods(
        self,
        ideal_state: IdealState,
        context: dict[str, Any] | None = None,
    ) -> list[VerificationMethod]:
        """Use LLM to suggest verification methods for each success criterion.

        Considers the task type and available infrastructure to recommend
        the most appropriate verification approaches.
        """
        criteria_data = [
            {
                "id": sc.id,
                "description": sc.description,
                "measurement": sc.measurement,
                "type": sc.criterion_type.value,
            }
            for sc in ideal_state.success_criteria
        ]

        prompt = self.prompts.get_ideal_state_verification_prompt(
            criteria_str=json.dumps(criteria_data, indent=2),
            task_description=ideal_state.task_description,
            context_str=json.dumps(context or {}, indent=2, default=str),
        )

        try:
            response = await self._structured_call(
                prompt=prompt,
                system_prompt=self.prompts.get("builder_agent/ideal_state_verification_system.md"),
                response_model=VerificationMethodSuggestionResponse,
                schema_name="verification_method_suggestion",
            )
            return [
                VerificationMethod(
                    method_type=vm.method_type,
                    description=vm.description,
                    automated=vm.automated,
                )
                for vm in response.verification_methods
            ]

        except Exception as e:
            logger.warning("Failed to suggest verification methods: %s", e)
            return []

    # ================================================================
    # Internal conversion helpers
    # ================================================================

    def _extraction_to_ideal_state(
        self,
        extraction: IdealStateExtractionResponse,
        task_description: str,
    ) -> IdealState:
        """Convert a structured extraction response to an IdealState domain model."""
        return IdealState(
            task_description=task_description,
            success_criteria=[_spec_to_success_criterion(sc) for sc in extraction.success_criteria],
            anti_criteria=[_spec_to_anti_criterion(ac) for ac in extraction.anti_criteria],
            quality_bar=QualityBar(
                description=extraction.quality_bar.description,
                examples=extraction.quality_bar.examples,
                anti_examples=extraction.quality_bar.anti_examples,
            ),
            verification_methods=[
                VerificationMethod(
                    method_type=vm.method_type,
                    description=vm.description,
                    automated=vm.automated,
                )
                for vm in extraction.verification_methods
            ],
            stop_conditions=[
                StopCondition(
                    condition_type=sc.condition_type,
                    threshold=sc.threshold,
                )
                for sc in extraction.stop_conditions
            ],
            constraints=extraction.constraints,
            assumptions=extraction.assumptions,
        )

    def _apply_refinement(
        self,
        current_state: IdealState,
        refinement: IdealStateRefinementResponse,
    ) -> IdealState:
        """Apply a structured refinement response to the current ideal state."""
        refined = current_state

        # Add new success criteria
        for sc_spec in refinement.new_success_criteria:
            refined.add_success_criterion(_spec_to_success_criterion(sc_spec))

        # Tighten existing criteria
        for tc in refinement.tightened_criteria:
            for criterion in refined.success_criteria:
                if criterion.id == tc.criterion_id:
                    if tc.updated_measurement:
                        criterion.measurement = tc.updated_measurement
                    criterion.target_score = tc.updated_target_score
                    break

        # Add new anti-criteria
        existing_ids = {ac.id for ac in refined.anti_criteria}
        for ac_spec in refinement.new_anti_criteria:
            if ac_spec.id not in existing_ids:
                refined.add_anti_criterion(_spec_to_anti_criterion(ac_spec))

        # Update quality bar if provided
        if refinement.updated_quality_description:
            refined.quality_bar = QualityBar(
                description=refinement.updated_quality_description,
                examples=refinement.updated_quality_examples or refined.quality_bar.examples,
                anti_examples=refinement.updated_quality_anti_examples
                or refined.quality_bar.anti_examples,
            )

        return refined.refine({})

    def _apply_clarification(
        self,
        current_state: IdealState,
        response: IdealStateClarificationResponse,
        user_clarification: str,
    ) -> IdealState:
        """Apply a structured clarification response to the current ideal state."""
        refinements: dict[str, Any] = {}

        # Update task description if provided
        if response.updated_task_description:
            refinements["task_description"] = (
                f"{current_state.task_description}\n\n"
                f"Additional context: {response.updated_task_description}"
            )

        # Add new success criteria
        for sc_spec in response.new_success_criteria:
            current_state.add_success_criterion(_spec_to_success_criterion(sc_spec))

        # Add new anti-criteria
        existing_ids = {ac.id for ac in current_state.anti_criteria}
        for ac_spec in response.new_anti_criteria:
            if ac_spec.id not in existing_ids:
                current_state.add_anti_criterion(_spec_to_anti_criterion(ac_spec))

        # Add constraints
        for constraint in response.additional_constraints:
            current_state.constraints.append(constraint)

        # Always record the clarification
        current_state.constraints.append(f"User clarification: {user_clarification[:200]}")

        return current_state.refine(refinements)

    def _create_default_ideal_state(self, task_description: str) -> IdealState:
        """Create a basic fallback ideal state when LLM extraction fails."""
        return IdealState(
            task_description=task_description,
            success_criteria=[
                SuccessCriterion(
                    id="default_task_completion",
                    description="Task completed successfully",
                    criterion_type=CriterionType.FUNCTIONAL,
                    rank=1,
                    measurement="Output exists and is human-readable advice (short paragraph)",
                    target_score=0.8,
                )
            ],
            stop_conditions=[
                StopCondition(condition_type="min_score", threshold=0.8),
                StopCondition(condition_type="max_iterations", threshold=5),
            ],
        )
