"""
THINK Node - Second phase of inner loop (loop.spec.md).

BL-003: Upgraded to output 2-3 structured approaches with explicit
tradeoffs/risks and recommended experiment plan deltas.

PLAN can select one approach deterministically (or with a simple policy).
"""

import logging
import time
import uuid
from typing import Any

from ..state.models import ProgenitorState
from ..state.execution_schemas import (
    StructuredApproach,
    ThinkOutput,
    TradeOff,
    Risk,
    ExperimentPlanDelta,
    PromptSpec,
)
from ..runtime.time_utils import utcnow_isoformat
from . import NodeFunction, NodeStateUpdate

logger = logging.getLogger(__name__)


def _get_telemetry():
    """Get telemetry components if available."""
    try:
        from ..telemetry import get_tracer, get_metrics
        return get_tracer(), get_metrics()
    except ImportError:
        return None, None


def create_node(
    llm_client,
    state_manager=None,
) -> NodeFunction:
    """Create the think node function.

    CQ-006: Returns NodeFunction type - async function that takes
    ProgenitorState and returns NodeStateUpdate (partial dict).
    """

    async def think_node(state: ProgenitorState) -> NodeStateUpdate:
        """
        THINK phase: Generate 2-3 structured approaches with explicit
        tradeoffs/risks and recommended experiment plan deltas (BL-003).

        Considers:
        - Current ideal state and criteria
        - Previous iteration results (if any)
        - Available tools and capabilities
        - Constraints and anti-criteria

        PLAN can select one approach deterministically (or with a simple policy).
        """
        logger.info("THINK: Starting think phase")
        start_time = time.perf_counter()
        tracer, metrics = _get_telemetry()
        span = None

        ideal_state = state.get("ideal_state")
        iteration_state = state.get("iteration", {})
        iteration = iteration_state.get("current", 0) if isinstance(iteration_state, dict) else getattr(iteration_state, "current", 0)

        if tracer:
            span = tracer.start_span(
                "node_think",
                attributes={
                    "iteration": iteration,
                    "has_ideal_state": ideal_state is not None,
                },
            )

        if not ideal_state:
            logger.warning("No ideal state in THINK phase")
            if span:
                span.set_attribute("skipped", True)
                span.end()
            return {}

        verification_state = state.get("verification", {})
        last_verification = verification_state.get("current_report") if isinstance(verification_state, dict) else getattr(verification_state, "current_report", None)

        # Build thinking prompt for structured approaches
        prompt = _build_thinking_prompt(ideal_state, iteration or 0, last_verification, state)

        try:
            response = await llm_client.generate(
                prompt=prompt,
                system_prompt="""You are a strategic thinker analyzing approaches to reach ideal state.
Generate exactly 2-3 distinct approaches, each with:
- Clear strategy and implementation steps
- Explicit trade-offs (what we gain vs what we give up)
- Known risks with likelihood and impact
- Expected success probability

Format each approach clearly with numbered sections.""",
            )

            # BL-003: Parse into structured approaches with tradeoffs/risks
            think_output = _parse_structured_approaches(response, iteration or 0, last_verification)

            if span:
                span.add_event("approaches_generated", {
                    "count": len(think_output.approaches),
                    "prompt_length": len(prompt),
                })

            if state_manager:
                # Log decision for audit trail
                await state_manager.log_decision({
                    "event": "thinking_phase",
                    "iteration": iteration,
                    "approaches_generated": len(think_output.approaches),
                    "approach_names": [a.name for a in think_output.approaches],
                    "recommended_approach": think_output.recommended_approach_id,
                    "timestamp": utcnow_isoformat(),
                })
                # Track LLM call in build_messages (build_state.json)
                await state_manager.add_build_message({
                    "phase": "think",
                    "iteration": iteration,
                    "prompt_preview": prompt[:500] + "..." if len(prompt) > 500 else prompt,
                    "response_preview": response[:500] + "..." if len(response) > 500 else response,
                    "timestamp": utcnow_isoformat(),
                })

            # Record metrics
            duration = time.perf_counter() - start_time
            if metrics:
                metrics.record_histogram("node_duration_seconds", duration, {"node": "think"})
                metrics.increment("node_invocations_total", {"node": "think", "success": "true"})

            if span:
                span.set_attribute("duration_seconds", duration)
                span.set_attribute("approaches_count", len(think_output.approaches))
                span.end()

            # Convert to dicts for state storage (LangGraph needs dicts)
            approaches_dicts = [a.model_dump() for a in think_output.approaches]

            # Get gaps from previous verification for nested structure
            gaps_analyzed = []
            if last_verification:
                verif_dict = last_verification.model_dump() if hasattr(last_verification, "model_dump") else last_verification
                gaps_analyzed = verif_dict.get("gaps", [])[:5]

            return {
                # Nested structure
                "phases": {
                    "think": {
                        "approaches": approaches_dicts,
                        "recommended_id": think_output.recommended_approach_id,
                        "gaps_analyzed": gaps_analyzed,
                    },
                },
                "build_phase": "think",
            }
        except Exception as e:
            logger.error(f"THINK phase error: {e}")
            if metrics:
                metrics.increment("node_invocations_total", {"node": "think", "success": "false"})
            if span:
                span.record_exception(e)
                span.end()
            # BL-003: Fallback to structured default approach
            default_approach = StructuredApproach(
                id=f"default_{uuid.uuid4().hex[:6]}",
                name="default",
                description="Proceed with standard execution",
                strategy="Execute the task using available tools",
                implementation_steps=["Execute primary action", "Verify result"],
                trade_offs=[
                    TradeOff(
                        aspect="Time",
                        positive="Fast execution",
                        negative="May miss edge cases",
                        severity="medium",
                    )
                ],
                risks=[
                    Risk(
                        id="risk_unknown",
                        description="Unknown risks due to thinking error",
                        likelihood="medium",
                        impact="medium",
                    )
                ],
                expected_success_probability=0.5,
            )
            return {
                "phases": {
                    "think": {
                        "approaches": [default_approach.model_dump()],
                        "recommended_id": default_approach.id,
                        "gaps_analyzed": False,
                    },
                },
                "build_phase": "think",
            }

    return think_node


