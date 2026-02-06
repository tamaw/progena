"""
THINK Node - Second phase of inner loop (loop.spec.md).

BL-003: Upgraded to output 2-3 structured approaches with explicit
tradeoffs/risks and recommended experiment plan deltas.

Uses structured LLM output (ThinkResponse) for validated responses,
PromptTemplates for maintainable prompts.

Includes:
- Strict approach quality validation
- Multi-factor selection with exploration bias
- Approach comparison matrix for PLAN node
- Retry logic on validation failure
- Approach history tracking in build_state.json

PLAN can select one approach deterministically (or with a simple policy).
"""

import json
import logging
import random
import time
import uuid
from typing import Any

from ..llm.prompts import PromptTemplates
from ..llm.response_models import ThinkResponse
from ..state.models import ProgenitorState
from ..state.execution_schemas import (
    StructuredApproach,
    ThinkOutput,
    TradeOff,
    Risk,
)
from ..runtime.time_utils import utcnow_isoformat
from . import NodeFunction, NodeStateUpdate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THINK_SYSTEM_PROMPT = (
    "You are a strategic thinker analyzing approaches to reach an ideal state. "
    "Generate exactly 2-3 distinct approaches, each with a different strategy. "
    "For each approach provide clear implementation steps, explicit trade-offs "
    "(what we gain vs what we give up), known risks with likelihood and impact, "
    "and an estimated success probability. "
    "Be specific and actionable. Approaches should be meaningfully different, "
    "not minor variations of the same idea."
)

# Validation thresholds
MIN_IMPLEMENTATION_STEPS = 2
MIN_QUALITY_SCORE = 0.5

# Retry on validation failure
MAX_LLM_RETRIES = 1

# Exploration probability for selection algorithm (prefer novel approaches)
EXPLORATION_PROBABILITY = 0.15

# Scoring weights for risk/trade-off penalties
_RISK_PENALTY = {"low": 0.0, "medium": 0.05, "high": 0.15}
_TRADEOFF_PENALTY = {"low": 0.0, "medium": 0.03, "high": 0.08}


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

def _get_telemetry():
    """Get telemetry components if available."""
    try:
        from ..telemetry import get_tracer, get_metrics
        return get_tracer(), get_metrics()
    except ImportError:
        return None, None


# ---------------------------------------------------------------------------
# Structured output helpers
# ---------------------------------------------------------------------------

def _get_response_format(llm_client) -> dict[str, Any]:
    """Get structured output response format for ThinkResponse."""
    if hasattr(llm_client, '_get_json_response_format'):
        return llm_client._get_json_response_format(ThinkResponse, "think_response")
    # Fallback: import the builder from the client module
    from ..llm.client import _build_json_schema_format
    return _build_json_schema_format(ThinkResponse, "think_response")


def _extract_gaps(last_verification) -> list[str]:
    """Extract gap list from a verification report."""
    if not last_verification:
        return []
    if hasattr(last_verification, "model_dump"):
        verif_dict = last_verification.model_dump()
    elif isinstance(last_verification, dict):
        verif_dict = last_verification
    else:
        return []
    return verif_dict.get("gaps", [])[:5]


# ---------------------------------------------------------------------------
# Approach conversion (LLM response -> internal models)
# ---------------------------------------------------------------------------

