"""Tracked ACP run dispatch, sandboxing, and completion helpers.

ACP peer of ``app/progenitor/a2a/tasks.py``. An ACP run is a local stdio agent
subprocess that Progenitor drives to completion. Unlike A2A (a remote task we
poll), the run finishes in-process, so there is no poll-refresh: a background
task updates the persisted :class:`ACPTaskRecord` when the run ends, and the idle
heartbeat picks up terminal-but-unnotified records to apply/notify.

Runs are confined to a fresh per-run **sandbox directory** passed to the agent as
its working directory and allowed dir — the guardrail for ``--yolo``-style agents.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any, cast

from ..state.models import ACPAgentConfig, ACPTaskRecord, ACPTaskStatus
from ..utils import utcnow
from .agents import build_agent_args
from .runner import ACPRunner, ACPRunResult

logger = logging.getLogger(__name__)

# Keep strong references to in-flight background runs so they are not GC'd.
_background_runs: set[asyncio.Task[None]] = set()


def resolve_acp_workspace_root() -> Path:
    """Base directory under which each ACP run gets a fresh sandbox subdir."""
    explicit = os.getenv("PROGENITOR_ACP_WORKSPACE")
    if explicit:
        return Path(explicit)
    data_dir = os.getenv("PROGENITOR_DATA_DIR") or "data"
    return Path(data_dir) / "acp_workspaces"


def provision_sandbox(task_id: str) -> Path:
    """Create and return a fresh per-run sandbox directory."""
    sandbox = resolve_acp_workspace_root() / task_id
    sandbox.mkdir(parents=True, exist_ok=True)
    return sandbox


def _acp_config_from_dict(agent_config: dict[str, Any]) -> ACPAgentConfig:
    raw = agent_config.get("acp")
    if isinstance(raw, ACPAgentConfig):
        return raw
    if isinstance(raw, dict):
        return ACPAgentConfig(**raw)
    # Fail fast rather than fall back to ACPAgentConfig() defaults, which would
    # silently spawn Copilot for an entry that never declared an ACP config.
    name = agent_config.get("name") or "<unknown>"
    raise ValueError(f"ACP agent {name!r} is missing its 'acp' configuration")


def _runner_for(acp_config: ACPAgentConfig, cwd: str) -> ACPRunner:
    if acp_config.port is not None:
        # TCP: connect to an already-running ACP server; model is set per session.
        return ACPRunner(
            host=acp_config.host,
            port=acp_config.port,
            model=acp_config.model,
            timeout=acp_config.timeout_seconds,
        )
    args = build_agent_args(
        acp_config.profile,
        model=acp_config.model,
        allowed_dirs=[cwd],
        flags=acp_config.flags,
        raw_args=acp_config.args or None,
    )
    return ACPRunner(acp_config.command, args, timeout=acp_config.timeout_seconds)


def _finalize_updates(result: ACPRunResult) -> dict[str, Any]:
    """Map a finished run result onto ACPTaskRecord update fields."""
    now = utcnow()
    status = ACPTaskStatus.COMPLETED if result.success else ACPTaskStatus.FAILED
    summary = result.output.strip().splitlines()[0][:200] if result.output.strip() else None
    return {
        "status": status.value,
        "output": result.output,
        "tool_calls": result.tool_calls,
        "stop_reason": result.stop_reason,
        "session_id": result.session_id,
        "summary": summary,
        "last_error": result.error,
        "updated_at": now,
        "completed_at": now,
    }


async def _run_and_finalize(
    *,
    state_manager: Any,
    task_id: str,
    runner: ACPRunner,
    request_text: str,
    cwd: str,
) -> None:
    """Background entrypoint: run the agent, then persist the terminal record."""
    result = await runner.run(request_text, cwd=cwd, run_id=task_id)
    try:
        await state_manager.update_acp_task(task_id, _finalize_updates(result))
    except Exception:  # pragma: no cover - defensive
        logger.exception("ACP [%s]: failed to persist completion", task_id)


async def dispatch_tracked_acp_task(
    *,
    state_manager: Any,
    agent_name: str,
    agent_config: dict[str, Any],
    request_text: str,
    request_metadata: dict[str, Any] | None = None,
    source: str = "call_agent",
    blocking: bool = False,
    notify_on_completion: bool = True,
) -> tuple[dict[str, Any], ACPTaskRecord]:
    """Start an ACP run, tracking it as an ``ACPTaskRecord``.

    Returns ``(response, record)``. When ``blocking`` the run is awaited and the
    record is terminal on return; otherwise a background task drives it and the
    record is returned in the ``working`` state for the heartbeat to complete.
    """
    acp_config = _acp_config_from_dict(agent_config)
    task_id = f"acp_{uuid.uuid4().hex[:12]}"
    if acp_config.port is not None:
        # TCP server: the working directory is on the (possibly remote) server, so
        # no local sandbox is provisioned; the configured cwd is passed through.
        cwd = acp_config.cwd or "/tmp"  # nosec B108 - remote server path, not a local temp file
        command_label = f"{acp_config.host or '127.0.0.1'}:{acp_config.port}"
    else:
        cwd = str(provision_sandbox(task_id))
        command_label = acp_config.command
    runner = _runner_for(acp_config, cwd)
    now = utcnow()
    record = ACPTaskRecord(
        task_id=task_id,
        agent_name=agent_name,
        command=command_label,
        cwd=cwd,
        source=source,
        request_text=request_text,
        request_metadata=dict(request_metadata or {}),
        status=ACPTaskStatus.WORKING,
        notify_on_completion=notify_on_completion,
        created_at=now,
        updated_at=now,
    )
    await state_manager.upsert_acp_task(record)

    if blocking:
        result = await runner.run(request_text, cwd=cwd, run_id=task_id)
        await state_manager.update_acp_task(task_id, _finalize_updates(result))
        record = cast(ACPTaskRecord, await state_manager.get_acp_task(task_id)) or record
        response = build_acp_task_response(record)
        response["success"] = result.success
        if not result.success and result.error:
            response["error"] = result.error
        return response, record

    task = asyncio.create_task(
        _run_and_finalize(
            state_manager=state_manager,
            task_id=task_id,
            runner=runner,
            request_text=request_text,
            cwd=cwd,
        )
    )
    _background_runs.add(task)
    task.add_done_callback(_background_runs.discard)

    response = build_acp_task_response(record)
    response["success"] = True
    return response, record


def build_acp_task_response(record: ACPTaskRecord) -> dict[str, Any]:
    """Compact tool-result payload describing a tracked ACP run."""
    payload: dict[str, Any] = {
        "task_id": record.task_id,
        "agent": record.agent_name,
        "status": record.status,
        "source": record.source,
        "transport": "acp",
        "cwd": record.cwd,
    }
    if record.is_terminal and record.output is not None:
        payload["output"] = record.output
    return payload


def build_acp_completion_message(record: ACPTaskRecord) -> str:
    """Human-readable completion summary for conversation history / notifications."""
    name = record.agent_name.replace("_", " ").strip() or "ACP agent"
    if record.status == ACPTaskStatus.COMPLETED.value:
        body = (record.output or "").strip()
        snippet = body[:1000] + ("…" if len(body) > 1000 else "") if body else "(no output)"
        return f"ACP agent '{name}' finished.\n\n{snippet}"
    reason = record.last_error or record.stop_reason or "unknown error"
    return f"ACP agent '{name}' run {record.status}: {reason}"


def _notification_title(record: ACPTaskRecord) -> str:
    name = record.agent_name.replace("_", " ").strip() or "ACP agent"
    return f"{name} run {record.status}"


def _completion_followup_thread_id(record: ACPTaskRecord) -> str | None:
    return str(record.request_metadata.get("thread_id") or "").strip() or None


def _build_completion_followup_prompt(record: ACPTaskRecord) -> str:
    raw_result = build_acp_completion_message(record).strip()
    original_request = record.request_text.strip() or "(missing original request)"
    return (
        "An async ACP agent run that the user started earlier in this conversation has finished.\n\n"
        f"Agent: {record.agent_name}\n"
        f"Status: {record.status}\n"
        f"Original request: {original_request}\n\n"
        "Raw task result:\n"
        f"{raw_result}\n\n"
        "Respond to the user in this same conversation with a short, helpful update. "
        "Summarize the result clearly. If the run failed, explain the failure briefly and "
        "suggest the next step."
    )


async def enqueue_acp_completion_followup(
    state_manager: Any, queues: Any, record: ACPTaskRecord
) -> bool:
    """Queue a same-thread follow-up turn for a terminal ACP run."""
    thread_id = _completion_followup_thread_id(record)
    if not thread_id:
        return False
    await queues.enqueue_input(
        _build_completion_followup_prompt(record),
        metadata={
            "thread_id": thread_id,
            "notify_on_completion": True,
            "completion_event_type": "agent_message",
            "source": "acp_task_completion_followup",
            "task_id": record.task_id,
            "agent": record.agent_name,
            "acp_status": record.status,
            "title": _notification_title(record),
            "level": "error" if record.status != ACPTaskStatus.COMPLETED.value else "info",
        },
    )
    await state_manager.update_acp_task(
        record.task_id,
        {"notification_sent_at": utcnow(), "updated_at": utcnow()},
    )
    return True
