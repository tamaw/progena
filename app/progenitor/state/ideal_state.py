"""
Ideal State Models - Core of loop.spec.md outer loop.

Defines the target state with success criteria, anti-criteria, quality bar,
verification methods, and stop conditions. The Ideal State is the primary
control surface for the agent loop.
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, Field

from ..runtime.time_utils import utcnow

logger = logging.getLogger(__name__)


class CriterionType(str, Enum):
    """Type of success criterion."""
    FUNCTIONAL = "functional"  # Does it work?
    QUALITY = "quality"  # Is it good?
    PERFORMANCE = "performance"  # Is it fast enough?
    SECURITY = "security"  # Is it safe?
    MAINTAINABILITY = "maintainability"  # Can we maintain it?
    USER_EXPERIENCE = "user_experience"  # Will users like it?


class SuccessCriterion(BaseModel):
    """A single success criterion with measurement approach."""

    id: str = Field(description="Unique identifier for this criterion")
    description: str = Field(description="What success looks like")
    criterion_type: CriterionType = Field(default=CriterionType.FUNCTIONAL)
    rank: int = Field(default=1, description="Priority rank (1=highest)")
    measurement: str = Field(description="How to verify this criterion")
    target_score: float = Field(default=1.0, description="Minimum score to pass (0-1)")
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
    anti_examples: list[str] = Field(default_factory=list, description="Examples of what's NOT quality")


class VerificationMethod(BaseModel):
    """How to verify the output meets criteria."""

    method_type: str = Field(description="Type: test, heuristic, llm_judge, user_feedback")
    description: str = Field(description="What this verification checks")
    automated: bool = Field(default=True, description="Can run automatically?")


class StopCondition(BaseModel):
    """When to stop iterating."""

    condition_type: str = Field(description="Type: min_score, max_iterations, time_limit, tool_budget")
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
        default_factory=list,
        description="Ranked, measurable success criteria"
    )
    anti_criteria: list[AntiCriterion] = Field(
        default_factory=list,
        description="Things to actively avoid/penalize"
    )
    quality_bar: QualityBar = Field(
        default_factory=lambda: QualityBar(description="High quality, correct, maintainable output")
    )
    verification_methods: list[VerificationMethod] = Field(
        default_factory=list,
        description="How to verify success"
    )
    stop_conditions: list[StopCondition] = Field(
        default_factory=list,
        description="When to stop iterating"
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

    def check_stop_conditions(self, current_score: float, iteration: int,
                             elapsed_time: float, tool_calls: int) -> tuple[bool, list[str]]:
        """Check if any stop conditions are met.

        Returns:
            (should_stop, reasons)
        """
        reasons = []

        for condition in self.stop_conditions:
            if condition.met:
                continue

            if condition.condition_type == "min_score":
                if current_score >= condition.threshold:
                    condition.met = True
                    reasons.append(f"Minimum score {condition.threshold} reached: {current_score:.2f}")

            elif condition.condition_type == "max_iterations":
                if iteration >= condition.threshold:
                    condition.met = True
                    reasons.append(f"Max iterations {condition.threshold} reached")

            elif condition.condition_type == "time_limit":
                if elapsed_time >= condition.threshold:
                    condition.met = True
                    reasons.append(f"Time limit {condition.threshold}s exceeded: {elapsed_time:.1f}s")

            elif condition.condition_type == "tool_budget":
                if tool_calls >= condition.threshold:
                    condition.met = True
                    reasons.append(f"Tool budget {condition.threshold} exhausted: {tool_calls} calls")

        should_stop = any(c.met for c in self.stop_conditions)
        return should_stop, reasons


class IdealStateBuilder:
    """Builds and refines ideal state from task descriptions."""

    def __init__(self, llm_client):
        self.llm_client = llm_client

    async def build_from_task(self, task_description: str, context: Optional[dict[str, Any]] = None) -> IdealState:
        """
        Build an ideal state from a task description.

        Uses LLM to extract:
        - Success criteria (ranked, measurable)
        - Anti-criteria (what to avoid)
        - Quality bar
        - Verification methods
        - Stop conditions
        """
        prompt = self._build_extraction_prompt(task_description, context or {})

        try:
            response = await self.llm_client.generate(
                prompt=prompt,
                system_prompt="You are an expert at analyzing tasks and defining measurable success criteria.",
            )

            # Parse LLM response into IdealState
            ideal_state_data = self._parse_llm_response(response, task_description)
            return IdealState(**ideal_state_data)

        except Exception as e:
            # Fallback to basic ideal state
            return self._create_default_ideal_state(task_description)

    async def refine_from_iteration(
        self,
        current_ideal_state: IdealState,
        iteration_results: dict[str, Any],
    ) -> IdealState:
        """
        Refine ideal state based on iteration results.

        Called during OBSERVE phase per loop.spec.md:
        - Tighten criteria that were too loose
        - Add anti-criteria for observed issues
        - Update based on verification gaps
        """
        # First, extract anti-criteria from violations (loop.spec.md behavioral rule)
        violations = iteration_results.get("violations", [])
        existing_anti_ids = {ac.id for ac in current_ideal_state.anti_criteria}

        new_anti_criteria = self._extract_anti_criteria_from_violations(
            violations, existing_anti_ids
        )

        # Add any new anti-criteria directly
        refined_state = current_ideal_state
        for anti in new_anti_criteria:
            refined_state.add_anti_criterion(anti)

        # Also ask LLM for refinements
        prompt = self._build_refinement_prompt(current_ideal_state, iteration_results)

        try:
            response = await self.llm_client.generate(
                prompt=prompt,
                system_prompt="You refine success criteria based on observed outcomes. Be specific about what to tighten or add.",
            )

            refinements = self._parse_refinement_response(response)

            # Handle LLM-suggested anti-criteria
            if "_new_anti_criteria" in refinements:
                for i, desc in enumerate(refinements["_new_anti_criteria"]):
                    anti_id = f"anti_llm_{refined_state.version}_{i+1}"
                    if anti_id not in existing_anti_ids:
                        refined_state.add_anti_criterion(AntiCriterion(
                            id=anti_id,
                            description=desc,
                            severity=0.8,
                            detection=f"Check for: {desc[:30]}",
                        ))
                del refinements["_new_anti_criteria"]

            # Apply other refinements
            if refinements:
                refined_state = refined_state.refine(refinements)
            else:
                # Increment version even if just anti-criteria added
                refined_state = refined_state.refine({})

            return refined_state

        except (ValueError, KeyError, TypeError) as e:
            # Log and return with at least the violation-based refinements
            logger.warning("Failed to parse LLM refinement response: %s", e)
            return refined_state.refine({})

    def _build_extraction_prompt(self, task: str, context: dict) -> str:
        """Build prompt for extracting ideal state from task."""
        return f"""Analyze this task and extract measurable success criteria:

