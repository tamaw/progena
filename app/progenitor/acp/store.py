"""Per-record ACP task persistence helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import TypeAdapter

from app.progenitor.acp.models import ACPTaskRecord
from app.progenitor.documents.kinds import ACP_TASK_KEY_PREFIX
from app.progenitor.persistence.documents import StateDocuments

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


class ACPTaskStore:
    """Own acp document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def get_acp_tasks(self) -> list[ACPTaskRecord]:
        """Return all per-record ACP task documents."""
        return await get_acp_tasks(
            list_documents_fn=self._access.store.list_documents,
        )

    async def get_acp_task(self, task_id: str) -> ACPTaskRecord | None:
        """Return one ACP task by id, or ``None`` if not found."""
        return await get_acp_task(
            task_id=task_id,
            peek_document_fn=self._access.store.peek_document,
        )

    async def upsert_acp_task(self, task: ACPTaskRecord) -> None:
        """Insert or update one ACP task document."""
        await upsert_acp_task(
            task=task,
            save_document_fn=self._access.store.save_document,
        )

    async def update_acp_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        """Apply a partial update to one ACP task document."""
        return await update_acp_task(
            task_id=task_id,
            updates=updates,
            merge_document_fn=self._access.store.merge_document,
        )

    async def append_acp_task_message(self, task_id: str, message: dict[str, Any]) -> bool:
        """Atomically append a requester message to an ACP task document."""
        return await append_acp_task_message(
            task_id=task_id,
            message=message,
            append_document_list_fn=self._access.store.append_document_list,
        )

    async def set_acp_task_answer(self, task_id: str, input_id: str, answer: Any) -> bool:
        """Atomically store one ACP input answer."""
        return await set_acp_task_answer(
            task_id=task_id,
            input_id=input_id,
            answer=answer,
            merge_document_object_fn=self._access.store.merge_document_object,
        )
