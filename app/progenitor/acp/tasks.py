"""ACP task helpers shared by the runtime and backend supervisor."""

from __future__ import annotations

from typing import Any

from ..state.models import ACPAgentConfig, ACPFailureKind, ACPTaskRecord, ACPTaskStatus
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
    original_request = record.prompt.strip() or "(missing original prompt)"
    return (
        "An async ACP agent task that the user started earlier in this conversation has finished.\n\n"
        f"Agent: {record.agent_name}\n"
        f"Status: {record.status}\n"
        f"Original prompt: {original_request}\n\n"
        "Raw task result:\n"
        f"{raw_result}\n\n"
        "Respond to the user in this same conversation with a short, helpful update. "
        "Summarize the result clearly. If the task failed, explain the failure briefly and "
        "suggest the next step."
    )


async def enqueue_acp_completion_followup(
    state_manager: Any, queues: Any, record: ACPTaskRecord
) -> bool:
    """Queue a same-thread follow-up turn for a terminal ACP task."""
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
