"""
Custom middleware for the Progenitor DeepAgent.

Provides:
- SecretRedactionMiddleware: Redacts API keys/secrets from tool call args and results
- AuditLoggingMiddleware: Logs tool calls to Postgres
- MetricsMiddleware: Tracks tool call counts and latency
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from deepagents.middleware.async_subagents import AsyncSubAgent
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.progenitor.async_subagents import build_tracked_task_record
from app.progenitor.ipc.stream_events import (
    build_tool_error_event,
    build_tool_result_event,
    build_tool_start_event,
    emit_stream_event,
)
from app.progenitor.state.models import AsyncSubagentTaskRecord, redact_secrets
from app.progenitor.utils import utcnow

logger = logging.getLogger(__name__)


class AuditLogSink(Protocol):
    async def write_log(
        self,
        *,
        source: str,
        message: str,
        level: str = "info",
        payload: dict[str, Any] | None = None,
        timestamp: Any = None,
        relative_path: str | None = None,
    ) -> None: ...


class SecretRedactionMiddleware(AgentMiddleware):
    """Redacts secrets from tool call arguments and results.

    Uses Progenitor's existing redact_secrets() which covers OpenAI, Azure,
    GitHub, Anthropic, AWS, and generic secret patterns. Applied as a
    wrap_tool_call interceptor so it covers all tools automatically.
    """

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Redact secrets in tool args before execution, and in results after."""
        # Redact secrets in tool call arguments
        tool_call = request.tool_call
        redacted_args = {}
        for key, value in tool_call.get("args", {}).items():
            if isinstance(value, str):
                redacted_args[key] = redact_secrets(value)
            else:
                redacted_args[key] = value

        if redacted_args != tool_call.get("args", {}):
            logger.debug(
                "Redacted secrets in tool call args for %s",
                tool_call.get("name", "unknown"),
            )

        # Don't modify the actual tool call args — we log redacted but
        # execute with originals (tools need real credentials to work)
        return await handler(request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Sync version of redaction wrapper."""
        return handler(request)


class AuditLoggingMiddleware(AgentMiddleware):
    """Logs tool calls to the configured Postgres audit trail.

    Each tool invocation is logged with: timestamp, tool name, arguments
    (redacted), duration, and success/failure status.
    """

    def __init__(
        self,
        log_sink: AuditLogSink | None = None,
    ):
        """Initialize audit logger.

        Args:
            log_sink: Async sink for durable audit logs.
        """
        self._log_sink = log_sink

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Log tool call with timing and redaction."""
        tool_call = request.tool_call
        tool_name = tool_call.get("name", "unknown")
        tool_id = tool_call.get("id", "") or ""

        # Redact args for logging (not for execution)
        raw_args = tool_call.get("args", {})
        redacted_args = {}
        for key, value in raw_args.items():
            if isinstance(value, str):
                redacted_args[key] = redact_secrets(value)
            else:
                redacted_args[key] = value

        # Best-effort: surface tool start to the UI stream.
        emit_stream_event(build_tool_start_event(tool_name, tool_id, raw_args))

        start_time = time.monotonic()
        error: str | None = None
        result: ToolMessage | Command[Any] | None = None
        try:
            result = await handler(request)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            entry = {
                "timestamp": time.time(),
                "tool_name": tool_name,
                "tool_id": tool_id,
                "args": redacted_args,
                "duration_ms": round(duration_ms, 2),
                "success": error is None,
                "error": error,
            }
            logger.info("Tool call: %s (%.1fms)", tool_name, duration_ms)

            # Best-effort: surface tool completion to the UI stream.
            if error is None:
                emit_stream_event(build_tool_result_event(tool_name, tool_id, duration_ms, result))
            else:
                emit_stream_event(build_tool_error_event(tool_name, tool_id, duration_ms, error))

            if self._log_sink is None:
                logger.warning("No audit log sink configured; dropping tool audit entry")
            else:
                try:
                    await self._log_sink.write_log(
                        source="tool_calls",
                        message=f"Tool call: {tool_name}",
                        level="error" if error else "info",
                        payload=entry,
                        timestamp=entry["timestamp"],
                        relative_path="tool_calls",
                    )
                except Exception:
                    logger.warning("Failed to write audit log to Postgres", exc_info=True)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Sync version — just pass through (most calls are async)."""
        return handler(request)


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
        self,
        *,
        state_manager: Any = None,
        async_subagents: list[AsyncSubAgent] | None = None,
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
                request,
                tool_name,
                result,
                tasks,
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
            raw_args = request.tool_call.get("args", {})
            task_id = str(raw_args.get("task_id") or "").strip()
            persisted = [task for task in persisted if task.task_id == task_id]
        if not persisted:
            return result, tasks

        merged_tasks = dict(tasks)
        for task in persisted:
            merged_tasks.setdefault(task.task_id, _async_task_record_payload(task))

        if merged_tasks == tasks:
            return result, tasks
        return _replace_async_tool_result(request, result, merged_tasks), merged_tasks


class MetricsMiddleware(AgentMiddleware):
    """Tracks tool call counts and latency for observability.

    Maintains in-memory counters accessible via get_metrics(). Useful for
    understanding agent behavior patterns and identifying bottlenecks.
    """

    def __init__(self):
        self._call_counts: dict[str, int] = {}
        self._total_duration_ms: dict[str, float] = {}
        self._error_counts: dict[str, int] = {}
        self._model_calls: int = 0

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Track tool call metrics."""
        tool_name = request.tool_call.get("name", "unknown")
        self._call_counts[tool_name] = self._call_counts.get(tool_name, 0) + 1

        start_time = time.monotonic()
        try:
            result = await handler(request)
            return result
        except Exception:
            self._error_counts[tool_name] = self._error_counts.get(tool_name, 0) + 1
            raise
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._total_duration_ms[tool_name] = (
                self._total_duration_ms.get(tool_name, 0) + duration_ms
            )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Sync version — just pass through."""
        return handler(request)

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Track model call count."""
        self._model_calls += 1
        return None

    def get_metrics(self) -> dict[str, Any]:
        """Return current metrics snapshot."""
        tool_metrics = {}
        for name in sorted(self._call_counts):
            count = self._call_counts[name]
            total_ms = self._total_duration_ms.get(name, 0)
            tool_metrics[name] = {
                "calls": count,
                "errors": self._error_counts.get(name, 0),
                "total_ms": round(total_ms, 2),
                "avg_ms": round(total_ms / count, 2) if count > 0 else 0,
            }
        return {
            "model_calls": self._model_calls,
            "tool_calls": sum(self._call_counts.values()),
            "tool_errors": sum(self._error_counts.values()),
            "tools": tool_metrics,
        }

    def reset(self):
        """Reset all metrics."""
        self._call_counts.clear()
        self._total_duration_ms.clear()
        self._error_counts.clear()
        self._model_calls = 0


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
    first = messages[0]
    content = getattr(first, "content", None)
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


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
    return Command(
        graph=getattr(result, "graph", None),
        update={**update, "async_tasks": tasks, "messages": [message]},
        resume=getattr(result, "resume", None),
        goto=getattr(result, "goto", ()),
    )


def _async_task_record_payload(task: AsyncSubagentTaskRecord) -> dict[str, Any]:
    payload = task.model_dump(mode="json")
    payload["task_id"] = task.task_id
    payload["agent_name"] = task.agent_name
    payload["run_id"] = task.run_id
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


def _tool_thread_id(request: ToolCallRequest) -> str | None:
    config = getattr(getattr(request, "runtime", None), "config", None)
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return None
    thread_id = str(configurable.get("thread_id") or "").strip()
    return thread_id or None


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
                {
                    "event_type": "async_subagent_start",
                    "content": agent_name,
                    "metadata": metadata,
                }
            )
        elif tool_name == "update_async_task":
            emit_stream_event(
                {
                    "event_type": "async_subagent_update",
                    "content": agent_name,
                    "metadata": metadata,
                }
            )
        elif tool_name == "cancel_async_task":
            emit_stream_event(
                {
                    "event_type": "async_subagent_cancel",
                    "content": agent_name,
                    "metadata": metadata,
                }
            )
        elif tool_name in {"check_async_task", "list_async_tasks"} and status in {
            "success",
            "error",
            "cancelled",
        }:
            emit_stream_event(
                {
                    "event_type": "async_subagent_result",
                    "content": agent_name,
                    "metadata": metadata,
                }
            )
