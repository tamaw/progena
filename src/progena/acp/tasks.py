"""ACP task helpers shared by the runtime and backend supervisor."""

from __future__ import annotations

from typing import Any

from src.progena.acp.models import ACPAgentConfig, ACPFailureKind, ACPTaskRecord, ACPTaskStatus

from ..utils import utcnow
from .agents import build_agent_args
from .runner import ACPRunner


def acp_config_from_agent_config(agent_config: dict[str, Any]) -> ACPAgentConfig:
    """Validate the ACP configuration from one registry entry."""
    raw = agent_config.get("acp")
    if isinstance(raw, ACPAgentConfig):
        return raw
    if isinstance(raw, dict):
        return ACPAgentConfig(**raw)
    name = agent_config.get("name") or "<unknown>"
    raise ValueError(f"ACP agent {name!r} is missing its 'acp' configuration")


def task_cwd(acp_config: ACPAgentConfig, requested_cwd: str | None) -> str:
    """Choose the agent-host working directory without inventing a sandbox."""
    return requested_cwd or acp_config.cwd or "/tmp"  # nosec B108 - agent host working path


def task_command(acp_config: ACPAgentConfig) -> str:
    """Return the display command persisted on an ACP task."""
    if acp_config.port is not None:
        return f"{acp_config.host or '127.0.0.1'}:{acp_config.port}"
    return acp_config.command


# Phrases agents use when the provider account, not the task, is out of
# capacity, e.g. Claude Code's "You've hit your session limit · resets 12:50am".
# Spaced phrases only: an identifier such as `rate_limiter.py` in an ordinary
# error must not read as a limit.
_USAGE_LIMIT_MARKERS = (
    "session limit",
    "usage limit",
    "rate limit",
    "quota",
    "too many requests",
)


def acp_failure_kind(error: str | None) -> ACPFailureKind:
    """Classify a failed turn's error so workflows can match on it without a model.

    Only the error is inspected: a failed turn's output may legitimately
    discuss rate limits in the code it was changing.
    """
    text = (error or "").lower()
    return "usage_limit" if any(marker in text for marker in _USAGE_LIMIT_MARKERS) else "error"


def runner_for(acp_config: ACPAgentConfig, cwd: str) -> ACPRunner:
    """Build the session-oriented driver for a configured ACP agent."""
    if acp_config.port is not None:
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


def _result_reference(record: ACPTaskRecord) -> str:
    return f"acp_get_result(task_id={record.task_id!r}, turn={record.turn})"


def build_acp_completion_message(record: ACPTaskRecord) -> str:
    """Build a compact completion record that points to retained task evidence."""
    name = record.agent_name.replace("_", " ").strip() or "ACP agent"
    reference = _result_reference(record)
    if record.status == ACPTaskStatus.COMPLETED.value:
        return f"ACP agent '{name}' finished. Details: {reference}."
    reason = (record.last_error or record.stop_reason or "unknown error")[:256]
    return f"ACP agent '{name}' run {record.status}: {reason}. Details: {reference}."


def _notification_title(record: ACPTaskRecord, event_type: str) -> str:
    name = record.agent_name.replace("_", " ").strip() or "ACP agent"
    if event_type == "pending_input":
        return f"{name} needs input"
    if event_type == "stall":
        return f"{name} may be stalled"
    return f"{name} run {record.status}"


def _completion_followup_thread_id(record: ACPTaskRecord) -> str | None:
    return str(record.request_metadata.get("thread_id") or "").strip() or None


def _input_prompt(record: ACPTaskRecord) -> str:
    pending = record.pending_input if isinstance(record.pending_input, dict) else {}
    input_id = str(pending.get("id") or "").strip()
    kind = str(pending.get("kind") or "input").strip()
    message = str(pending.get("message") or "").strip()
    preview = message[:1000]
    truncated = " (truncated; retrieve details)" if len(message) > len(preview) else ""
    question = f"\nQuestion: {preview}{truncated}" if preview else ""
    return (
        f"ACP task {record.task_id} (turn {record.turn}) needs {kind} input "
        f"(pending input {input_id}).{question}\n"
        f"Read the exact input before answering: acp_get_result(task_id={record.task_id!r}, "
        f"turn={record.turn}, source='input'). Respond using acp_answer with that input ID."
    )


def _build_followup_prompt(record: ACPTaskRecord, event_type: str) -> str:
    reference = _result_reference(record)
    if event_type == "pending_input":
        return _input_prompt(record)
    if event_type == "stall":
        return (
            f"ACP task {record.task_id} (turn {record.turn}) may be stalled. "
            f"Inspect it only if action is needed: {reference}."
        )
    error = ""
    if record.status != ACPTaskStatus.COMPLETED.value:
        reason = (record.last_error or record.stop_reason or "unknown error").strip()
        preview = reason[:1000]
        error = f"\nError: {preview}{'…' if len(reason) > len(preview) else ''}"
    return (
        f"ACP task {record.task_id} (turn {record.turn}) reached {record.status}.{error}\n"
        f"Details: {reference}."
    )


async def enqueue_acp_completion_followup(
    state_manager: Any,
    queues: Any,
    record: ACPTaskRecord,
    *,
    event_type: str = "terminal",
    event_identity: str | None = None,
) -> bool:
    """Queue one compact, same-thread follow-up for an actionable ACP event."""
    thread_id = _completion_followup_thread_id(record)
    if not thread_id:
        return False
    await queues.enqueue_input(
        _build_followup_prompt(record, event_type),
        metadata={
            "thread_id": thread_id,
            "notify_on_completion": True,
            "completion_event_type": "agent_message",
            "source": "acp_task_actionable_followup",
            "task_id": record.task_id,
            "agent": record.agent_name,
            "acp_status": record.status,
            "acp_turn": record.turn,
            "acp_event_type": event_type,
            "title": _notification_title(record, event_type),
            "level": "error"
            if event_type == "terminal" and record.status != ACPTaskStatus.COMPLETED.value
            else "info",
        },
    )
    acknowledgements = dict(record.material_event_acknowledgements)
    if event_identity is not None:
        acknowledgements[event_type] = event_identity
    update: dict[str, Any] = {
        "material_event_acknowledgements": acknowledgements,
        "updated_at": utcnow(),
    }
    if event_type == "terminal":
        update["notification_sent_at"] = utcnow()
    await state_manager.update_acp_task(record.task_id, update)
    return True