TASK: {task}

CONTEXT: {context}

Extract:
1. Success Criteria (ranked by priority, measurable)
   - What defines success?
   - How can we measure each criterion?
   - What's the minimum acceptable score?

2. Anti-Criteria (what to avoid)
   - Common failure modes
   - User annoyances
   - Quality violations

3. Quality Bar
   - What would "euphoric surprise" look like?
   - What's the difference between acceptable and amazing?

4. Verification Methods
   - How to verify each criterion (tests, heuristics, human check)

5. Stop Conditions
   - Minimum verification score (0-1)
   - Max iterations
   - Time/resource limits

Format as structured data."""

    def _build_refinement_prompt(self, ideal_state: IdealState, results: dict) -> str:
        """Build prompt for refining ideal state."""
        return f"""Refine the ideal state based on iteration results:

CURRENT IDEAL STATE:
{ideal_state.model_dump_json(indent=2)}

ITERATION RESULTS:
{results}

Suggest refinements:
1. Tighten any criteria that were too loose
2. Add new anti-criteria for observed issues
3. Adjust quality bar based on what's achievable
4. Update verification methods if needed

Return only the refinements as structured data."""

    def _parse_llm_response(self, response: str, task_description: str) -> dict[str, Any]:
        """Parse LLM response into ideal state data."""
        # Simplified parser - in production would use structured output
        return {
            "task_description": task_description,
            "success_criteria": [
                SuccessCriterion(
                    id="criterion_1",
                    description="Task completed successfully",
                    criterion_type=CriterionType.FUNCTIONAL,
                    rank=1,
                    measurement="Check output exists and is valid",
                    target_score=0.8,
                )
            ],
            "anti_criteria": [
                AntiCriterion(
                    id="anti_1",
                    description="Avoid verbose or unnecessary output",
                    severity=0.5,
                    detection="Check for fluff words and redundancy",
                )
            ],
            "stop_conditions": [
                StopCondition(condition_type="min_score", threshold=0.8),
                StopCondition(condition_type="max_iterations", threshold=5),
            ],
        }

    def _parse_refinement_response(self, response: str) -> dict[str, Any]:
        """Parse refinement suggestions from LLM response."""
        # Try to extract structured refinements
        refinements = {}

        # Simple heuristic parsing - in production use structured output
        lines = response.strip().split("\n")
        new_anti_criteria = []
        tightened_criteria = []

        in_anti_criteria = False
        in_tightening = False

        for line in lines:
            line_lower = line.lower().strip()

            # Detect sections
            if "anti-criteria" in line_lower or "avoid" in line_lower:
                in_anti_criteria = True
                in_tightening = False
                continue
            if "tighten" in line_lower or "criteria" in line_lower:
                in_anti_criteria = False
                in_tightening = True
                continue

            # Extract anti-criteria
            if in_anti_criteria and line.strip().startswith("-"):
                content = line.strip().lstrip("- ")
                if content:
                    new_anti_criteria.append(content)

            # Extract criteria tightening (for future use)
            if in_tightening and line.strip().startswith("-"):
                content = line.strip().lstrip("- ")
                if content:
                    tightened_criteria.append(content)

        if new_anti_criteria:
            refinements["_new_anti_criteria"] = new_anti_criteria

        return refinements

    def _extract_anti_criteria_from_violations(
        self,
        violations: list[dict[str, Any]],
        existing_ids: set[str],
    ) -> list[AntiCriterion]:
        """
        Extract new anti-criteria from observed violations.

        Per loop.spec.md: Anti-criteria actively penalise fluff, violations, and user annoyances.
        This method creates new anti-criteria from observed issues.
        """
        new_anti_criteria = []

        for i, violation in enumerate(violations):
            violation_desc = violation.get("description", "")
            if not violation_desc:
                continue

            # Generate ID
            anti_id = f"anti_violation_{len(existing_ids) + i + 1}"

            # Don't duplicate
            if violation_desc.lower() in [ac.lower() for ac in existing_ids]:
                continue

            new_anti_criteria.append(AntiCriterion(
                id=anti_id,
                description=f"Avoid: {violation_desc}",
                severity=violation.get("severity", 1.0),
                detection=f"Check for recurrence of: {violation_desc[:50]}",
            ))

        return new_anti_criteria

    def _create_default_ideal_state(self, task_description: str) -> IdealState:
        """Create a basic fallback ideal state."""
        return IdealState(
            task_description=task_description,
            success_criteria=[
                SuccessCriterion(
                    id="default_success",
                    description="Task completed",
                    criterion_type=CriterionType.FUNCTIONAL,
                    rank=1,
                    measurement="Output exists",
                    target_score=0.8,
                )
            ],
            stop_conditions=[
                StopCondition(condition_type="min_score", threshold=0.8),
                StopCondition(condition_type="max_iterations", threshold=5),
            ],
        )

    async def refine_with_clarification(
        self,
        current_ideal_state: "IdealState",
        clarification_context: dict[str, Any],
    ) -> "IdealState":
        """
        Refine ideal state with user's clarification response.

        Called when the user provides additional context after Copilot
        indicated it needed more information. This incorporates the
        user's response into the ideal state criteria and task description.

        Args:
            current_ideal_state: The ideal state before clarification
            clarification_context: Contains:
                - user_clarification: The user's response text
                - original_context: What triggered the clarification need
                - iteration_before_clarification: Which iteration needed it

        Returns:
            Updated IdealState with clarification incorporated
        """
        user_clarification = clarification_context.get("user_clarification", "")
        original_context = clarification_context.get("original_context", "")

        if not user_clarification:
            return current_ideal_state

        # Build prompt to incorporate clarification into ideal state
        prompt = f"""The user has provided additional clarification for this task.