def _convert_approaches(think_resp: ThinkResponse) -> list[StructuredApproach]:
    """Convert LLM ThinkResponse approaches to internal StructuredApproach objects.

    Returns a plain list (may be empty if the LLM returned nothing).
    """
    approaches: list[StructuredApproach] = []
    for i, resp_approach in enumerate(think_resp.approaches[:3]):
        approach = StructuredApproach(
            id=f"approach_{uuid.uuid4().hex[:6]}",
            name=resp_approach.name[:50] or f"approach_{i + 1}",
            description=resp_approach.strategy,
            strategy=resp_approach.strategy,
            implementation_steps=resp_approach.implementation_steps,
            trade_offs=[
                TradeOff(
                    aspect=to.aspect[:50],
                    positive=to.positive[:100],
                    negative=to.negative[:100],
                    severity=to.severity,
                )
                for to in resp_approach.trade_offs
            ],
            risks=[
                Risk(
                    id=f"risk_{uuid.uuid4().hex[:6]}",
                    description=r.description[:200],
                    likelihood=r.likelihood,
                    impact=r.impact,
                )
                for r in resp_approach.risks
            ],
            addresses_gaps=resp_approach.addresses_gaps,
            expected_success_probability=resp_approach.expected_success_probability,
            priority=i + 1,
        )
        # Build description from strategy + steps
        if approach.implementation_steps:
            approach.description += ". Steps: " + "; ".join(approach.implementation_steps[:3])
        approaches.append(approach)
    return approaches


# ---------------------------------------------------------------------------
# Strict approach validation
# ---------------------------------------------------------------------------

def _validate_approach(
    approach: StructuredApproach,
    gaps: list[str],
) -> tuple[bool, float, list[str]]:
    """Validate a single approach strictly.

    Returns (is_valid, quality_score, issues).
    Approaches with quality_score below MIN_QUALITY_SCORE are rejected.
    """
    issues: list[str] = []
    score = 1.0

    # Must have enough implementation steps
    if len(approach.implementation_steps) < MIN_IMPLEMENTATION_STEPS:
        issues.append(
            f"Too few implementation steps ({len(approach.implementation_steps)}, "
            f"need >= {MIN_IMPLEMENTATION_STEPS})"
        )
        score -= 0.3

    # When gaps exist, approach should reference them
    if gaps and not approach.addresses_gaps:
        issues.append("Doesn't address any identified gaps")
        score -= 0.4

    # Must have at least one trade-off
    if not approach.trade_offs:
        issues.append("No trade-offs identified")
        score -= 0.2

    # Must have at least one risk
    if not approach.risks:
        issues.append("No risks identified")
        score -= 0.2

    # Reject overly generic approaches (empty or trivial strategy)
    if not approach.strategy or len(approach.strategy.strip()) < 15:
        issues.append("Strategy is too short or empty")
        score -= 0.3

    # Success probability must be plausible
    if approach.expected_success_probability <= 0.0:
        issues.append("Success probability is zero or negative")
        score -= 0.1

    quality = max(0.0, score)
    return quality >= MIN_QUALITY_SCORE, quality, issues


def _validate_approaches(
    approaches: list[StructuredApproach],
    gaps: list[str],
) -> tuple[list[StructuredApproach], dict[str, list[str]]]:
    """Validate all approaches, returning only those that pass.

    Returns (valid_approaches, all_issues_by_approach_id).
    """
    valid: list[StructuredApproach] = []
    all_issues: dict[str, list[str]] = {}

    for approach in approaches:
        is_valid, quality, issues = _validate_approach(approach, gaps)
        if issues:
            all_issues[approach.id] = issues
        if is_valid:
            valid.append(approach)
        else:
            logger.info(
                "THINK: Rejected approach '%s' (quality=%.2f): %s",
                approach.name, quality, "; ".join(issues),
            )

    return valid, all_issues


# ---------------------------------------------------------------------------
# Multi-factor selection algorithm with exploration bias
# ---------------------------------------------------------------------------

