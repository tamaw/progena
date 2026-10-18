"""Per-record ACP task persistence helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import TypeAdapter

from ..document_kinds import ACP_TASK_KEY_PREFIX
from .models import ACPTaskRecord

_JSON_FIELDS = TypeAdapter(dict[str, Any])


def acp_task_key(task_id: str) -> str:
    """Return the state-store key for one ACP task."""
    return f"{ACP_TASK_KEY_PREFIX}{task_id}"


def _payload_to_record(payload: dict[str, Any], *, task_id: str) -> ACPTaskRecord:
    """Validate one stored task, filling ``task_id`` from its key."""
    return ACPTaskRecord.model_validate({"task_id": task_id, **payload})


async def get_acp_tasks(
    *,
    list_documents_fn: Callable[[str], Awaitable[list[tuple[str, Any]]]],
) -> list[ACPTaskRecord]:
    """Return each persisted ACP task, sorted by its task id."""
    records: list[ACPTaskRecord] = []
    for key, payload in await list_documents_fn(ACP_TASK_KEY_PREFIX):
        if isinstance(payload, dict):
            records.append(
                _payload_to_record(payload, task_id=key.removeprefix(ACP_TASK_KEY_PREFIX))
            )
    return sorted(records, key=lambda record: record.task_id)


async def get_acp_task(
    *,
    task_id: str,
    peek_document_fn: Callable[[str], Awaitable[Any | None]],
) -> ACPTaskRecord | None:
    payload = await peek_document_fn(acp_task_key(task_id))
    return _payload_to_record(payload, task_id=task_id) if isinstance(payload, dict) else None


async def upsert_acp_task(
    *,
    task: ACPTaskRecord,
    save_document_fn: Callable[[str, Any], Awaitable[None]],
) -> None:
    """Persist one ACP task document."""
    await save_document_fn(acp_task_key(task.task_id), task.model_dump(mode="json"))


async def update_acp_task(
    *,
    task_id: str,
    updates: dict[str, Any],
    merge_document_fn: Callable[[str, dict[str, Any]], Awaitable[bool]],
) -> bool:
    """Atomically merge top-level fields into one ACP task document.

    The backend supervisor writes the same document concurrently, so this never
    rewrites the whole payload from a stale read.
    """
    return await merge_document_fn(
        acp_task_key(task_id), _JSON_FIELDS.dump_python(updates, mode="json")
    )


async def append_acp_task_message(
    *,
    task_id: str,
    message: dict[str, Any],
    append_document_list_fn: Callable[[str, str, Any], Awaitable[bool]],
) -> bool:
    """Atomically append one requester message to an ACP task."""
    return await append_document_list_fn(acp_task_key(task_id), "messages", message)


async def set_acp_task_answer(
    *,
    task_id: str,
    input_id: str,
    answer: Any,
    merge_document_object_fn: Callable[[str, str, dict[str, Any]], Awaitable[bool]],
) -> bool:
    """Atomically record one answer without overwriting other pending answers."""
    return await merge_document_object_fn(acp_task_key(task_id), "answers", {input_id: answer})
