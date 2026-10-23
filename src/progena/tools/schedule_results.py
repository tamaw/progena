"""System tools for inspecting persisted schedule and cron run results."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, Field, model_validator

from src.progena.sessions.messages import extract_message_text, get_last_ai_message

from .protocol import BaseSystemTool, ToolContext, ToolResult


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


def _schedule_type(schedule_id: str) -> str:
    normalized = str(schedule_id).strip().lower()
    if normalized.startswith("cron_"):
        return "cron"
    if normalized.startswith("sched_"):
        return "schedule"
    return "unknown"


def _coerce_run_dict(run: Any) -> dict[str, Any]:
    if hasattr(run, "model_dump"):
        return cast(dict[str, Any], run.model_dump(mode="json"))
    if isinstance(run, dict):
        return dict(run)
    return {}


def _coerce_schedule_dict(entry: Any) -> dict[str, Any]:
    if hasattr(entry, "model_dump"):
        return cast(dict[str, Any], entry.model_dump(mode="json"))
    if isinstance(entry, dict):
        return dict(entry)
    return {}


def _summarize_checkpoint_message(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        message = message.model_dump(mode="json")

    role = None
    content = None
    if isinstance(message, dict):
        role = message.get("role") or message.get("type")
        content = extract_message_text(message)
        if content is None:
            raw_content = message.get("content")
            if isinstance(raw_content, str):
                content = raw_content.strip() or None
            elif raw_content is not None:
                content = str(raw_content).strip() or None
    else:
        role = getattr(message, "role", None) or getattr(message, "type", None)
        content = extract_message_text(message)
        if content is None:
            raw_content = getattr(message, "content", None)
            if isinstance(raw_content, str):
                content = raw_content.strip() or None
            elif raw_content is not None:
                content = str(raw_content).strip() or None

    return {
        "role": str(role or "unknown"),
        "content": content or "",
    }


async def _load_checkpoint_snapshot(thread_state: Any, thread_id: str) -> dict[str, Any] | None:
    """Read a run thread's latest state natively from the Progena agent graph."""
    if thread_state is None:
        return None
    snapshot = await thread_state(thread_id)
    if snapshot is None or snapshot.created_at is None:
        return None

    raw_messages = (snapshot.values or {}).get("messages") or []
    messages = [_summarize_checkpoint_message(message) for message in raw_messages]
    assistant_message = get_last_ai_message(raw_messages)
    assistant_text = (
        extract_message_text(assistant_message) if assistant_message is not None else None
    )
    return {
        "thread_id": thread_id,
        "checkpoint_id": (snapshot.config.get("configurable") or {}).get("checkpoint_id"),
        "checkpoint_ts": snapshot.created_at,
        "messages": messages,
        "assistant_response": assistant_text,
    }


def _summarize_run(run: dict[str, Any]) -> dict[str, Any]:
    response = str(run.get("response") or "").strip()
    error = str(run.get("error") or "").strip()
    return {
        "run_id": run.get("run_id"),
        "schedule_id": run.get("schedule_id"),
        "schedule_type": _schedule_type(str(run.get("schedule_id") or "")),
        "thread_id": run.get("thread_id"),
        "message": run.get("message"),
        "user_request": run.get("user_request"),
        "status": run.get("status"),
        "result_status": run.get("result_status"),
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "response_preview": response[:400] if response else None,
        "error": error or None,
    }


class ListScheduleRunsArgs(BaseModel):
    """Structured arguments for listing recent schedule or cron runs."""

    schedule_id: str | None = Field(
        default=None,
        description="Optional schedule ID to filter on, for example cron_xxx or sched_xxx.",
    )
    status: str | None = Field(
        default=None,
        description="Optional run status filter such as queued, running, completed, or error.",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of runs to return.",
    )


class GetScheduleRunResultArgs(BaseModel):
    """Structured arguments for one schedule or cron run lookup."""

    run_id: str | None = Field(default=None, description="Specific schedule run ID to inspect.")
    schedule_id: str | None = Field(
        default=None,
        description="Schedule ID whose latest run should be returned when run_id is omitted.",
    )
    include_messages: bool = Field(
        default=True,
        description="Include checkpoint thread messages from Postgres when available.",
    )
    message_limit: int = Field(
        default=8,
        ge=1,
        le=50,
        description="Maximum number of checkpoint messages to include.",
    )

    @model_validator(mode="after")
    def validate_identity(self) -> GetScheduleRunResultArgs:
        if not self.run_id and not self.schedule_id:
            raise ValueError("Either run_id or schedule_id is required")
        return self


