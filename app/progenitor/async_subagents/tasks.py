"""Helpers for tracked DeepAgents async subagent tasks."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from deepagents.middleware.async_subagents import AsyncSubAgent
from langgraph_sdk import get_client

from ..state.models import AsyncSubagentTaskRecord, AsyncSubagentTaskStatus
from ..utils import utcnow

HELLO_WORLD_ASYNC_ASSISTANT_ID = "hello-world-async"
HELLO_WORLD_ASYNC_RESULT = "HELLO_FROM_ASYNC_SUBAGENT"


def build_tracked_task_record(
    task: dict[str, Any],
    *,
    spec: AsyncSubAgent,
    parent_thread_id: str | None,
    request_text: str,
    request_metadata: dict[str, Any] | None = None,
    source: str = "start_async_task",
    notify_on_completion: bool = True,
) -> AsyncSubagentTaskRecord:
    """Build a persisted async-subagent record from DeepAgents task metadata."""
    return AsyncSubagentTaskRecord(
        task_id=str(task["task_id"]),
        agent_name=str(task["agent_name"]),
        assistant_id=str(spec["graph_id"]),
        agent_url=str(spec.get("url") or ""),
        parent_thread_id=parent_thread_id,
        run_id=str(task["run_id"]),
        source=source,
        request_text=request_text,
        request_metadata=dict(request_metadata or {}),
        status=AsyncSubagentTaskStatus(str(task["status"])),
        notify_on_completion=notify_on_completion,
    )


def build_completion_message(record: AsyncSubagentTaskRecord) -> str:
    """Build a user-visible completion message for an async subagent."""
    if record.status == "success":
        result = record.summary or record.latest_message or "(completed with no result)"
        return (
            f"Async subagent {record.agent_name} finished. "
            f"task_id={record.task_id}. Result: {result}"
        )
    if record.status == "cancelled":
        return f"Async subagent {record.agent_name} was cancelled. task_id={record.task_id}."
    error = record.last_error or "unknown error"
    return f"Async subagent {record.agent_name} failed. task_id={record.task_id}. Error: {error}"


async def refresh_tracked_async_subagent_task(
    state_manager: Any,
    task_id: str,
) -> AsyncSubagentTaskRecord:
    """Fetch the latest task snapshot from the async-subagent server and persist it."""
    record = await state_manager.get_async_subagent_task(task_id)
    if record is None:
        raise LookupError(f"Unknown tracked async subagent task: {task_id}")

    client = get_client(url=record.agent_url or None)
    run = await client.runs.get(thread_id=record.task_id, run_id=record.run_id)

    summary: str | None = record.summary
    latest_message: str | None = record.latest_message
    last_error: str | None = record.last_error
    completed_at: datetime | None = record.completed_at
    status = str(run.get("status") or record.status)

    if status == "success":
        thread = await client.threads.get(thread_id=record.task_id)
        values = thread.get("values") or {}
        messages = values.get("messages") or []
        if messages:
            last = messages[-1]
            latest_message = last.get("content", "") if isinstance(last, dict) else str(last)
            summary = latest_message
        else:
            latest_message = "(completed with no output messages)"
            summary = latest_message
        completed_at = completed_at or utcnow()
        last_error = None
    elif status in {"error", "cancelled"}:
        error_text = run.get("error")
        last_error = str(error_text) if error_text else last_error
        completed_at = completed_at or utcnow()

    await state_manager.update_async_subagent_task(
        task_id,
        {
            "status": status,
            "summary": summary,
            "latest_message": latest_message,
            "last_error": last_error,
            "latest_run": dict(run),
            "updated_at": utcnow(),
            "completed_at": completed_at,
        },
    )
    refreshed = await state_manager.get_async_subagent_task(task_id)
    if refreshed is None:
        raise LookupError(f"Tracked async subagent task disappeared: {task_id}")
    return cast(AsyncSubagentTaskRecord, refreshed)