def _select_recommended_approach(
    approaches: list[StructuredApproach],
    gaps: list[str],
    previous_approach_names: list[str],
) -> tuple[str, str]:
    """Select best approach using multi-factor scoring with exploration bias.

    Factors considered:
    - Base success probability
    - Gap coverage bonus
    - Risk penalty (high-likelihood + high-impact risks penalized most)
    - Trade-off severity penalty
    - Novelty bonus (approaches not tried in previous iterations)
    - Complexity penalty (>10 steps)

    Has EXPLORATION_PROBABILITY chance of selecting a random non-optimal
    approach to avoid local optima.
    """
    if not approaches:
        return "", "No approaches available"

    scored: list[tuple[float, StructuredApproach]] = []

    for approach in approaches:
        score = approach.expected_success_probability

        # Gap coverage bonus
        if gaps:
            gap_overlap = len(set(approach.addresses_gaps) & set(gaps))
            coverage = gap_overlap / len(gaps)
            score += coverage * 0.3

        # Risk penalty
        for risk in approach.risks:
            if risk.likelihood == "high" and risk.impact == "high":
                score -= 0.20
            else:
                lp = _RISK_PENALTY.get(risk.likelihood, 0.05)
                ip = _RISK_PENALTY.get(risk.impact, 0.05)
                score -= (lp + ip) / 2

        # Trade-off severity penalty
        for to in approach.trade_offs:
            score -= _TRADEOFF_PENALTY.get(to.severity, 0.03)

        # Novelty bonus (prefer untried approaches)
        if previous_approach_names and approach.name not in previous_approach_names:
            score += 0.05

        # Complexity penalty for very long plans
        if len(approach.implementation_steps) > 10:
            score -= 0.03

        scored.append((score, approach))

    # Sort best-first
    scored.sort(key=lambda x: x[0], reverse=True)

    # Exploration: occasionally pick a non-best approach
    if len(scored) > 1 and random.random() < EXPLORATION_PROBABILITY:
        _, selected = random.choice(scored[1:])
        reasoning = (
            f"Exploring '{selected.name}' (exploration mode) — "
            f"base probability {selected.expected_success_probability:.0%}"
        )
        return selected.id, reasoning

    # Exploitation: pick the best
    best_score, best = scored[0]
    reasoning = (
        f"Selected '{best.name}' with adjusted score {best_score:.2f} "
        f"(base probability {best.expected_success_probability:.0%})"
    )
    if gaps:
        gap_overlap = len(set(best.addresses_gaps) & set(gaps))
        if gap_overlap > 0:
            reasoning += f", addresses {gap_overlap}/{len(gaps)} gaps"

    return best.id, reasoning


# ---------------------------------------------------------------------------
# Approach comparison matrix
# ---------------------------------------------------------------------------

def _build_comparison(
    approaches: list[StructuredApproach],
    gaps: list[str],
) -> dict[str, Any]:
    """Build comparison matrix between approaches for the PLAN node."""
    values = []
    for a in approaches:
        risk_score = sum(
            (_RISK_PENALTY.get(r.likelihood, 0.05) + _RISK_PENALTY.get(r.impact, 0.05))
            for r in a.risks
        )
        gap_count = len(set(a.addresses_gaps) & set(gaps)) if gaps else 0
        values.append({
            "approach_id": a.id,
            "name": a.name,
            "success_prob": a.expected_success_probability,
            "risk_level": round(risk_score, 3),
            "complexity": len(a.implementation_steps),
            "gap_coverage": gap_count,
            "trade_off_count": len(a.trade_offs),
        })

    key_diffs: list[str] = []
    if len(approaches) >= 2:
        probs = [a.expected_success_probability for a in approaches]
        if max(probs) - min(probs) > 0.15:
            key_diffs.append(
                f"Success probability range: {min(probs):.0%} to {max(probs):.0%}"
            )
        complexities = [len(a.implementation_steps) for a in approaches]
        if max(complexities) - min(complexities) > 3:
            key_diffs.append(
                f"Complexity range: {min(complexities)} to {max(complexities)} steps"
            )
        strategies = [a.strategy[:60] for a in approaches]
        key_diffs.append(f"Strategies: {' vs '.join(strategies)}")

    return {
        "dimensions": [
            "success_prob", "risk_level", "complexity",
            "gap_coverage", "trade_off_count",
        ],
        "values": values,
        "key_differences": key_diffs,
    }


# ---------------------------------------------------------------------------
# Prompt building (with enhanced context)
# ---------------------------------------------------------------------------

