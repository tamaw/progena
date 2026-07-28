from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from deepagents.middleware.async_subagents import AsyncSubAgent
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.progenitor.async_subagents import build_tracked_task_record
from app.progenitor.sse.stream_events import emit_stream_event
from app.progenitor.state.models import AsyncSubagentTaskRecord
from app.progenitor.utils import utcnow


class AsyncSubagentTrackingMiddleware(AgentMiddleware):
    """Persists and emits lifecycle events for DeepAgents async-subagent tools."""

    _ASYNC_TOOL_NAMES = {
        "start_async_task",
        "check_async_task",
        "update_async_task",
        "cancel_async_task",
        "list_async_tasks",
    }

    def __init__(
        self, *, state_manager: Any = None, async_subagents: list[AsyncSubAgent] | None = None
    ):
        self._state_manager = state_manager
        self._async_subagents = {
            str(spec["name"]): spec for spec in (async_subagents or []) if "name" in spec
        }

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_name = str(request.tool_call.get("name") or "")
        if tool_name not in self._ASYNC_TOOL_NAMES:
            return await handler(request)

        result = await handler(request)
        tasks = _extract_async_tasks(result)
        if tool_name in {"list_async_tasks", "check_async_task"}:
            result, tasks = await self._merge_persisted_async_tasks(
                request, tool_name, result, tasks
            )
        if tasks:
            await self._persist_async_tasks(request, tool_name, tasks, result)
            _emit_async_subagent_events(request, tool_name, tasks)
        return result

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return handler(request)

    async def _persist_async_tasks(
        self,
        request: ToolCallRequest,
        tool_name: str,
        tasks: dict[str, dict[str, Any]],
        result: ToolMessage | Command[Any],
    ) -> None:
        if self._state_manager is None:
            return
        raw_args = request.tool_call.get("args", {})
        parent_thread_id = _tool_thread_id(request)
        request_text = str(raw_args.get("description") or raw_args.get("message") or "")
        if tool_name == "start_async_task":
            subagent_name = str(raw_args.get("subagent_type") or "")
            spec = self._async_subagents.get(subagent_name)
            if spec is None:
                return
            for task in tasks.values():
                record = build_tracked_task_record(
                    task,
                    spec=spec,
                    parent_thread_id=parent_thread_id,
                    request_text=request_text,
                    request_metadata={"tool_args": dict(raw_args)},
                )
                await self._state_manager.upsert_async_subagent_task(record)
            return

        result_payload = _extract_check_result_payload(result)
        for task_id, task in tasks.items():
            existing = await self._state_manager.get_async_subagent_task(task_id)
            if existing is None:
                continue
            updates: dict[str, Any] = {
                "run_id": str(task.get("run_id") or existing.run_id),
                "status": str(task.get("status") or existing.status),
                "updated_at": utcnow(),
            }
            if tool_name == "update_async_task":
                updates["request_metadata"] = {
                    **dict(existing.request_metadata),
                    "last_update_message": request_text,
                }
            if tool_name == "cancel_async_task":
                updates["completed_at"] = existing.completed_at or utcnow()
            if result_payload is not None:
                if "result" in result_payload:
                    updates["summary"] = str(result_payload.get("result") or "")
                    updates["latest_message"] = str(result_payload.get("result") or "")
                    updates["completed_at"] = existing.completed_at or utcnow()
                if "error" in result_payload:
                    updates["last_error"] = str(result_payload.get("error") or "")
                    updates["completed_at"] = existing.completed_at or utcnow()
            await self._state_manager.update_async_subagent_task(task_id, updates)

    async def _merge_persisted_async_tasks(
        self,
        request: ToolCallRequest,
        tool_name: str,
        result: ToolMessage | Command[Any],
        tasks: dict[str, dict[str, Any]],
    ) -> tuple[ToolMessage | Command[Any], dict[str, dict[str, Any]]]:
        if self._state_manager is None:
            return result, tasks
        persisted = await self._state_manager.get_async_subagent_tasks()
        if tool_name == "check_async_task":
            task_id = str(request.tool_call.get("args", {}).get("task_id") or "").strip()
            persisted = [task for task in persisted if task.task_id == task_id]
        if not persisted:
            return result, tasks
        merged_tasks = dict(tasks)
        for task in persisted:
            merged_tasks.setdefault(task.task_id, _async_task_record_payload(task))
        if merged_tasks == tasks:
            return result, tasks
        return _replace_async_tool_result(request, result, merged_tasks), merged_tasks


