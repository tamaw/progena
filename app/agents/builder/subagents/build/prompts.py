"""Prompt assembly helpers for the build subagent.

Lifted from the original ``app/agents/builder/agent.py`` build flow so the
build subagent owns its prompt construction without reaching back into the
top-level service module.
"""

from __future__ import annotations

import json
from typing import Any

from ...client import PlannedBuild
from ...prompts import load_prompt, load_skill_context


def _as_bullets(items: list[str], fallback: str) -> str:
    if not items:
        return fallback
    return "\n".join(f"- {item}" for item in items)


def build_setup_prompt(
    request_text: str,
    context: dict[str, Any],
    planned_build: PlannedBuild,
) -> str:
    ctx = json.dumps(context, indent=2, sort_keys=True) if context else "{}"
    plan = planned_build.plan
    return load_prompt(
        "task_setup.md",
        request_text=request_text.strip(),
        context_json=ctx,
        task_name=plan.task_name,
        work_item_id=plan.work_item_id,
        branch_name=plan.branch_name,
        description=plan.description,
        planned_at_utc=planned_build.planned_at_utc,
        reasoning=planned_build.reasoning or "No reasoning returned.",
        skill_context=load_skill_context(["git-workflow", "task-setup"]),
    ).strip()


def build_execution_prompt(
    request_text: str,
    context: dict[str, Any],
    planned_build: PlannedBuild,
) -> str:
    ctx = json.dumps(context, indent=2, sort_keys=True) if context else "{}"
    plan = planned_build.plan
    return load_prompt(
        "execution_template.md",
        request_text=request_text.strip(),
        context_json=ctx,
        task_name=plan.task_name,
        work_item_id=plan.work_item_id,
        branch_name=plan.branch_name,
        description=plan.description,
        copilot_goal=plan.copilot_goal or "Implement the requested change safely.",
        success_criteria=_as_bullets(plan.success_criteria, "- none provided"),
        validation_targets=_as_bullets(plan.validation_targets, "- pytest\n- pyright\n- ruff"),
        reasoning=planned_build.reasoning or "No reasoning returned.",
        planned_at_utc=planned_build.planned_at_utc,
        skill_context=load_skill_context(),
    ).strip()


def _validation_summary_block(report: dict[str, Any] | None) -> str:
    if not report:
        return "(no validation report available)"
    verdict = report.get("verdict", "unknown")
    score = report.get("score", "n/a")
    confidence = report.get("confidence", "n/a")
    summary = report.get("summary", "")
    gaps = report.get("gaps") or []
    gaps_block = "\n".join(f"- {g}" for g in gaps) if gaps else "- none reported"
    return (
        f"verdict={verdict} score={score} confidence={confidence}\n"
        f"summary: {summary}\n"
        f"gaps:\n{gaps_block}"
    )


def build_teardown_prompt(
    request_text: str,
    context: dict[str, Any],
    planned_build: PlannedBuild,
    validation_report: dict[str, Any] | None = None,
) -> str:
    ctx = json.dumps(context, indent=2, sort_keys=True) if context else "{}"
    plan = planned_build.plan
    return load_prompt(
        "task_teardown.md",
        request_text=request_text.strip(),
        context_json=ctx,
        task_name=plan.task_name,
        work_item_id=plan.work_item_id,
        branch_name=plan.branch_name,
        description=plan.description,
        planned_at_utc=planned_build.planned_at_utc,
        reasoning=planned_build.reasoning or "No reasoning returned.",
        skill_context=load_skill_context(["git-workflow", "task-teardown"]),
        validation_summary=_validation_summary_block(validation_report),
    ).strip()


