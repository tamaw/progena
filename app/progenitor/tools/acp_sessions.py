"""System tools for starting and controlling durable ACP agent sessions."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import uuid
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from ..acp import acp_config_from_agent_config, task_command, task_cwd
from ..state.models import ACPTaskRecord, ACPTaskStatus
from ..utils import utcnow
from .protocol import BaseSystemTool, ToolContext, ToolResult

_SEND_WAIT_SECONDS = 20.0
_STATUS_POLL_SECONDS = 2.0
_OUTPUT_LIMIT = 4000


class ACPStartArgs(BaseModel):
    """Arguments for starting a durable ACP session."""

    agent: str = Field(description="Exact name of a registered ACP agent.")
    prompt: str = Field(description="Initial task prompt for the ACP agent.")
    cwd: str | None = Field(
        default=None,
        description="Optional working directory on the ACP agent host.",
    )
    card_id: str | None = Field(
        default=None,
        description="Optional work-card identifier associated with this session.",
    )
    mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] | None = Field(
        default=None,
        description="Optional ACP session mode to apply before the turn starts.",
    )


class ACPSendArgs(BaseModel):
    """Arguments for sending a follow-up message to an ACP session."""

    task_id: str = Field(description="ACP task ID returned by acp_start.")
    message: str = Field(description="Follow-up instruction to inject or run as the next turn.")


class ACPStatusArgs(BaseModel):
    """Arguments for inspecting an ACP session."""

    task_id: str = Field(description="ACP task ID returned by acp_start.")
    wait_seconds: float = Field(
        default=0,
        ge=0,
        le=300,
        description="Optionally wait up to this many seconds for status, input, stall, or turn changes.",
    )


class ACPAnswerArgs(BaseModel):
    """Arguments for answering an ACP question, plan, or permission prompt."""

    task_id: str = Field(description="ACP task ID containing the pending input.")
    input_id: str = Field(description="Exact pending_input ID reported by acp_status.")
    answer: Any = Field(description="Answer matching the pending input's documented shape.")


class ACPCancelArgs(BaseModel):
    """Arguments for cancelling an ACP session."""

    task_id: str = Field(description="ACP task ID to cancel.")


class ACPListArgs(BaseModel):
    """Arguments for listing durable ACP sessions."""

    card_id: str | None = Field(default=None, description="Only sessions for this work-card ID.")
    status: str | None = Field(
        default=None, description="Only sessions with this lifecycle status."
    )
    agent: str | None = Field(default=None, description="Only sessions for this ACP agent name.")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum session rows to return.")


async def _load_registry(registry: Any) -> None:
    load = getattr(registry, "load", None)
    if callable(load):
        with contextlib.suppress(Exception):
            result = load()
            if inspect.isawaitable(result):
                await result


def _find_agent(registry: Any, agent: str) -> dict[str, Any] | None:
    config: Any = None
    find_by_name = getattr(registry, "find_by_name", None)
    if callable(find_by_name):
        with contextlib.suppress(Exception):
            config = find_by_name(agent)
    if not isinstance(config, dict):
        find_by_capability = getattr(registry, "find_agent_by_capability", None)
        if callable(find_by_capability):
            with contextlib.suppress(Exception):
                config = find_by_capability(agent)
    return config if isinstance(config, dict) else None


def _trim(value: Any, limit: int = _OUTPUT_LIMIT) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _json_value(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    return dump(mode="json") if callable(dump) else value


def _status_signature(record: ACPTaskRecord) -> tuple[Any, ...]:
    return (
        record.status_value,
        _json_value(getattr(record, "pending_input", None)),
        record.stalled,
        record.turn,
    )


def _snapshot(record: ACPTaskRecord) -> dict[str, Any]:
    progress = record.progress if isinstance(record.progress, dict) else {}
    return {
        "task_id": record.task_id,
        "status": record.status_value,
        "turn": record.turn,
        "stalled": record.stalled,
        "mode": getattr(record, "mode", None),
        "progress": {
            "last_message": _trim(progress.get("last_message")),
            "plan": progress.get("plan"),
            "last_tool": progress.get("last_tool"),
            "tool_call_count": progress.get("tool_call_count", 0),
        },
        "output": _trim(record.output),
        "last_error": record.last_error,
        "pending_input": _json_value(getattr(record, "pending_input", None)),
    }


def _message_status(record: ACPTaskRecord, message_id: str) -> tuple[str, str | None] | None:
    messages = getattr(record, "messages", [])
    for message in messages if isinstance(messages, list) else []:
        value = _json_value(message)
        if isinstance(value, dict) and value.get("id") == message_id:
            return str(value.get("status") or "pending"), _trim(value.get("error"))
    return None


class ACPStartTool(BaseSystemTool):
    """Queue a new ACP session for the backend ACP supervisor."""

    @property
    def args_model(self) -> type[BaseModel]:
        return ACPStartArgs

    @property
    def name(self) -> str:
        return "acp_start"

    @property
    def description(self) -> str:
        return (
            "Start a durable session with a registered ACP CLI agent such as Claude Code. "
            "Returns immediately with an ACP task ID; use acp_status to observe it, acp_send "
            "for follow-up instructions, and acp_answer when the agent asks a question."
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot create ACP session")
        if context.tool_registry is None:
            return ToolResult.fail("tool_registry unavailable")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        agent = str(args.get("agent") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        if not agent:
            return ToolResult.fail("Missing 'agent'")
        if not prompt:
            return ToolResult.fail("Missing 'prompt'")

        await _load_registry(context.tool_registry)
        config = _find_agent(context.tool_registry, agent)
        if config is None:
            return ToolResult.fail(f"Unknown ACP agent: {agent}")
        if config.get("enabled", True) is False:
            return ToolResult.fail(f"Agent '{agent}' is disabled")
        if str(config.get("transport") or "").lower() != "acp":
            return ToolResult.fail(f"Agent '{agent}' is not an ACP agent")
        try:
            acp_config = acp_config_from_agent_config(config)
        except ValueError as exc:
            return ToolResult.fail(str(exc))

        raw_mode = args.get("mode")
        mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] | None = None
        if raw_mode is not None:
            candidate = str(raw_mode).strip()
            if candidate not in {"default", "acceptEdits", "plan", "bypassPermissions"}:
                return ToolResult.fail(f"Invalid ACP mode: {candidate}")
            mode = cast(Literal["default", "acceptEdits", "plan", "bypassPermissions"], candidate)
        task_id = f"acp_{uuid.uuid4().hex[:12]}"
        state = context.state if isinstance(context.state, dict) else {}
        source = str(state.get("agent_name") or state.get("caller_agent") or "progenitor")
        record = ACPTaskRecord(
            task_id=task_id,
            agent_name=str(config.get("name") or agent).strip() or agent,
            prompt=prompt,
            cwd=task_cwd(acp_config, args.get("cwd")),
            card_id=str(args.get("card_id") or "").strip() or None,
            command=task_command(acp_config),
            source=source,
            request_metadata={
                "caller": source,
                "thread_id": str(state.get("thread_id") or "").strip() or None,
            },
            status=ACPTaskStatus.QUEUED,
            notify_on_completion=False,
            created_at=utcnow(),
            updated_at=utcnow(),
            mode=mode,
        )
        await context.state_manager.upsert_acp_task(record)
        return ToolResult.ok({"task_id": task_id, "status": ACPTaskStatus.QUEUED.value})


class ACPSendTool(BaseSystemTool):
    """Deliver a follow-up instruction over a live ACP supervisor connection."""

    @property
    def args_model(self) -> type[BaseModel]:
        return ACPSendArgs

    @property
    def name(self) -> str:
        return "acp_send"

    @property
    def description(self) -> str:
        return (
            "Send a follow-up message to an existing ACP session. During a running turn it is "
            "steered into that turn; when idle it starts the next turn. Waits briefly to report "
            "whether the supervisor injected or started it."
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot send ACP message")
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        task_id = str(args.get("task_id") or "").strip()
        text = str(args.get("message") or "").strip()
        if not task_id:
            return ToolResult.fail("Missing 'task_id'")
        if not text:
            return ToolResult.fail("Missing 'message'")

        message_id = f"acp_msg_{uuid.uuid4().hex[:12]}"
        appended = await context.state_manager.append_acp_task_message(
            task_id,
            {"id": message_id, "text": text, "status": "pending"},
        )
        if not appended:
            return ToolResult.fail(f"Unknown ACP task: {task_id}")

        deadline = asyncio.get_running_loop().time() + _SEND_WAIT_SECONDS
        while True:
            record = await context.state_manager.get_acp_task(task_id)
            if record is None:
                return ToolResult.fail(f"ACP task disappeared: {task_id}")
            message = _message_status(record, message_id)
            if message is not None and message[0] != "pending":
                outcome, error = message
                return ToolResult.ok({"outcome": outcome, **({"error": error} if error else {})})
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return ToolResult.ok({"outcome": "pending"})
            await asyncio.sleep(min(0.25, remaining))


class ACPStatusTool(BaseSystemTool):
    """Inspect the compact live state of an ACP session."""

    @property
    def args_model(self) -> type[BaseModel]:
        return ACPStatusArgs

    @property
    def name(self) -> str:
        return "acp_status"

    @property
    def description(self) -> str:
        return (
            "Get compact ACP session progress, output, errors, and any pending question, plan, "
            "or permission input. Optionally wait for a material session-state change."
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot inspect ACP session")
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        task_id = str(args.get("task_id") or "").strip()
        if not task_id:
            return ToolResult.fail("Missing 'task_id'")
        try:
            wait_seconds = min(max(float(args.get("wait_seconds") or 0), 0), 300)
        except (TypeError, ValueError):
            return ToolResult.fail("'wait_seconds' must be a number between 0 and 300")

        record = await context.state_manager.get_acp_task(task_id)
        if record is None:
            return ToolResult.fail(f"Unknown ACP task: {task_id}")
        initial = _status_signature(record)
        if wait_seconds <= 0:
            return ToolResult.ok(_snapshot(record))

        deadline = asyncio.get_running_loop().time() + wait_seconds
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return ToolResult.ok(_snapshot(record))
            await asyncio.sleep(min(_STATUS_POLL_SECONDS, remaining))
            updated = await context.state_manager.get_acp_task(task_id)
            if updated is None:
                return ToolResult.fail(f"ACP task disappeared: {task_id}")
            record = updated
            if _status_signature(record) != initial:
                return ToolResult.ok(_snapshot(record))


class ACPAnswerTool(BaseSystemTool):
    """Deliver a response to ACP input held open by the supervisor."""

    @property
    def args_model(self) -> type[BaseModel]:
        return ACPAnswerArgs

    @property
    def name(self) -> str:
        return "acp_answer"

    @property
    def description(self) -> str:
        return (
            "Answer the exact pending ACP question, plan review, or permission request returned "
            "by acp_status. The answer is delivered on the supervisor's live connection."
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot answer ACP input")
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        task_id = str(args.get("task_id") or "").strip()
        input_id = str(args.get("input_id") or "").strip()
        if not task_id:
            return ToolResult.fail("Missing 'task_id'")
        if not input_id:
            return ToolResult.fail("Missing 'input_id'")

        record = await context.state_manager.get_acp_task(task_id)
        if record is None:
            return ToolResult.fail(f"Unknown ACP task: {task_id}")
        pending = _json_value(getattr(record, "pending_input", None))
        pending_id = pending.get("id") if isinstance(pending, dict) else None
        if pending_id != input_id:
            return ToolResult.fail(f"No matching pending input '{input_id}' for ACP task {task_id}")
        delivered = await context.state_manager.set_acp_task_answer(
            task_id, input_id, args.get("answer")
        )
        if not delivered:
            return ToolResult.fail(f"ACP task disappeared: {task_id}")
        return ToolResult.ok({"delivered": True})


class ACPCancelTool(BaseSystemTool):
    """Request cancellation of an ACP session."""

    @property
    def args_model(self) -> type[BaseModel]:
        return ACPCancelArgs

    @property
    def name(self) -> str:
        return "acp_cancel"

    @property
    def description(self) -> str:
        return "Request that the backend supervisor cancel an ACP session's current turn."

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot cancel ACP session")
        task_id = str((context.tool_args or {}).get("task_id") or "").strip()
        if not task_id:
            return ToolResult.fail("Missing 'task_id'")
        cancelled = await context.state_manager.update_acp_task(task_id, {"cancel_requested": True})
        if not cancelled:
            return ToolResult.fail(f"Unknown ACP task: {task_id}")
        return ToolResult.ok({"task_id": task_id, "cancel_requested": True})


class ACPListTool(BaseSystemTool):
    """List compact summaries of durable ACP sessions."""

    @property
    def args_model(self) -> type[BaseModel]:
        return ACPListArgs

    @property
    def name(self) -> str:
        return "acp_list"

    @property
    def description(self) -> str:
        return "List recent ACP sessions, optionally filtered by card, status, or ACP agent name."

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot list ACP sessions")
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        card_id = str(args.get("card_id") or "").strip() or None
        status = str(args.get("status") or "").strip().lower() or None
        agent = str(args.get("agent") or "").strip() or None
        try:
            limit = min(max(int(args.get("limit") or 20), 1), 100)
        except (TypeError, ValueError):
            return ToolResult.fail("'limit' must be an integer between 1 and 100")

        records = await context.state_manager.get_acp_tasks()
        filtered = [
            record
            for record in records
            if (card_id is None or record.card_id == card_id)
            and (status is None or record.status_value == status)
            and (agent is None or record.agent_name == agent)
        ]
        filtered.sort(key=lambda record: record.updated_at, reverse=True)
        return ToolResult.ok(
            {
                "tasks": [
                    {
                        "task_id": record.task_id,
                        "agent": record.agent_name,
                        "status": record.status_value,
                        "turn": record.turn,
                        "card_id": record.card_id,
                        "cwd": record.cwd,
                        "updated_at": _timestamp(record.updated_at),
                        "summary": _trim(record.summary or record.output or record.last_error),
                    }
                    for record in filtered[:limit]
                ]
            }
        )


def _timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


__all__ = [
    "ACPAnswerTool",
    "ACPCancelTool",
    "ACPListTool",
    "ACPSendTool",
    "ACPStartTool",
    "ACPStatusTool",
]
