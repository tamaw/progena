"""Get the latest state for a tracked A2A task."""

from __future__ import annotations

from typing import Any

from ..runtime.a2a_tasks import (
    DEFAULT_A2A_HISTORY_LENGTH,
    build_task_response,
    refresh_tracked_a2a_task,
)
from .protocol import BaseSystemTool, ToolContext, ToolResult


class GetA2ATaskTool(BaseSystemTool):
    """Query progress or final results for a tracked A2A task."""

    @property
    def name(self) -> str:
        return "get_a2a_task"

    @property
    def description(self) -> str:
        return "Get the current status, progress, or final result of a tracked A2A task by task_id"

    @property
    def keywords(self) -> list[str]:
        return [
            "get a2a task",
            "task status",
            "task progress",
            "task result",
            "check task",
        ]

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot query tracked A2A tasks")

        args: dict[str, Any] = context.tool_args if isinstance(context.tool_args, dict) else {}
        task_id = str(args.get("task_id") or args.get("id") or context.user_input or "").strip()
        history_length = int(args.get("history_length") or DEFAULT_A2A_HISTORY_LENGTH)
        refresh_arg = args.get("refresh", True)
        refresh = (
            refresh_arg
            if isinstance(refresh_arg, bool)
            else str(refresh_arg).strip().lower() in {"1", "true", "yes", "on"}
        )

        if not task_id:
            return ToolResult.fail("Missing 'task_id'")

        record = await context.state_manager.get_a2a_task(task_id)
        if record is None:
            return ToolResult.fail(f"Unknown tracked A2A task: {task_id}")

        refresh_error: str | None = None
        if refresh:
            try:
                record = await refresh_tracked_a2a_task(
                    context.state_manager,
                    task_id,
                    tool_registry=context.tool_registry,
                    history_length=history_length,
                )
            except Exception as exc:
                refresh_error = str(exc)

        payload = build_task_response(record)
        if refresh_error:
            payload["refresh_error"] = refresh_error
        return ToolResult.ok(payload)