def build_teardown_failed_prompt(
    request_text: str,
    context: dict[str, Any],
    planned_build: PlannedBuild,
    validation_report: dict[str, Any] | None,
) -> str:
    """Render the cleanup-only teardown prompt used when validation fails."""
    ctx = json.dumps(context, indent=2, sort_keys=True) if context else "{}"
    plan = planned_build.plan
    return load_prompt(
        "task_teardown_failed.md",
        request_text=request_text.strip(),
        context_json=ctx,
        task_name=plan.task_name,
        work_item_id=plan.work_item_id,
        branch_name=plan.branch_name,
        description=plan.description,
        planned_at_utc=planned_build.planned_at_utc,
        reasoning=planned_build.reasoning or "No reasoning returned.",
        skill_context=load_skill_context(["git-workflow", "task-teardown"]),
        validation_summary=_validation_summary_block(validation_report),
    ).strip()


def extract_text(result: dict[str, Any]) -> str:
    """Extract user-facing text from a DeepAgent result dict."""
    messages = result.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            content = getattr(msg, "content", None) or (
                msg.get("content") if isinstance(msg, dict) else None
            )
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts: list[str] = []
                for p in content:
                    if isinstance(p, str):
                        parts.append(p)
                    elif isinstance(p, dict):
                        parts.append(str(p.get("text") or p.get("value") or ""))
                text = "".join(parts).strip()
                if text:
                    return text
    for key in ("output_text", "final_text"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return "Builder completed but produced no text response."


_TOOL_OUTPUT_TRUNC = 2000


def _msg_attr(msg: Any, key: str) -> Any:
    """Return ``msg.<key>`` or ``msg[key]`` if available."""
    val = getattr(msg, key, None)
    if val is not None:
        return val
    if isinstance(msg, dict):
        return msg.get(key)
    return None


def _msg_type(msg: Any) -> str:
    """Best-effort message-type classification."""
    msg_type = _msg_attr(msg, "type")
    if isinstance(msg_type, str) and msg_type:
        return msg_type
    role = _msg_attr(msg, "role")
    if isinstance(role, str) and role:
        return role
    cls = msg.__class__.__name__.lower() if hasattr(msg, "__class__") else ""
    if "tool" in cls:
        return "tool"
    if "ai" in cls or "assistant" in cls:
        return "ai"
    if "human" in cls or "user" in cls:
        return "human"
    if "system" in cls:
        return "system"
    return "unknown"


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                parts.append(str(p.get("text") or p.get("value") or ""))
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def _truncate(text: str, limit: int = _TOOL_OUTPUT_TRUNC) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def serialize_transcript(result: dict[str, Any]) -> str:
    """Serialize a DeepAgent result into a structured evidence transcript.

    Includes assistant text **and** tool calls (name + args) and tool results
    (output, truncated). This is the form fed to the validate subagent so its
    verdict reflects real evidence rather than narrative summaries.
    """
    messages = result.get("messages")
    if not isinstance(messages, list):
        return _truncate(_content_to_text(result.get("output_text") or ""))

    sections: list[str] = []
    for msg in messages:
        kind = _msg_type(msg)
        if kind in {"system"}:
            continue
        if kind in {"human", "user"}:
            continue
        if kind in {"ai", "assistant"}:
            text = _content_to_text(_msg_attr(msg, "content")).strip()
            if text:
                sections.append(f"[assistant]\n{_truncate(text)}")
            tool_calls = _msg_attr(msg, "tool_calls") or []
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    name = (
                        tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                    ) or "<unknown>"
                    args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)
                    try:
                        args_text = json.dumps(args, sort_keys=True, default=str)
                    except (TypeError, ValueError):
                        args_text = str(args)
                    sections.append(f"[tool_call] name={name}\nargs={_truncate(args_text, 800)}")
        elif kind == "tool":
            name = _msg_attr(msg, "name") or "<tool>"
            status = _msg_attr(msg, "status")
            output = _content_to_text(_msg_attr(msg, "content")).strip()
            header = f"[tool_result] name={name}"
            if status:
                header += f" status={status}"
            sections.append(f"{header}\n{_truncate(output)}")
        else:
            text = _content_to_text(_msg_attr(msg, "content")).strip()
            if text:
                sections.append(f"[{kind}]\n{_truncate(text)}")

    if not sections:
        return "(no transcript captured)"
    return "\n\n".join(sections)