def _build_thinking_prompt(
    prompts: PromptTemplates,
    ideal_state,
    iteration: int,
    last_verification,
    state,
) -> str:
    """Build the user prompt for THINK phase using the prompt template.

    Includes: ideal state, criteria, anti-criteria, iteration context,
    lessons learned, available tools, constraints, and dependencies.
    """
    ideal_state_dict = (
        ideal_state.model_dump() if hasattr(ideal_state, "model_dump") else ideal_state
    )

    # Extract success criteria
    criteria = ideal_state_dict.get('success_criteria', [])
    criteria_text = "\n".join([
        f"- {c.get('description', 'N/A')} (target: {c.get('target_score', 0.8)})"
        for c in criteria[:5]
    ]) or "- Complete task successfully"

    # Extract anti-criteria
    anti_criteria = ideal_state_dict.get('anti_criteria', [])
    anti_text = "\n".join([
        f"- {ac.get('description', 'N/A')}"
        for ac in anti_criteria[:3]
    ]) or "- None specified"

    # Build iteration context (empty if first iteration)
    iteration_context = ""
    gaps: list[str] = []
    if iteration > 0 and last_verification:
        if hasattr(last_verification, "model_dump"):
            verif_dict = last_verification.model_dump()
        elif isinstance(last_verification, dict):
            verif_dict = last_verification
        else:
            verif_dict = {"overall_score": 0, "passed": False, "gaps": []}

        gaps = verif_dict.get('gaps', [])[:5]
        gap_lines = (
            "\n".join([f"  - {gap}" for gap in gaps]) if gaps
            else "  - None identified"
        )
        iteration_context = (
            f"## Previous Iteration (#{iteration})\n\n"
            f"**Score:** {verif_dict.get('overall_score', 0):.2f}\n"
            f"**Passed:** {verif_dict.get('passed', False)}\n\n"
            f"**Gaps identified:**\n{gap_lines}"
        )

    # Build lessons context
    lessons_context = ""
    lessons_learned = state.get("history", {}).get("lessons_learned", [])
    if lessons_learned and iteration > 0:
        recent_lessons = (
            lessons_learned[-2:] if len(lessons_learned) > 2 else lessons_learned
        )
        parts = ["## Lessons From Previous Iterations\n"]
        for lesson in recent_lessons:
            iter_num = lesson.get("iteration", "?")
            parts.append(f"### Iteration {iter_num}")
            for key, label in [
                ("what_worked", "**What worked:**"),
                ("what_failed", "**What failed:**"),
                ("adjustments", "**Recommended adjustments:**"),
            ]:
                items = lesson.get(key, [])
                if items:
                    parts.append(label)
                    for item in items[:3]:
                        parts.append(f"  - {item}")
            insights = lesson.get("insights", [])
            if insights:
                parts.append("**Key insights:**")
                for item in insights[:2]:
                    parts.append(f"  - {item}")
        lessons_context = "\n".join(parts)

    # Build tools context
    tools_context = ""
    config = state.get("config", {})
    if isinstance(config, dict):
        available_tools = config.get("available_tools", [])
        if available_tools:
            tool_lines = "\n".join([f"- {tool}" for tool in available_tools[:10]])
            tools_context = f"## Available Tools\n\n{tool_lines}"

    # Build constraints context
    constraints_context = ""
    if isinstance(config, dict):
        constraints = config.get("constraints", {})
        if isinstance(constraints, dict) and constraints:
            parts = ["## Constraints\n"]
            for key, val in constraints.items():
                parts.append(f"- **{key}:** {val}")
            constraints_context = "\n".join(parts)

    # Build dependencies context
    dependencies_context = ""
    deps = state.get("dependencies", {})
    if isinstance(deps, dict):
        critical = deps.get("critical", [])
        if critical:
            dep_lines = "\n".join([f"- {dep}" for dep in critical[:5]])
            dependencies_context = f"## Dependencies\n\n{dep_lines}"

    # Build dynamic focus items
    focus_parts = []
    if gaps:
        focus_parts.append("- Addressing the identified gaps from previous iteration")
    else:
        focus_parts.append("- Meeting success criteria")
    if lessons_learned and iteration > 0:
        focus_parts.append("- Learning from previous iteration lessons")
    focus_items = "\n".join(focus_parts)

    return prompts.get_think_prompt(
        task_description=ideal_state_dict.get('task_description', 'N/A'),
        criteria=criteria_text,
        anti_criteria=anti_text,
        iteration_context=iteration_context,
        lessons_context=lessons_context,
        focus_items=focus_items,
        tools_context=tools_context,
        constraints_context=constraints_context,
        dependencies_context=dependencies_context,
    )


