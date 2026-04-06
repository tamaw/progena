"""Shared A2A task tracking, refresh, and notification helpers."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, cast

from ..state.models import A2ATaskRecord, A2ATaskStatus
from ..tools.mcp import A2AClient
from ..tools.user_communication import EventPusher, UserCommunicationTools
from .time_utils import utcnow

logger = logging.getLogger(__name__)

DEFAULT_A2A_HISTORY_LENGTH = 20
DEFAULT_A2A_POLL_SECONDS = 5.0


def normalize_a2a_task_status(raw_state: Any) -> A2ATaskStatus:
    """Map raw SDK task state values onto the persisted status enum."""
    value = str(raw_state or "").strip().lower()
    if value == "input_required":
        value = A2ATaskStatus.INPUT_REQUIRED.value
    if value == "cancelled":
        value = A2ATaskStatus.CANCELED.value
    try:
        return A2ATaskStatus(value)
    except ValueError:
        return A2ATaskStatus.UNKNOWN


def _extract_text_from_parts(parts: Any) -> str | None:
    if not isinstance(parts, list):
        return None

    text_parts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            text_parts.append(text.strip())
            continue
        root = part.get("root")
        if isinstance(root, dict):
            root_text = root.get("text")
            if isinstance(root_text, str) and root_text.strip():
                text_parts.append(root_text.strip())

    if not text_parts:
        return None
    return "\n".join(text_parts)


def extract_message_text(message_payload: Any) -> str | None:
    """Extract the most useful human-readable text from an A2A message payload."""
    if isinstance(message_payload, str) and message_payload.strip():
        return message_payload.strip()
    if not isinstance(message_payload, dict):
        return None
    parts = message_payload.get("parts")
    text = _extract_text_from_parts(parts)
    if text:
        return text
    for key in ("text", "content"):
        value = message_payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def summarize_task_payload(task_payload: dict[str, Any]) -> str | None:
    """Produce a compact user-facing progress summary from an A2A task snapshot."""
    status = task_payload.get("status")
    if isinstance(status, dict):
        status_text = extract_message_text(status.get("message"))
        if status_text:
            return status_text

    history = task_payload.get("history")
    if isinstance(history, list):
        for item in reversed(history):
            text = extract_message_text(item)
            if text:
                return text

    artifacts = task_payload.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in reversed(artifacts):
            if not isinstance(artifact, dict):
                continue
            text = extract_message_text(artifact)
            if text:
                return text
            parts_text = _extract_text_from_parts(artifact.get("parts"))
            if parts_text:
                return parts_text

    return None


def is_task_payload(payload: Any) -> bool:
    """Return whether *payload* looks like a serialized A2A Task."""
    return (
        isinstance(payload, dict)
        and str(payload.get("kind") or "task") == "task"
        and isinstance(payload.get("id"), str)
    )


def build_task_response(record: A2ATaskRecord) -> dict[str, Any]:
    """Format a tracked task record for tool responses."""
    return {
        "task_id": record.task_id,
        "agent": record.agent_name,
        "status": record.status,
        "context_id": record.context_id,
        "summary": record.summary,
        "latest_message": record.latest_message,
        "last_error": record.last_error,
        "notify_on_completion": record.notify_on_completion,
        "notification_sent_at": record.notification_sent_at.isoformat()
        if record.notification_sent_at
        else None,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "task": record.latest_task,
    }


def _build_notification_title(record: A2ATaskRecord) -> str:
    agent_name = record.agent_name.replace("_", " ").strip() or "Agent"
    status = record.status.replace("-", " ")
    return f"{agent_name} task {status}"


def _build_notification_body(record: A2ATaskRecord) -> str:
    summary = (record.summary or record.latest_message or "").strip()
    if summary:
        return f"{summary}\n\ntask_id: {record.task_id}"
    return f"{record.agent_name} task {record.task_id} is {record.status}."


def _notification_level(record: A2ATaskRecord) -> str:
    if record.status == A2ATaskStatus.COMPLETED.value:
        return "success"
    if record.status in {A2ATaskStatus.FAILED.value, A2ATaskStatus.REJECTED.value}:
        return "error"
    if record.status in {A2ATaskStatus.CANCELED.value, A2ATaskStatus.AUTH_REQUIRED.value}:
        return "warning"
    return "info"


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
    task_id = (
        str(metadata.get("a2a_task_id") or "").strip() or f"{agent_name}_{uuid.uuid4().hex[:12]}"
    )
    context_id = str(metadata.get("a2a_context_id") or "").strip() or uuid.uuid4().hex
    metadata["a2a_task_id"] = task_id
    metadata["a2a_context_id"] = context_id

    response = await A2AClient().call_agent(
        agent_config=agent_config,
        request=request_text,
        context=metadata,
        timeout=timeout,
        task_id=task_id,
        context_id=context_id,
        blocking=blocking,
    )

    task_record: A2ATaskRecord | None = None
    payload = response.get("result") if isinstance(response, dict) else None
    if response.get("success") and is_task_payload(payload):
        task_record = await upsert_tracked_task_from_payload(
            state_manager,
            task_payload=cast(dict[str, Any], payload),
            agent_name=agent_name,
            agent_config=agent_config,
            source=source,
            request_text=request_text,
            request_metadata=metadata,
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


async def send_completion_notification(
    state_manager: Any, llm_client: Any, record: A2ATaskRecord
) -> bool:
    """Emit a completion notification for a tracked terminal task."""
    push = getattr(llm_client, "_push_event", None)
    if not callable(push):
        return False

    current_state = await state_manager.get_current_state()
    run_id = str(current_state.get("run_id") or "").strip() or None
    tools = UserCommunicationTools(cast(EventPusher, push), run_id=run_id)
    await tools.notify_user(
        title=_build_notification_title(record),
        body=_build_notification_body(record),
        level=_notification_level(record),
        metadata={
            "task_id": record.task_id,
            "agent": record.agent_name,
            "a2a_status": record.status,
        },
    )
    await state_manager.update_a2a_task(
        record.task_id,
        {"notification_sent_at": utcnow(), "updated_at": utcnow()},
    )
    return True


class A2ATaskTracker:
    """Background poller that keeps tracked A2A task state fresh."""

    def __init__(
        self,
        state_manager: Any,
        llm_client: Any = None,
        *,
        poll_seconds: float = DEFAULT_A2A_POLL_SECONDS,
    ) -> None:
        self.state_manager = state_manager
        self.llm_client = llm_client
        self.poll_seconds = max(1.0, poll_seconds)

    async def poll_once(self) -> None:
        """Refresh active tasks and notify on newly terminal ones."""
        tasks = await self.state_manager.get_a2a_tasks()
        for record in tasks:
            try:
                current = record
                if not record.is_terminal:
                    current = await refresh_tracked_a2a_task(self.state_manager, record.task_id)
                if (
                    current.is_terminal
                    and current.notify_on_completion
                    and current.notification_sent_at is None
                    and self.llm_client is not None
                ):
                    await send_completion_notification(self.state_manager, self.llm_client, current)
            except Exception as exc:
                logger.warning("A2A task refresh failed for %s: %s", record.task_id, exc)

    async def run(self) -> None:
        """Continuously refresh tracked A2A tasks until cancelled."""
        while True:
            await self.poll_once()
            await asyncio.sleep(self.poll_seconds)
