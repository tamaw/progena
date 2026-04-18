"""Validate subagent — score the execution evidence against plan criteria."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from ....client import BuilderClient
from ....prompts import load_prompt
from ..state import ValidateState

logger = logging.getLogger(__name__)


class _CriterionEval(BaseModel):
    criterion: str
    met: bool
    evidence: str = ""


class ValidationReport(BaseModel):
    """Structured report returned by the validate subagent."""

    verdict: Literal["pass", "pass_with_concerns", "fail"]
    confidence: float = Field(ge=0.0, le=1.0)
    score: float = Field(ge=0.0, le=1.0)
    criteria: list[_CriterionEval] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    summary: str


def create_score_node(
    client: BuilderClient,
) -> Callable[[ValidateState], Awaitable[dict[str, Any]]]:
    system_prompt = load_prompt("validate_system.md").strip()

    async def score(state: ValidateState) -> dict:
        planned_build = state.get("planned_build")
        evidence_text = (state.get("evidence_text") or "").strip()
        request_text = (state.get("request_text") or "").strip()

        plan_json = "{}"
        if planned_build is not None:
            plan_json = json.dumps(
                {
                    **planned_build.plan.model_dump(),
                    "reasoning": planned_build.reasoning,
                    "planned_at_utc": planned_build.planned_at_utc,
                },
                indent=2,
                sort_keys=True,
            )

        user_message = (
            f"Original request:\n{request_text or '(none)'}\n\n"
            f"Plan (JSON):\n{plan_json}\n\n"
            f"Execution evidence:\n{evidence_text or '(no evidence captured)'}"
        )

        try:
            report, _reasoning = await client.chat_structured_with_reasoning(
                messages=[{"role": "user", "content": user_message}],
                response_model=ValidationReport,
                schema_name="builder_validation_report",
                system_prompt=system_prompt,
                max_tokens=4096,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Validate subagent failed to score evidence")
            fallback = {
                "verdict": "fail",
                "confidence": 0.0,
                "score": 0.0,
                "criteria": [],
                "gaps": [f"validation_error: {exc}"],
                "next_actions": ["Re-run validation with richer evidence."],
                "summary": f"Validation failed to produce a report: {exc}",
            }
            return {
                "validation_report": fallback,
                "result_text": fallback["summary"],
            }

        report_dict = report.model_dump()
        logger.info(
            "Validate subagent: verdict=%s score=%.2f confidence=%.2f",
            report.verdict,
            report.score,
            report.confidence,
        )
        return {
            "validation_report": report_dict,
            "result_text": report.summary,
        }

    return score