# ---------------------------------------------------------------------------
# Default / fallback output
# ---------------------------------------------------------------------------

def _build_default_output(iteration: int, gaps_analyzed: list[str]) -> ThinkOutput:
    """Build a single default approach when the LLM fails to produce valid output."""
    default_approach = StructuredApproach(
        id=f"default_{uuid.uuid4().hex[:6]}",
        name="direct_execution",
        description="Proceed with standard execution using available tools",
        strategy="Execute the task directly using available tools and verify the result",
        implementation_steps=[
            "Analyze the task requirements",
            "Execute primary action using available tools",
            "Verify the result matches success criteria",
        ],
        trade_offs=[
            TradeOff(
                aspect="Simplicity",
                positive="Fast execution with minimal overhead",
                negative="May miss edge cases without deeper analysis",
                severity="medium",
            )
        ],
        risks=[
            Risk(
                id="risk_default",
                description="Limited analysis due to thinking phase failure",
                likelihood="medium",
                impact="medium",
            )
        ],
        expected_success_probability=0.5,
    )
    return ThinkOutput(
        iteration=iteration,
        approaches=[default_approach],
        gaps_analyzed=gaps_analyzed,
        constraints_considered=[],
        recommended_approach_id=default_approach.id,
        recommendation_reasoning="Default approach used due to analysis failure",
    )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def _handle_error(
    error: Exception,
    iteration: int,
    last_verification,
    start_time: float,
    metrics,
    span,
    error_type: str = "unknown",
) -> NodeStateUpdate:
    """Handle errors consistently, returning a usable fallback state.

    Distinguishes between error types for better diagnostics:
    - json_parse: LLM returned invalid JSON
    - validation: Pydantic validation failed
    - llm_error: LLM call itself failed
    - unknown: Unexpected error
    """
    if metrics:
        metrics.increment("node_invocations_total", {
            "node": "think", "success": "false", "error_type": error_type,
        })
    if span:
        span.set_attribute("error_type", error_type)
        span.record_exception(error)
        span.end()

    gaps_analyzed = _extract_gaps(last_verification)
    think_output = _build_default_output(iteration or 0, gaps_analyzed)

    return {
        "phases": {
            "think": {
                "approaches": [a.model_dump() for a in think_output.approaches],
                "recommended_id": think_output.recommended_approach_id,
                "recommendation_reasoning": think_output.recommendation_reasoning,
                "gaps_analyzed": gaps_analyzed,
                "error": {
                    "type": error_type,
                    "message": str(error)[:500],
                },
            },
        },
        "build_phase": "think",
    }


# ---------------------------------------------------------------------------
# Approach history (stored in build_state.json)
# ---------------------------------------------------------------------------

async def _load_approach_history(state_manager) -> list[dict[str, Any]]:
    """Load approach history from build_state.json."""
    if not state_manager:
        return []
    try:
        build_state = await state_manager.get_build_state()
        return build_state.get("history", {}).get("approach_history", [])
    except Exception:
        return []