def _extract_async_tasks(result: ToolMessage | Command[Any]) -> dict[str, dict[str, Any]]:
    update = getattr(result, "update", None)
    if not isinstance(update, dict):
        return {}
    async_tasks = update.get("async_tasks")
    if not isinstance(async_tasks, dict):
        return {}
    return {
        str(task_id): dict(task) for task_id, task in async_tasks.items() if isinstance(task, dict)
    }


def _extract_check_result_payload(result: ToolMessage | Command[Any]) -> dict[str, Any] | None:
    update = getattr(result, "update", None)
    if not isinstance(update, dict):
        return None
    messages = update.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    content = getattr(messages[0], "content", None)
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _tool_thread_id(request: ToolCallRequest) -> str | None:
    config = getattr(getattr(request, "runtime", None), "config", None)
    if isinstance(config, dict):
        configurable = config.get("configurable")
        if isinstance(configurable, dict):
            thread_id = str(configurable.get("thread_id") or "").strip()
            if thread_id:
                return thread_id
    state = request.state if isinstance(request.state, dict) else {}
    return str(state.get("thread_id") or "").strip() or None


def _async_task_record_payload(task: Any) -> dict[str, Any]:
    payload = task.model_dump(mode="json") if isinstance(task, AsyncSubagentTaskRecord) else {}
    payload["task_id"] = str(task.task_id)
    payload["agent_name"] = str(task.agent_name)
    payload["run_id"] = str(task.run_id)
    payload["status"] = str(task.status)
    return payload


def _async_tasks_result_payload(tasks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = list(tasks.values())
    counts = {
        "total": len(rows),
        "running": sum(1 for task in rows if str(task.get("status")) == "running"),
        "success": sum(1 for task in rows if str(task.get("status")) == "success"),
        "error": sum(1 for task in rows if str(task.get("status")) == "error"),
        "cancelled": sum(1 for task in rows if str(task.get("status")) == "cancelled"),
    }
    return {
        "tasks": rows,
        "total": counts["total"],
        "running": counts["running"],
        "success": counts["success"],
        "error": counts["error"],
        "cancelled": counts["cancelled"],
    }


def _replace_async_tool_result(
    request: ToolCallRequest,
    result: ToolMessage | Command[Any],
    tasks: dict[str, dict[str, Any]],
) -> ToolMessage | Command[Any]:
    content = json.dumps(_async_tasks_result_payload(tasks), sort_keys=True)
    tool_call_id = str(request.tool_call.get("id") or "")
    message = ToolMessage(content=content, tool_call_id=tool_call_id)
    if not isinstance(result, Command):
        return message
    update = getattr(result, "update", None)
    if not isinstance(update, dict):
        update = {}
    replacement = ToolMessage(
        content=content,
        tool_call_id=tool_call_id,
    )
    return Command(
        graph=getattr(result, "graph", None),
        update={**update, "messages": [replacement], "async_tasks": tasks},
        resume=getattr(result, "resume", None),
        goto=getattr(result, "goto", ()),
    )


def _emit_async_subagent_events(
    request: ToolCallRequest,
    tool_name: str,
    tasks: dict[str, dict[str, Any]],
) -> None:
    parent_thread_id = _tool_thread_id(request)
    raw_args = request.tool_call.get("args", {})
    for task in tasks.values():
        agent_name = str(task.get("agent_name") or raw_args.get("subagent_type") or "")
        task_id = str(task.get("task_id") or "")
        status = str(task.get("status") or "")
        metadata = {
            "subagent_name": agent_name,
            "task_id": task_id,
            "status": status,
        }
        if parent_thread_id:
            metadata["thread_id"] = parent_thread_id
        if tool_name == "start_async_task":
            emit_stream_event(
                {"event_type": "async_subagent_start", "content": agent_name, "metadata": metadata}
            )
        elif tool_name == "update_async_task":
            emit_stream_event(
                {"event_type": "async_subagent_update", "content": agent_name, "metadata": metadata}
            )
        elif tool_name == "cancel_async_task":
            emit_stream_event(
                {"event_type": "async_subagent_cancel", "content": agent_name, "metadata": metadata}
            )
        elif tool_name in {"check_async_task", "list_async_tasks"} and status in {
            "success",
            "error",
            "cancelled",
        }:
            emit_stream_event(
                {"event_type": "async_subagent_result", "content": agent_name, "metadata": metadata}
            )
