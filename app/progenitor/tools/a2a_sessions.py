"""A2A task-session tools for sending, observing, canceling, and listing remote work."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, cast

from pydantic import BaseModel, Field

from ..a2a import (
    build_task_response,
    cancel_tracked_a2a_task,
    dispatch_tracked_a2a_task,
    extract_message_text,
    refresh_tracked_a2a_task,
)
from ..sse.stream_events import build_a2a_call_progress_event, emit_stream_event
from ..state.models import A2ATaskRecord
from .protocol import BaseSystemTool, ToolContext, ToolResult

_MAX_WAIT_SECONDS = 300.0
_SETTLED_STATUSES = {"completed", "failed", "canceled", "cancelled", "input-required"}


class A2ASendArgs(BaseModel):
    """Arguments for a blocking A2A message send."""

    agent: str = Field(description="Exact registered A2A agent name or capability.")
    message: str = Field(description="Message to send to the agent.")
    task_id: str | None = Field(default=None, description="Existing A2A task to continue.")
    context_id: str | None = Field(default=None, description="Existing A2A context to continue.")
    wait_seconds: float = Field(default=60.0, description="Seconds to wait, capped at 300.")
    context: dict[str, Any] | None = Field(default=None, description="Optional message metadata.")


class A2ASendAsyncArgs(BaseModel):
    """Arguments for an asynchronous A2A message send."""

    agent: str = Field(description="Exact registered A2A agent name or capability.")
    message: str = Field(description="Message to send to the agent.")
    task_id: str | None = Field(default=None, description="Existing A2A task to continue.")
    context_id: str | None = Field(default=None, description="Existing A2A context to continue.")
    context: dict[str, Any] | None = Field(default=None, description="Optional message metadata.")


class A2AStatusArgs(BaseModel):
    """Arguments for inspecting a tracked A2A task."""

    task_id: str = Field(description="Tracked A2A task ID.")
    wait_seconds: float | None = Field(
        default=None, description="Optional seconds to refresh until settled, capped at 300."
    )


class A2ACancelArgs(BaseModel):
    """Arguments for canceling a tracked A2A task."""

    task_id: str = Field(description="Tracked A2A task ID to cancel.")


class A2AListArgs(BaseModel):
    """Arguments for listing tracked A2A tasks."""

    agent: str | None = Field(default=None, description="Optional agent-name filter.")
    status: str | None = Field(default=None, description="Optional normalized status filter.")


class _A2ATool(BaseSystemTool):
    async def _resolve_agent(
        self, context: ToolContext, agent: str
    ) -> tuple[str, dict[str, Any]] | ToolResult:
        if context.tool_registry is None:
            return ToolResult.fail("tool_registry unavailable")
        await _refresh_registry(context.tool_registry)
        cfg = _find_agent(context.tool_registry, agent)
        if not isinstance(cfg, dict):
            return ToolResult.fail(f"Unknown agent: {agent}")
        if cfg.get("enabled", True) is False:
            return ToolResult.fail(f"Agent '{agent}' is disabled")
        if str(cfg.get("transport") or "http").strip().lower() == "acp":
            return ToolResult.fail(f"Agent '{agent}' uses ACP; start it with acp_start instead.")
        if str(cfg.get("transport") or "http").strip().lower() != "http":
            return ToolResult.fail(f"Agent '{agent}' is not an HTTP A2A agent")
        resolved_name = str(cfg.get("name") or agent).strip() or agent
        return resolved_name, cfg


class A2ASendTool(_A2ATool):
    @property
    def args_model(self) -> type[BaseModel]:
        return A2ASendArgs

    @property
    def name(self) -> str:
        return "a2a_send"

    @property
    def description(self) -> str:
        return (
            "Send a message to a registered A2A agent and wait up to wait_seconds for it to settle."
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        return await _send(context, blocking=True, source=self.name)


class A2ASendAsyncTool(_A2ATool):
    @property
    def args_model(self) -> type[BaseModel]:
        return A2ASendAsyncArgs

    @property
    def name(self) -> str:
        return "a2a_send_async"

    @property
    def description(self) -> str:
        return "Send a message to a registered A2A agent and return its tracked task immediately."

    async def execute(self, context: ToolContext) -> ToolResult:
        return await _send(context, blocking=False, source=self.name)


class A2AStatusTool(BaseSystemTool):
    @property
    def args_model(self) -> type[BaseModel]:
        return A2AStatusArgs

    @property
    def name(self) -> str:
        return "a2a_status"

    @property
    def description(self) -> str:
        return "Refresh and return a tracked A2A task, optionally waiting for it to settle."

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot query tracked A2A tasks")
        args = _tool_args(context)
        task_id = str(args.get("task_id") or "").strip()
        if not task_id:
            return ToolResult.fail("Missing 'task_id'")
        record = await context.state_manager.get_a2a_task(task_id)
        if record is None:
            return ToolResult.fail(f"Unknown tracked A2A task: {task_id}")

        wait_seconds = _wait_seconds(args.get("wait_seconds"), default=0.0)
        deadline = asyncio.get_running_loop().time() + wait_seconds
        refresh_error: str | None = None
        while True:
            try:
                record = await refresh_tracked_a2a_task(
                    context.state_manager, task_id, tool_registry=context.tool_registry
                )
                refresh_error = None
            except Exception as exc:
                refresh_error = str(exc)
                break
            if _is_settled(record) or asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(min(0.25, max(deadline - asyncio.get_running_loop().time(), 0.0)))

        payload = build_task_response(record)
        if refresh_error:
            payload["refresh_error"] = refresh_error
        return ToolResult.ok(payload)


class A2ACancelTool(BaseSystemTool):
    @property
    def args_model(self) -> type[BaseModel]:
        return A2ACancelArgs

    @property
    def name(self) -> str:
        return "a2a_cancel"

    @property
    def description(self) -> str:
        return "Cancel a tracked A2A task and return its current task record."

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot cancel tracked A2A tasks")
        task_id = str(_tool_args(context).get("task_id") or "").strip()
        if not task_id:
            return ToolResult.fail("Missing 'task_id'")
        try:
            record = await cancel_tracked_a2a_task(
                context.state_manager, task_id, tool_registry=context.tool_registry
            )
        except (LookupError, RuntimeError) as exc:
            return ToolResult.fail(str(exc))
        return ToolResult.ok(build_task_response(record))


class A2AListTool(BaseSystemTool):
    @property
    def args_model(self) -> type[BaseModel]:
        return A2AListArgs

    @property
    def name(self) -> str:
        return "a2a_list"

    @property
    def description(self) -> str:
        return "List tracked A2A tasks, optionally filtered by agent or status."

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot list tracked A2A tasks")
        args = _tool_args(context)
        agent = str(args.get("agent") or "").strip()
        status = str(args.get("status") or "").strip().lower()
        records = await context.state_manager.get_a2a_tasks()
        filtered = [
            record
            for record in records
            if (not agent or record.agent_name == agent)
            and (not status or record.status_value == status)
        ]
        return ToolResult.ok(
            {"tasks": [build_task_response(record) for record in filtered], "count": len(filtered)}
        )


async def _send(context: ToolContext, *, blocking: bool, source: str) -> ToolResult:
    if context.state_manager is None:
        return ToolResult.fail("state_manager unavailable; cannot track A2A tasks")
    args = _tool_args(context)
    agent = str(args.get("agent") or "").strip()
    message = str(args.get("message") or "").strip()
    if not agent:
        return ToolResult.fail("Missing 'agent'")
    if not message:
        return ToolResult.fail("Missing 'message'")

    resolver = A2ASendTool()
    resolved = await resolver._resolve_agent(context, agent)
    if isinstance(resolved, ToolResult):
        return resolved
    resolved_name, cfg = resolved
    task_id = str(args.get("task_id") or "").strip() or None
    context_id = str(args.get("context_id") or "").strip() or None
    wait_seconds = _wait_seconds(args.get("wait_seconds"), default=60.0) if blocking else 60.0
    extra_context = args.get("context")
    metadata = dict(extra_context) if isinstance(extra_context, dict) else {}
    metadata.update(
        {
            "caller": "progenitor",
            "dispatch_mode": "blocking" if blocking else "async",
            "thread_id": str((context.state or {}).get("thread_id") or "").strip() or None,
        }
    )
    progress_callback = (
        _make_progress_callback(context.tool_call_id, resolved_name) if blocking else None
    )
    result, tracked_task = await dispatch_tracked_a2a_task(
        state_manager=context.state_manager,
        agent_name=resolved_name,
        agent_config=cfg,
        request_text=message,
        request_metadata=metadata,
        source=source,
        timeout=wait_seconds,
        blocking=blocking,
        notify_on_completion=not blocking,
        on_progress=progress_callback,
        task_id=task_id,
        context_id=context_id,
    )
    if tracked_task is not None:
        payload = build_task_response(tracked_task)
        if not blocking:
            payload["message"] = (
                f"Task submitted to '{resolved_name}' (task_id: {tracked_task.task_id}). "
                "You will be notified when it finishes."
            )
        return ToolResult.ok(payload)
    if not result.get("success"):
        return ToolResult.fail(str(result.get("error") or f"A2A send failed: {resolved_name}"))
    return ToolResult.fail(f"{resolved_name} did not return a trackable A2A task")


def _tool_args(context: ToolContext) -> dict[str, Any]:
    args = context.tool_args if isinstance(context.tool_args, dict) else {}
    nested_args = args.get("tool_args")
    if isinstance(nested_args, dict):
        return {**nested_args, **{key: value for key, value in args.items() if key != "tool_args"}}
    return args


async def _refresh_registry(tool_registry: Any) -> None:
    load = getattr(tool_registry, "load", None)
    if callable(load):
        with contextlib.suppress(Exception):
            result = load()
            if inspect.isawaitable(result):
                await cast(Awaitable[Any], result)


def _find_agent(tool_registry: Any, agent: str) -> dict[str, Any] | None:
    find_by_name = getattr(tool_registry, "find_by_name", None)
    if callable(find_by_name):
        with contextlib.suppress(Exception):
            result = find_by_name(agent)
            if isinstance(result, dict):
                return result
    find_by_capability = getattr(tool_registry, "find_agent_by_capability", None)
    if callable(find_by_capability):
        with contextlib.suppress(Exception):
            result = find_by_capability(agent)
            if isinstance(result, dict):
                return result
    return None


def _wait_seconds(value: Any, *, default: float) -> float:
    try:
        seconds = default if value is None else float(value)
    except (TypeError, ValueError):
        return default
    return min(max(seconds, 0.0), _MAX_WAIT_SECONDS)


def _is_settled(record: A2ATaskRecord) -> bool:
    return record.status_value in _SETTLED_STATUSES


def _make_progress_callback(
    tool_call_id: str, agent_name: str
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Stream A2A status updates under the originating tool-call ID."""

    async def _on_progress(update_payload: dict[str, Any]) -> None:
        if not tool_call_id:
            return
        status = update_payload.get("status") if isinstance(update_payload, dict) else None
        state = str(status.get("state") or "") if isinstance(status, dict) else ""
        message_text = (
            extract_message_text(status.get("message")) if isinstance(status, dict) else ""
        ) or ""
        emit_stream_event(
            build_a2a_call_progress_event(tool_call_id, agent_name, state, message_text)
        )

    return _on_progress


__all__ = [
    "A2ACancelTool",
    "A2AListTool",
    "A2ASendAsyncTool",
    "A2ASendTool",
    "A2AStatusTool",
]