def _build_thinking_prompt(ideal_state, iteration: int, last_verification, state) -> str:
    """Build prompt for thinking phase with structured output requirements."""
    ideal_state_dict = ideal_state.model_dump() if hasattr(ideal_state, "model_dump") else ideal_state

    # Extract success criteria descriptions
    criteria = ideal_state_dict.get('success_criteria', [])
    criteria_text = "\n".join([
        f"  - {c.get('description', 'N/A')} (target: {c.get('target_score', 0.8)})"
        for c in criteria[:5]
    ])

    # Extract anti-criteria
    anti_criteria = ideal_state_dict.get('anti_criteria', [])
    anti_text = "\n".join([
        f"  - {ac.get('description', 'N/A')}"
        for ac in anti_criteria[:3]
    ])

    prompt_parts = [
        "Analyze approaches to reach this ideal state and generate 2-3 structured approaches:",
        "",
        "IDEAL STATE:",
        f"Task: {ideal_state_dict.get('task_description', 'N/A')}",
        "",
        "Success Criteria:",
        criteria_text if criteria_text else "  - Complete task successfully",
        "",
        "Anti-Criteria (things to avoid):",
        anti_text if anti_text else "  - None specified",
    ]

    # Add previous iteration context
    gaps = []
    if iteration > 0 and last_verification:
        if hasattr(last_verification, "model_dump"):
            verif_dict = last_verification.model_dump()
        elif isinstance(last_verification, dict):
            verif_dict = last_verification
        else:
            verif_dict = {"overall_score": 0, "passed": False, "gaps": []}

        gaps = verif_dict.get('gaps', [])[:5]
        gap_lines = [f"  - {gap}" for gap in gaps] if gaps else ["  - None identified"]
        prompt_parts.extend([
            "",
            f"PREVIOUS ITERATION (#{iteration}):",
            f"Score: {verif_dict.get('overall_score', 0):.2f}",
            f"Passed: {verif_dict.get('passed', False)}",
            "Gaps identified:",
        ])
        prompt_parts.extend(gap_lines)

    # BL-BUG-002: Add lessons_learned to the prompt so THINK can learn from past iterations
    lessons_learned = state.get("lessons_learned", [])
    if lessons_learned and iteration > 0:
        # Get most recent lessons (last 2 iterations)
        recent_lessons = lessons_learned[-2:] if len(lessons_learned) > 2 else lessons_learned
        prompt_parts.extend([
            "",
            "LESSONS FROM PREVIOUS ITERATIONS:",
        ])
        for lesson in recent_lessons:
            iter_num = lesson.get("iteration", "?")
            prompt_parts.append(f"  Iteration {iter_num}:")
            # What worked
            what_worked = lesson.get("what_worked", [])
            if what_worked:
                prompt_parts.append("    What worked:")
                for item in what_worked[:3]:
                    prompt_parts.append(f"      - {item}")
            # What failed
            what_failed = lesson.get("what_failed", [])
            if what_failed:
                prompt_parts.append("    What failed:")
                for item in what_failed[:3]:
                    prompt_parts.append(f"      - {item}")
            # Adjustments
            adjustments = lesson.get("adjustments", [])
            if adjustments:
                prompt_parts.append("    Recommended adjustments:")
                for item in adjustments[:3]:
                    prompt_parts.append(f"      - {item}")
            # Insights
            insights = lesson.get("insights", [])
            if insights:
                prompt_parts.append("    Key insights:")
                for item in insights[:2]:
                    prompt_parts.append(f"      - {item}")

    prompt_parts.extend([
        "",
        "Generate 2-3 DISTINCT approaches. For each approach provide:",
        "",
        "APPROACH [N]: [Name]",
        "STRATEGY: [Core strategy/technique]",
        "STEPS:",
        "  1. [Step 1]",
        "  2. [Step 2]",
        "  ...",
        "TRADE-OFFS:",
        "  - [Aspect]: Gain [X] but lose [Y] (severity: low/medium/high)",
        "RISKS:",
        "  - [Risk]: likelihood=low/medium/high, impact=low/medium/high",
        "SUCCESS_PROBABILITY: [0.0-1.0]",
        "ADDRESSES_GAPS: [Which gaps from previous iteration this addresses]",
        "",
        "Focus on:",
        "- Addressing the identified gaps" if gaps else "- Meeting success criteria",
        "- Learning from previous iteration lessons" if lessons_learned else "",
        "- Avoiding anti-criteria",
        "- Providing measurable outcomes",
    ])

    return "\n".join(prompt_parts)


