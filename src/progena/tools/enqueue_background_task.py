"""Queue a background work item for the Heartbeat loop."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from src.progena.orchestration.state import BackgroundTaskEntry
from src.progena.sessions.models import generate_thread_id

from .protocol import BaseSystemTool, ToolContext, ToolResult


class EnqueueBackgroundTaskArgs(BaseModel):
    request: str = Field(description="Prompt to queue for Heartbeat background execution.")
    notify_on_completion: bool = Field(
        default=True,
        description="Whether Heartbeat should emit a user-visible callback when the queued work finishes.",
    )
    isolated_thread: bool = Field(
        default=True,
        description="Run the queued work on an isolated thread so it does not mutate the main conversation.",
    )
    completion_event_type: str = Field(
        default="agent_message",
        description="SSE event type to emit when the queued work completes.",
    )


class EnqueueBackgroundTaskTool(BaseSystemTool):
    """Queue a prompt for later Heartbeat execution."""

    @property
    def name(self) -> str:
        return "enqueue_background_task"

    @property
    def description(self) -> str:
        return "Queue a prompt for the Heartbeat loop to execute in the background"

    @property
    def args_model(self) -> type[BaseModel]:
        return EnqueueBackgroundTaskArgs

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("State manager is not available.")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        request_text = str(args.get("request") or context.user_input or "").strip()
        if not request_text:
            return ToolResult.fail("Missing 'request'")

        isolated_thread = bool(args.get("isolated_thread", True))
        current_thread_id = (
            str(context.state.get("thread_id") or "").strip()
            if isinstance(context.state, dict)
            else ""
        )
        target_thread_id = (
            generate_thread_id() if isolated_thread or not current_thread_id else current_thread_id
        )
        completion_event_type = (
            str(args.get("completion_event_type") or "agent_message").strip() or "agent_message"
        )
        notify_on_completion = bool(args.get("notify_on_completion", True))
        task_id = f"bg_{uuid.uuid4().hex[:12]}"

        entry = BackgroundTaskEntry(
            id=task_id,
            content=request_text,
            metadata={
                "thread_id": target_thread_id,
                "notify_on_completion": notify_on_completion,
                "completion_event_type": completion_event_type,
                "source": "enqueue_background_task",
            },
        )
        await context.state_manager.enqueue_background_task(entry)
        queue_depth = len(await context.state_manager.get_background_tasks())
        return ToolResult.ok(
            {
                "success": True,
                "task_id": task_id,
                "queued": True,
                "queue_depth": queue_depth,
                "thread_id": target_thread_id,
                "isolated_thread": isolated_thread,
                "notify_on_completion": notify_on_completion,
                "completion_event_type": completion_event_type,
            }
        )
