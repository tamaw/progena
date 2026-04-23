"""Get the latest state for a tracked A2A task."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..runtime.a2a_tasks import (
    DEFAULT_A2A_HISTORY_LENGTH,
    build_task_response,
    refresh_tracked_a2a_task,
)
from .protocol import BaseSystemTool, ToolContext, ToolResult


class GetA2ATaskArgs(BaseModel):
    """Structured arguments for tracked A2A task lookup."""

    task_id: str = Field(description="Tracked task ID to look up.")
    refresh: bool = Field(
        default=True,
        description="Refresh task state from the remote agent before returning.",
    )
    history_length: int = Field(
        default=DEFAULT_A2A_HISTORY_LENGTH,
        description="Maximum number of recent task history events to include.",
    )


class GetA2ATaskTool(BaseSystemTool):
    """Query progress or final results for a tracked A2A task."""

    @property
    def args_model(self) -> type[BaseModel]:
        return GetA2ATaskArgs

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

    @property
    def deepagent_args_schema(self) -> type[BaseModel] | None:
        return GetA2ATaskArgs

    def deepagent_prepare_args(self, **kwargs: Any) -> tuple[str, dict[str, Any] | None]:
        task_id = str(kwargs.get("task_id") or "")
        return task_id, {
            "task_id": task_id,
            "history_length": kwargs.get("history_length", DEFAULT_A2A_HISTORY_LENGTH),
            "refresh": kwargs.get("refresh", True),
        }

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