def _parse_structured_approaches(response: str, iteration: int, last_verification) -> ThinkOutput:
    """Parse LLM response into structured ThinkOutput with StructuredApproach objects.

    BL-003: Approaches become typed/JSON with explicit tradeoffs/risks.
    """
    approaches = []
    lines = response.split("\n")

    current_approach = None
    current_section = None
    approach_count = 0

    # Extract gaps from last verification
    gaps_analyzed = []
    if last_verification:
        if hasattr(last_verification, "model_dump"):
            verif_dict = last_verification.model_dump()
        elif isinstance(last_verification, dict):
            verif_dict = last_verification
        else:
            verif_dict = {}
        gaps_analyzed = verif_dict.get("gaps", [])[:5]

    for line in lines:
        line_stripped = line.strip()
        line_lower = line_stripped.lower()

        # Detect new approach
        if line_lower.startswith("approach") and (":" in line_stripped or "[" in line_stripped):
            # Save previous approach
            if current_approach:
                approaches.append(current_approach)

            approach_count += 1
            # Extract name from line like "APPROACH 1: Direct Implementation" or "APPROACH [1]: Name"
            name_part = line_stripped.split(":", 1)[-1].strip() if ":" in line_stripped else f"approach_{approach_count}"

            current_approach = StructuredApproach(
                id=f"approach_{uuid.uuid4().hex[:6]}",
                name=name_part[:50] or f"approach_{approach_count}",
                description="",
                strategy="",
                implementation_steps=[],
                trade_offs=[],
                risks=[],
                addresses_gaps=[],
                priority=approach_count,
            )
            current_section = "name"

        # Parse sections within approach
        elif current_approach:
            if line_lower.startswith("strategy:"):
                current_approach.strategy = line_stripped.split(":", 1)[-1].strip()
                current_section = "strategy"

            elif line_lower.startswith("steps:"):
                current_section = "steps"

            elif line_lower.startswith("trade-off") or line_lower.startswith("tradeoff"):
                current_section = "tradeoffs"

            elif line_lower.startswith("risk"):
                current_section = "risks"

            elif line_lower.startswith("success_probability:") or line_lower.startswith("probability:"):
                try:
                    prob_str = line_stripped.split(":", 1)[-1].strip()
                    current_approach.expected_success_probability = float(prob_str)
                except (ValueError, IndexError):
                    pass

            elif line_lower.startswith("addresses_gaps:") or line_lower.startswith("addresses:"):
                gap_text = line_stripped.split(":", 1)[-1].strip()
                current_approach.addresses_gaps = [g.strip() for g in gap_text.split(",") if g.strip()]

            # Parse content based on current section
            elif current_section == "strategy" and line_stripped and not line_stripped.startswith(("-", "•")):
                current_approach.strategy += " " + line_stripped

            elif current_section == "steps" and (line_stripped.startswith(("-", "•", "1", "2", "3", "4", "5"))):
                step = line_stripped.lstrip("-•0123456789. ").strip()
                if step:
                    current_approach.implementation_steps.append(step)

            elif current_section == "tradeoffs" and line_stripped.startswith(("-", "•")):
                tradeoff = _parse_tradeoff(line_stripped)
                if tradeoff:
                    current_approach.trade_offs.append(tradeoff)

            elif current_section == "risks" and line_stripped.startswith(("-", "•")):
                risk = _parse_risk(line_stripped)
                if risk:
                    current_approach.risks.append(risk)

    # Don't forget the last approach
    if current_approach:
        approaches.append(current_approach)

    # Ensure we have at least one approach
    if not approaches:
        approaches.append(StructuredApproach(
            id=f"default_{uuid.uuid4().hex[:6]}",
            name="primary",
            description=response[:500] if response else "Standard approach",
            strategy="Execute the task directly",
            implementation_steps=["Execute primary action", "Verify result"],
            trade_offs=[
                TradeOff(
                    aspect="Simplicity",
                    positive="Quick to implement",
                    negative="May miss nuances",
                    severity="low",
                )
            ],
            risks=[
                Risk(
                    id="risk_iteration",
                    description="May need iteration",
                    likelihood="medium",
                    impact="low",
                )
            ],
            expected_success_probability=0.6,
        ))

    # Ensure we have 2-3 approaches (BL-003 requirement)
    while len(approaches) < 2:
        alt_approach = StructuredApproach(
            id=f"alternative_{uuid.uuid4().hex[:6]}",
            name=f"alternative_{len(approaches) + 1}",
            description="Alternative approach with different trade-offs",
            strategy="Take a different angle to the problem",
            implementation_steps=["Identify alternative path", "Execute alternative", "Compare results"],
            trade_offs=[
                TradeOff(
                    aspect="Exploration",
                    positive="May find better solution",
                    negative="Takes more time",
                    severity="medium",
                )
            ],
            risks=[
                Risk(
                    id=f"risk_alt_{len(approaches)}",
                    description="Alternative may not be better",
                    likelihood="medium",
                    impact="low",
                )
            ],
            expected_success_probability=0.4,
            priority=len(approaches) + 1,
        )
        approaches.append(alt_approach)

    # Build description from strategy and steps for each approach
    for approach in approaches:
        if not approach.description:
            approach.description = approach.strategy
            if approach.implementation_steps:
                approach.description += ". Steps: " + "; ".join(approach.implementation_steps[:3])

    # Select recommended approach (highest success probability that addresses gaps)
    recommended = max(approaches, key=lambda a: (
        len(set(a.addresses_gaps) & set(gaps_analyzed)) * 0.3 + a.expected_success_probability
    ))

    return ThinkOutput(
        iteration=iteration,
        approaches=approaches[:3],  # Limit to 3
        gaps_analyzed=gaps_analyzed,
        constraints_considered=[],
        recommended_approach_id=recommended.id,
        recommendation_reasoning=f"Selected '{recommended.name}' with {recommended.expected_success_probability:.0%} success probability",
    )