async def _save_approach_history(
    state_manager,
    approach_history: list[dict[str, Any]],
    selected_approach: StructuredApproach,
    iteration: int,
    quality_score: float,
) -> None:
    """Append the selected approach to history in build_state.json."""
    if not state_manager:
        return
    try:
        approach_history.append({
            "iteration": iteration,
            "approach_name": selected_approach.name,
            "approach_id": selected_approach.id,
            "strategy_preview": selected_approach.strategy[:200],
            "success_probability": selected_approach.expected_success_probability,
            "quality_score": quality_score,
            "timestamp": utcnow_isoformat(),
        })
        await state_manager.update_build_state({
            "history": {
                "approach_history": approach_history,
            }
        })
    except Exception as e:
        logger.warning("THINK: Failed to save approach history: %s", e)


# ---------------------------------------------------------------------------
# Node factory
# ---------------------------------------------------------------------------

def create_node(
    llm_client,
    state_manager=None,
) -> NodeFunction:
    """Create the think node function.

    CQ-006: Returns NodeFunction type - async function that takes
    ProgenitorState and returns NodeStateUpdate (partial dict).
    """

    prompts = PromptTemplates()

    async def think_node(state: ProgenitorState) -> NodeStateUpdate:
        """
        THINK phase: Generate 2-3 structured approaches with explicit
        tradeoffs/risks and recommended experiment plan deltas (BL-003).

        Flow:
        1. Build prompt with full context (tools, constraints, dependencies)
        2. Call LLM with structured output
        3. Validate approaches strictly (reject low-quality ones)
        4. Retry LLM if all approaches rejected
        5. Select recommended approach via multi-factor scoring
        6. Build comparison matrix for PLAN node
        7. Store approach history in build_state.json
        """
        logger.info("THINK: Starting think phase")
        start_time = time.perf_counter()
        tracer, metrics = _get_telemetry()
        span = None

        ideal_state = state.get("ideal_state")
        iteration_state = state.get("iteration", {})
        iteration = (
            iteration_state.get("current", 0)
            if isinstance(iteration_state, dict)
            else getattr(iteration_state, "current", 0)
        )

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
        last_verification = (
            verification_state.get("current_report")
            if isinstance(verification_state, dict)
            else getattr(verification_state, "current_report", None)
        )

        # Build prompt with enhanced context
        prompt = _build_thinking_prompt(
            prompts, ideal_state, iteration or 0, last_verification, state,
        )

        # Get structured output response format
        response_format = _get_response_format(llm_client)
        system_prompt = THINK_SYSTEM_PROMPT

        # If using json_object fallback (older Azure), add json instruction
        if response_format.get("type") == "json_object":
            system_prompt += (
                "\n\nReturn a single JSON object and nothing else (valid json)."
            )

        # Load approach history for exploration/novelty scoring
        approach_history = await _load_approach_history(state_manager)
        previous_names = [h.get("approach_name", "") for h in approach_history]

        gaps_analyzed = _extract_gaps(last_verification)
        valid_approaches: list[StructuredApproach] = []
        all_issues: dict[str, list[str]] = {}
        raw_response = ""

        try:
            # Retry loop: generate approaches, validate, retry if all rejected
            for attempt in range(MAX_LLM_RETRIES + 1):
                raw_response = await llm_client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    system_prompt=system_prompt,
                    temperature=0.7,
                    response_format=response_format,
                )

                raw_data = json.loads(raw_response)
                think_resp = ThinkResponse.model_validate(raw_data)

                approaches = _convert_approaches(think_resp)

                if not approaches:
                    logger.warning(
                        "THINK: LLM returned no approaches (attempt %d/%d)",
                        attempt + 1, MAX_LLM_RETRIES + 1,
                    )
                    if attempt < MAX_LLM_RETRIES:
                        continue
                    break

                # Strict validation
                valid_approaches, all_issues = _validate_approaches(
                    approaches, gaps_analyzed,
                )

                if valid_approaches:
                    logger.info(
                        "THINK: %d/%d approaches passed validation (attempt %d)",
                        len(valid_approaches), len(approaches), attempt + 1,
                    )
                    break

                logger.warning(
                    "THINK: All %d approaches rejected on attempt %d/%d",
                    len(approaches), attempt + 1, MAX_LLM_RETRIES + 1,
                )

            # Fall back to default if nothing survived
            if not valid_approaches:
                logger.warning("THINK: No valid approaches after retries, using default")
                default_output = _build_default_output(iteration or 0, gaps_analyzed)
                valid_approaches = list(default_output.approaches)

            # Multi-factor selection with exploration bias
            rec_id, rec_reasoning = _select_recommended_approach(
                valid_approaches, gaps_analyzed, previous_names,
            )

            # Build comparison matrix
            comparison = _build_comparison(valid_approaches, gaps_analyzed)

            # Build final ThinkOutput
            think_output = ThinkOutput(
                iteration=iteration or 0,
                approaches=valid_approaches,
                gaps_analyzed=gaps_analyzed,
                constraints_considered=[],
                recommended_approach_id=rec_id,
                recommendation_reasoning=rec_reasoning,
            )

            # Telemetry
            if span:
                span.add_event("approaches_generated", {
                    "count": len(think_output.approaches),
                    "prompt_length": len(prompt),
                    "structured_output": True,
                    "validation_issues": len(all_issues),
                })

            # Audit trail + build messages
            if state_manager:
                await state_manager.log_decision({
                    "event": "thinking_phase",
                    "iteration": iteration,
                    "approaches_generated": len(think_output.approaches),
                    "approach_names": [a.name for a in think_output.approaches],
                    "recommended_approach": think_output.recommended_approach_id,
                    "recommendation_reasoning": rec_reasoning,
                    "validation_issues": {
                        k: v for k, v in all_issues.items()
                    } if all_issues else None,
                    "timestamp": utcnow_isoformat(),
                })
                response_preview = (
                    raw_response[:500] + "..."
                    if len(raw_response) > 500 else raw_response
                )
                await state_manager.add_build_message({
                    "phase": "think",
                    "iteration": iteration,
                    "prompt_preview": (
                        prompt[:500] + "..." if len(prompt) > 500 else prompt
                    ),
                    "response_preview": response_preview,
                    "timestamp": utcnow_isoformat(),
                })

            # Store approach history
            selected = next(
                (a for a in valid_approaches if a.id == rec_id), None,
            )
            if selected:
                # Compute quality score for the selected approach
                _, quality, _ = _validate_approach(selected, gaps_analyzed)
                await _save_approach_history(
                    state_manager, approach_history, selected,
                    iteration or 0, quality,
                )

            # Record metrics
            duration = time.perf_counter() - start_time
            if metrics:
                metrics.record_histogram(
                    "node_duration_seconds", duration, {"node": "think"},
                )
                metrics.increment(
                    "node_invocations_total", {"node": "think", "success": "true"},
                )

            if span:
                span.set_attribute("duration_seconds", duration)
                span.set_attribute("approaches_count", len(think_output.approaches))
                span.end()

            approaches_dicts = [a.model_dump() for a in think_output.approaches]

            return {
                "phases": {
                    "think": {
                        "approaches": approaches_dicts,
                        "recommended_id": think_output.recommended_approach_id,
                        "recommendation_reasoning": rec_reasoning,
                        "gaps_analyzed": gaps_analyzed,
                        "comparison": comparison,
                        "validation_issues": (
                            {k: v for k, v in all_issues.items()}
                            if all_issues else {}
                        ),
                    },
                },
                "build_phase": "think",
            }

        except json.JSONDecodeError as e:
            logger.warning(
                "THINK: Could not parse JSON from LLM response: %s", e,
            )
            return _handle_error(
                e, iteration, last_verification,
                start_time, metrics, span, error_type="json_parse",
            )

        except ValueError as e:
            logger.warning(
                "THINK: Pydantic validation error: %s", e,
            )
            return _handle_error(
                e, iteration, last_verification,
                start_time, metrics, span, error_type="validation",
            )

        except Exception as e:
            logger.error("THINK phase error: %s", e, exc_info=True)
            return _handle_error(
                e, iteration, last_verification,
                start_time, metrics, span, error_type="llm_error",
            )

    return think_node
