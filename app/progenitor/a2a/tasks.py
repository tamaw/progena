"""Tracked A2A task persistence, refresh, and notification helpers."""

from __future__ import annotations

import logging
from typing import Any, cast

from ..state.models import A2ATaskRecord, A2ATaskStatus
from ..utils import utcnow
from .client import A2AClient
from .payloads import (
    build_completion_message,
    extract_message_text,
    is_task_payload,
    normalize_a2a_task_status,
    notification_level,
    summarize_task_payload,
)

logger = logging.getLogger(__name__)

DEFAULT_A2A_HISTORY_LENGTH = 20
DEFAULT_A2A_POLL_SECONDS = 5.0


def _build_notification_title(record: A2ATaskRecord) -> str:
    agent_name = record.agent_name.replace("_", " ").strip() or "Agent"
    status = record.status.replace("-", " ")
    return f"{agent_name} task {status}"


def _completion_followup_thread_id(record: A2ATaskRecord) -> str | None:
    return str(record.request_metadata.get("thread_id") or "").strip() or None


def _build_completion_followup_prompt(record: A2ATaskRecord) -> str:
    raw_result = build_completion_message(record).strip()
    original_request = record.request_text.strip() or "(missing original request)"
    return (
        "An async external agent task that the user started earlier in this conversation has "
        "finished.\n\n"
        f"Agent: {record.agent_name}\n"
        f"Status: {record.status}\n"
        f"Original request: {original_request}\n\n"
        "Raw task result:\n"
        f"{raw_result}\n\n"
        "Respond to the user in this same conversation with a short, helpful update. "
        "Summarize the result clearly. If the task failed, explain the failure briefly and "
        "suggest the next step."
    )


def _build_task_record(
    *,
    task_id: str,
    agent_name: str,
    agent_config: dict[str, Any],
    source: str,
    request_text: str,
    request_metadata: dict[str, Any],
    notify_on_completion: bool,
) -> A2ATaskRecord:
    now = utcnow()
    return A2ATaskRecord(
        task_id=task_id,
        agent_name=agent_name,
        agent_url=str(agent_config.get("url") or "").strip() or None,
        agent_card_url=str(agent_config.get("agent_card_url") or "").strip() or None,
        transport=str(agent_config.get("transport") or "http").strip() or "http",
        context_id=str(request_metadata.get("a2a_context_id") or "").strip() or None,
        source=source,
        request_text=request_text,
        request_metadata=dict(request_metadata),
        notify_on_completion=notify_on_completion,
        created_at=now,
        updated_at=now,
    )


async def upsert_tracked_task_from_payload(
    state_manager: Any,
    *,
    task_payload: dict[str, Any],
    agent_name: str,
    agent_config: dict[str, Any],
    source: str,
    request_text: str,
    request_metadata: dict[str, Any] | None = None,
    notify_on_completion: bool = True,
) -> A2ATaskRecord:
    """Create or update the persisted record for a serialized A2A task payload."""
    metadata = dict(request_metadata or {})
    task_id = str(task_payload.get("id") or metadata.get("a2a_task_id") or "").strip()
    if not task_id:
        raise ValueError("A2A task payload missing id")

    record = cast(A2ATaskRecord | None, await state_manager.get_a2a_task(task_id))
    if record is None:
        record = _build_task_record(
            task_id=task_id,
            agent_name=agent_name,
            agent_config=agent_config,
            source=source,
            request_text=request_text,
            request_metadata=metadata,
            notify_on_completion=notify_on_completion,
        )

    record.agent_name = agent_name
    record.agent_url = str(agent_config.get("url") or record.agent_url or "").strip() or None
    record.agent_card_url = (
        str(agent_config.get("agent_card_url") or record.agent_card_url or "").strip() or None
    )
    record.transport = (
        str(agent_config.get("transport") or record.transport or "http").strip() or "http"
    )
    record.context_id = (
        str(task_payload.get("contextId") or record.context_id or "").strip() or None
    )
    record.request_text = request_text or record.request_text
    record.request_metadata = {**record.request_metadata, **metadata}
    record.source = source
    record.notify_on_completion = notify_on_completion
    record.latest_task = task_payload
    record.status = normalize_a2a_task_status(
        task_payload.get("status", {}).get("state")
        if isinstance(task_payload.get("status"), dict)
        else None
    )
    record.latest_message = (
        extract_message_text(task_payload.get("status", {}).get("message"))
        if isinstance(task_payload.get("status"), dict)
        else None
    )
    record.summary = summarize_task_payload(task_payload) or record.latest_message
    if record.status in {
        A2ATaskStatus.FAILED.value,
        A2ATaskStatus.REJECTED.value,
        A2ATaskStatus.AUTH_REQUIRED.value,
    }:
        record.last_error = record.summary or record.latest_message
    elif record.status == A2ATaskStatus.UNKNOWN.value:
        record.last_error = record.last_error
    else:
        record.last_error = None
    record.updated_at = utcnow()
    if record.is_terminal and record.completed_at is None:
        record.completed_at = record.updated_at
    await state_manager.upsert_a2a_task(record)
    return cast(A2ATaskRecord, record)