class ListScheduleRunsTool(BaseSystemTool):
    """List recent persisted schedule and cron executions."""

    @property
    def args_model(self) -> type[BaseModel]:
        return ListScheduleRunsArgs

    @property
    def name(self) -> str:
        return "list_schedule_runs"

    @property
    def description(self) -> str:
        return "List recent cron and schedule runs, including status, thread_id, and result summary"

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None or not hasattr(context.state_manager, "get_schedule_runs"):
            return ToolResult.fail("state_manager unavailable; cannot query schedule runs")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        schedule_id = str(args.get("schedule_id") or "").strip() or None
        status = str(args.get("status") or "").strip().lower() or None
        limit = int(args.get("limit") or 10)

        runs = [_coerce_run_dict(run) for run in await context.state_manager.get_schedule_runs()]
        if schedule_id:
            runs = [run for run in runs if str(run.get("schedule_id") or "") == schedule_id]
        if status:
            runs = [run for run in runs if str(run.get("status") or "").strip().lower() == status]
        runs.sort(
            key=lambda run: (
                _parse_timestamp(run.get("completed_at")),
                _parse_timestamp(run.get("started_at")),
                _parse_timestamp(run.get("created_at")),
            ),
            reverse=True,
        )
        selected = runs[:limit]
        return ToolResult.ok(
            {
                "runs": [_summarize_run(run) for run in selected],
                "total_count": len(selected),
                "matched_count": len(runs),
                "filters": {
                    "schedule_id": schedule_id,
                    "status": status,
                    "limit": limit,
                },
            }
        )


class GetScheduleRunResultTool(BaseSystemTool):
    """Inspect one persisted schedule or cron run in detail."""

    @property
    def args_model(self) -> type[BaseModel]:
        return GetScheduleRunResultArgs

    @property
    def name(self) -> str:
        return "get_schedule_run_result"

    @property
    def description(self) -> str:
        return "Get the detailed result of one cron or schedule run, including checkpoint thread messages from Postgres when available"

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None or not hasattr(context.state_manager, "get_schedule_runs"):
            return ToolResult.fail("state_manager unavailable; cannot query schedule runs")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        run_id = str(args.get("run_id") or "").strip() or None
        schedule_id = str(args.get("schedule_id") or "").strip() or None
        include_messages = bool(args.get("include_messages", True))
        message_limit = int(args.get("message_limit") or 8)

        runs = [_coerce_run_dict(run) for run in await context.state_manager.get_schedule_runs()]
        target: dict[str, Any] | None = None
        if run_id:
            target = next((run for run in runs if str(run.get("run_id") or "") == run_id), None)
        elif schedule_id:
            matching = [run for run in runs if str(run.get("schedule_id") or "") == schedule_id]
            matching.sort(
                key=lambda run: (
                    _parse_timestamp(run.get("completed_at")),
                    _parse_timestamp(run.get("started_at")),
                    _parse_timestamp(run.get("created_at")),
                ),
                reverse=True,
            )
            target = matching[0] if matching else None

        if target is None:
            lookup = run_id or schedule_id or "unknown"
            return ToolResult.fail(f"No schedule run found for {lookup}")

        schedule_entry = None
        if hasattr(context.state_manager, "get_schedule"):
            schedule = await context.state_manager.get_schedule()
            match = (
                schedule.get(str(target.get("schedule_id") or ""))
                if isinstance(schedule, dict)
                else None
            )
            schedule_entry = _coerce_schedule_dict(match) if match is not None else None

        checkpoint = None
        checkpoint_note = None
        thread_id = str(target.get("thread_id") or "").strip()
        if include_messages and thread_id:
            checkpoint = await _load_checkpoint_snapshot(context.thread_state, thread_id)
            if checkpoint is not None:
                checkpoint["messages"] = checkpoint.get("messages", [])[-message_limit:]
            else:
                checkpoint_note = (
                    "Checkpoint snapshot unavailable: the run thread has no saved state."
                )

        payload: dict[str, Any] = {
            "run": _summarize_run(target) | {"response": target.get("response")},
            "schedule": schedule_entry,
            "checkpoint": checkpoint,
        }
        if checkpoint_note:
            payload["checkpoint_note"] = checkpoint_note
        return ToolResult.ok(payload)