def _parse_tradeoff(line: str) -> TradeOff | None:
    """Parse a trade-off from a line like '- Speed: Gain fast execution but lose thoroughness (medium)'"""
    try:
        content = line.lstrip("-• ").strip()

        # Try to extract aspect and details
        if ":" in content:
            aspect, rest = content.split(":", 1)
            aspect = aspect.strip()
        else:
            aspect = "General"
            rest = content

        # Look for gain/lose or positive/negative patterns
        positive = "Benefit"
        negative = "Cost"
        severity = "medium"

        rest_lower = rest.lower()
        if "gain" in rest_lower and "lose" in rest_lower:
            parts = rest.split("but")
            if len(parts) == 2:
                positive = parts[0].replace("gain", "").replace("Gain", "").strip()
                negative = parts[1].replace("lose", "").replace("Lose", "").strip()
        elif "but" in rest_lower:
            parts = rest.split("but")
            if len(parts) == 2:
                positive = parts[0].strip()
                negative = parts[1].strip()
        else:
            positive = rest.strip()

        # Extract severity
        for sev in ["low", "medium", "high"]:
            if sev in rest_lower:
                severity = sev
                break

        return TradeOff(
            aspect=aspect[:50],
            positive=positive[:100],
            negative=negative[:100],
            severity=severity,
        )
    except Exception:
        return None


def _parse_risk(line: str) -> Risk | None:
    """Parse a risk from a line like '- May fail: likelihood=medium, impact=high'"""
    try:
        content = line.lstrip("-• ").strip()

        description = content
        likelihood = "medium"
        impact = "medium"

        # Extract likelihood and impact if present
        content_lower = content.lower()
        for level in ["low", "medium", "high"]:
            if f"likelihood={level}" in content_lower or f"likelihood: {level}" in content_lower:
                likelihood = level
            if f"impact={level}" in content_lower or f"impact: {level}" in content_lower:
                impact = level

        # Clean description
        description = content.split(",")[0].split("likelihood")[0].split("impact")[0].strip()
        if ":" in description:
            description = description.split(":", 1)[-1].strip()

        return Risk(
            id=f"risk_{uuid.uuid4().hex[:6]}",
            description=description[:200],
            likelihood=likelihood,
            impact=impact,
        )
    except Exception:
        return None