async def dispatch_tracked_a2a_task(
    *,
    state_manager: Any,
    agent_name: str,
    agent_config: dict[str, Any],
    request_text: str,
    request_metadata: dict[str, Any] | None = None,
    source: str,
    timeout: float,
    blocking: bool,
    notify_on_completion: bool = True,
) -> tuple[dict[str, Any], A2ATaskRecord | None]:
    """Submit an A2A task and persist the returned task snapshot when available."""
    metadata = dict(request_metadata or {})
    metadata.pop("a2a_task_id", None)
    metadata.pop("a2a_context_id", None)
    callback_url = str(metadata.pop("a2a_callback_url", "") or "").strip() or None
    if callback_url is None:
        callback_url = str(agent_config.get("callback_url", "") or "").strip() or None

    response = await A2AClient().call_agent(
        agent_config=agent_config,
        request=request_text,
        context=metadata,
        timeout=timeout,
        blocking=blocking,
        callback_url=callback_url,
    )

    task_record: A2ATaskRecord | None = None
    payload = response.get("result") if isinstance(response, dict) else None
    if response.get("success") and is_task_payload(payload):
        task_payload = cast(dict[str, Any], payload)
        tracked_metadata = dict(metadata)
        tracked_metadata["a2a_task_id"] = str(task_payload.get("id") or "")
        payload_context_id = str(task_payload.get("contextId") or "").strip()
        if payload_context_id:
            tracked_metadata["a2a_context_id"] = payload_context_id
        task_record = await upsert_tracked_task_from_payload(
            state_manager,
            task_payload=task_payload,
            agent_name=agent_name,
            agent_config=agent_config,
            source=source,
            request_text=request_text,
            request_metadata=tracked_metadata,
            notify_on_completion=notify_on_completion,
        )

    return response, task_record


async def _resolve_agent_config(
    state_manager: Any,
    record: A2ATaskRecord,
    *,
    tool_registry: Any = None,
) -> dict[str, Any]:
    if tool_registry is not None and hasattr(tool_registry, "find_by_name"):
        try:
            cfg = tool_registry.find_by_name(record.agent_name)
            if isinstance(cfg, dict):
                return dict(cfg)
        except Exception:
            logger.debug("Tool registry lookup failed for %s", record.agent_name, exc_info=True)

    registry = await state_manager.get_registry()
    if isinstance(registry, dict):
        by_name = registry.get(record.agent_name)
        if isinstance(by_name, dict):
            return {"name": record.agent_name, **by_name}
        agents = registry.get("agents")
        if isinstance(agents, list):
            for agent in agents:
                if isinstance(agent, dict) and str(agent.get("name") or "") == record.agent_name:
                    return dict(agent)

    if record.agent_url:
        return {
            "name": record.agent_name,
            "url": record.agent_url,
            "agent_card_url": record.agent_card_url,
            "transport": record.transport,
        }

    raise LookupError(f"No agent configuration available for tracked task {record.task_id}")


async def refresh_tracked_a2a_task(
    state_manager: Any,
    task_id: str,
    *,
    tool_registry: Any = None,
    history_length: int = DEFAULT_A2A_HISTORY_LENGTH,
    timeout: float = 15.0,
) -> A2ATaskRecord:
    """Fetch the latest task snapshot from the agent and persist it."""
    record = await state_manager.get_a2a_task(task_id)
    if record is None:
        raise LookupError(f"Unknown tracked A2A task: {task_id}")

    agent_config = await _resolve_agent_config(state_manager, record, tool_registry=tool_registry)
    result = await A2AClient().get_task(
        agent_config=agent_config,
        task_id=task_id,
        history_length=history_length,
        timeout=timeout,
    )
    if not result.get("success"):
        error = str(result.get("error") or "Failed to fetch task state")
        await state_manager.update_a2a_task(
            task_id,
            {"last_error": error, "updated_at": utcnow()},
        )
        raise RuntimeError(error)

    payload = result.get("result")
    if not is_task_payload(payload):
        raise RuntimeError(f"Invalid A2A task payload returned for {task_id}")

    return await upsert_tracked_task_from_payload(
        state_manager,
        task_payload=cast(dict[str, Any], payload),
        agent_name=record.agent_name,
        agent_config=agent_config,
        source=record.source,
        request_text=record.request_text,
        request_metadata=record.request_metadata,
        notify_on_completion=record.notify_on_completion,
    )


async def enqueue_completion_followup(
    state_manager: Any, queues: Any, record: A2ATaskRecord
) -> bool:
    """Queue a same-thread follow-up turn for a tracked terminal A2A task."""
    thread_id = _completion_followup_thread_id(record)
    if not thread_id:
        return False
    await queues.enqueue_input(
        _build_completion_followup_prompt(record),
        metadata={
            "thread_id": thread_id,
            "notify_on_completion": True,
            "completion_event_type": "agent_message",
            "source": "a2a_task_completion_followup",
            "task_id": record.task_id,
            "agent": record.agent_name,
            "a2a_status": record.status,
            "title": _build_notification_title(record),
            "level": notification_level(record),
        },
    )
    await state_manager.update_a2a_task(
        record.task_id,
        {"notification_sent_at": utcnow(), "updated_at": utcnow()},
    )
    return True