ORIGINAL TASK:
{current_ideal_state.task_description}

WHAT TRIGGERED CLARIFICATION:
{original_context}

USER'S CLARIFICATION:
{user_clarification}

Based on this clarification, update the task understanding:
1. How does this clarification change or enhance the task description?
2. Are there new success criteria to add?
3. Are there new anti-criteria (things to avoid)?
4. Does this tighten any existing criteria?

Provide:
- UPDATED_TASK: An enhanced task description incorporating the clarification
- NEW_CRITERIA: Any new success criteria (one per line, starting with -)
- NEW_ANTI_CRITERIA: Any new anti-criteria (one per line, starting with -)
- TIGHTENED: Any criteria that should be more specific now"""

        try:
            response = await self.llm_client.generate(
                prompt=prompt,
                system_prompt="You incorporate user clarifications into task specifications. Be specific and actionable.",
            )

            refinements = self._parse_clarification_response(response, current_ideal_state)

            # Update task description if provided
            if "task_description" in refinements:
                current_ideal_state.task_description = refinements["task_description"]
                del refinements["task_description"]

            # Add new criteria
            if "_new_criteria" in refinements:
                for i, desc in enumerate(refinements["_new_criteria"]):
                    criterion_id = f"criterion_clarification_{current_ideal_state.version}_{i+1}"
                    current_ideal_state.add_success_criterion(SuccessCriterion(
                        id=criterion_id,
                        description=desc,
                        criterion_type=CriterionType.FUNCTIONAL,
                        rank=2,  # Lower priority than original criteria
                        measurement=f"Verify: {desc[:50]}",
                        target_score=0.8,
                    ))
                del refinements["_new_criteria"]

            # Add new anti-criteria
            if "_new_anti_criteria" in refinements:
                for i, desc in enumerate(refinements["_new_anti_criteria"]):
                    anti_id = f"anti_clarification_{current_ideal_state.version}_{i+1}"
                    current_ideal_state.add_anti_criterion(AntiCriterion(
                        id=anti_id,
                        description=desc,
                        severity=0.8,
                        detection=f"Check for: {desc[:30]}",
                    ))
                del refinements["_new_anti_criteria"]

            # Add clarification to constraints for context
            current_ideal_state.constraints.append(
                f"User clarification: {user_clarification[:200]}"
            )

            return current_ideal_state.refine(refinements)

        except Exception as e:
            logger.warning("Failed to incorporate clarification: %s", e)
            # At minimum, add clarification as a constraint
            current_ideal_state.constraints.append(
                f"User clarification: {user_clarification[:200]}"
            )
            return current_ideal_state.refine({})

    def _parse_clarification_response(
        self,
        response: str,
        ideal_state: "IdealState"
    ) -> dict[str, Any]:
        """Parse clarification refinement response from LLM."""
        refinements = {}
        lines = response.strip().split("\n")

        current_section = None
        new_criteria = []
        new_anti_criteria = []
        updated_task = []

        for line in lines:
            line_stripped = line.strip()
            line_lower = line_stripped.lower()

            # Detect section headers
            if line_lower.startswith("updated_task:"):
                current_section = "task"
                content = line_stripped.split(":", 1)
                if len(content) > 1 and content[1].strip():
                    updated_task.append(content[1].strip())
                continue
            elif line_lower.startswith("new_criteria:"):
                current_section = "criteria"
                continue
            elif line_lower.startswith("new_anti_criteria:"):
                current_section = "anti"
                continue
            elif line_lower.startswith("tightened:"):
                current_section = "tightened"
                continue

            # Extract content based on section
            if current_section == "task" and line_stripped:
                updated_task.append(line_stripped)
            elif current_section == "criteria" and line_stripped.startswith("-"):
                content = line_stripped.lstrip("- ")
                if content:
                    new_criteria.append(content)
            elif current_section == "anti" and line_stripped.startswith("-"):
                content = line_stripped.lstrip("- ")
                if content:
                    new_anti_criteria.append(content)

        # Build refinements
        if updated_task:
            # Append clarification context to task, don't replace entirely
            clarification_summary = " ".join(updated_task)
            refinements["task_description"] = (
                f"{ideal_state.task_description}\n\n"
                f"Additional context: {clarification_summary}"
            )

        if new_criteria:
            refinements["_new_criteria"] = new_criteria

        if new_anti_criteria:
            refinements["_new_anti_criteria"] = new_anti_criteria

        return refinements
