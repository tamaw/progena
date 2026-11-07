"""Per-task persistence for tracked async-subagent work."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from src.progena.async_subagents.models import AsyncSubagentTaskRecord
from src.progena.persistence.documents import StateDocuments
from src.progena.persistence.task_evidence import (
    archive_task_evidence,
    archive_task_record,
    load_archived_task_record,
    load_task_evidence,
)
from src.progena.utils import utcnow

logger = logging.getLogger(__name__)

ASYNC_SUBAGENT_TASK_KEY_PREFIX = "async_subagent_task:"
_TASK_KIND = "async_subagent"
_TERMINAL_RETENTION = timedelta(days=30)
_MAX_INLINE_TEXT_BYTES = 4 * 1024
_MAX_INLINE_METADATA_BYTES = 8 * 1024
_MAX_INLINE_RECORD_BYTES = 24 * 1024


def _task_key(task_id: str) -> str:
    return f"{ASYNC_SUBAGENT_TASK_KEY_PREFIX}{task_id}"


def _json_size(value: Any) -> int:
    return len(json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":")).encode())


def _truncate_utf8(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    suffix = "… [full value archived]"
    body = encoded[: max(limit - len(suffix.encode("utf-8")), 0)].decode("utf-8", "ignore")
    return f"{body}{suffix}"


def _compact_text(value: str, limit: int, evidence_ref: Any) -> str:
    """Keep a structured summary parseable when its full value is archived."""
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return _truncate_utf8(value, limit)
    if isinstance(parsed, (dict, list)):
        return json.dumps(
            {"truncated": True, "evidence_ref": evidence_ref.model_dump(mode="json")},
            separators=(",", ":"),
        )
    return _truncate_utf8(value, limit)


def _is_archived_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if value.endswith("… [full value archived]"):
        return True
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and parsed.get("truncated") is True and "evidence_ref" in parsed


def _routing_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata[key]
        for key in ("caller", "owner", "parent_thread_id", "thread_id")
        if key in metadata
    }


def _record_payload(record: AsyncSubagentTaskRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


def _same_semantics(left: AsyncSubagentTaskRecord, right: AsyncSubagentTaskRecord) -> bool:
    left_payload = _record_payload(left)
    right_payload = _record_payload(right)
    left_payload.pop("updated_at", None)
    right_payload.pop("updated_at", None)
    return left_payload == right_payload


def _newer(left: AsyncSubagentTaskRecord, right: AsyncSubagentTaskRecord) -> bool:
    return left.updated_at > right.updated_at


class AsyncSubagentStore:
    """Own compact per-record async-subagent task documents and evidence."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def _compact_record(self, record: AsyncSubagentTaskRecord) -> AsyncSubagentTaskRecord:
        compact = record.model_copy(deep=True)
        evidence_refs = dict(compact.evidence_refs)

        if compact.latest_run is not None:
            evidence_refs["latest_run"] = await archive_task_evidence(
                self._access.store, compact.latest_run
            )
            compact.latest_run = None

        for field in ("request_text", "summary", "latest_message", "last_error"):
            value = getattr(compact, field)
            if field in evidence_refs and not _is_archived_placeholder(value):
                evidence_refs.pop(field, None)
            if isinstance(value, str) and len(value.encode("utf-8")) > _MAX_INLINE_TEXT_BYTES:
                evidence_ref = await archive_task_evidence(
                    self._access.store, {"field": field, "value": value}
                )
                evidence_refs[field] = evidence_ref
                setattr(
                    compact,
                    field,
                    _compact_text(value, _MAX_INLINE_TEXT_BYTES, evidence_ref),
                )

        if _json_size(compact.request_metadata) > _MAX_INLINE_METADATA_BYTES:
            evidence_refs["request_metadata"] = await archive_task_evidence(
                self._access.store, {"field": "request_metadata", "value": compact.request_metadata}
            )
            compact.request_metadata = _routing_metadata(compact.request_metadata)

        compact.evidence_refs = evidence_refs
        if _json_size(_record_payload(compact)) > _MAX_INLINE_RECORD_BYTES:
            raise ValueError(
                "Async-subagent task "
                f"{compact.task_id!r} routing metadata exceeds the inline record limit"
            )
        return compact

    async def _compact_updates(
        self, updates: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Archive oversized update values before the locked record mutation."""
        compact = dict(updates)
        evidence_refs: dict[str, Any] = {}
        raw_run = compact.get("latest_run")
        if isinstance(raw_run, dict):
            evidence_refs["latest_run"] = await archive_task_evidence(self._access.store, raw_run)
            compact["latest_run"] = None
        for field in ("request_text", "summary", "latest_message", "last_error"):
            value = compact.get(field)
            if isinstance(value, str) and len(value.encode("utf-8")) > _MAX_INLINE_TEXT_BYTES:
                reference = await archive_task_evidence(
                    self._access.store, {"field": field, "value": value}
                )
                evidence_refs[field] = reference
                compact[field] = _compact_text(value, _MAX_INLINE_TEXT_BYTES, reference)
        metadata = compact.get("request_metadata")
        if isinstance(metadata, dict) and _json_size(metadata) > _MAX_INLINE_METADATA_BYTES:
            evidence_refs["request_metadata"] = await archive_task_evidence(
                self._access.store, {"field": "request_metadata", "value": metadata}
            )
            compact["request_metadata"] = _routing_metadata(metadata)
        return compact, evidence_refs

    async def _hydrate_record(self, record: AsyncSubagentTaskRecord) -> AsyncSubagentTaskRecord:
        hydrated = record.model_copy(deep=True)
        for field, reference in hydrated.evidence_refs.items():
            evidence = await load_task_evidence(self._access.store, reference)
            if evidence is None:
                continue
            if field == "latest_run" and isinstance(evidence, dict):
                hydrated.latest_run = evidence
            elif (
                isinstance(evidence, dict)
                and evidence.get("field") == field
                and field
                in {
                    "request_text",
                    "request_metadata",
                    "summary",
                    "latest_message",
                    "last_error",
                }
            ):
                restored = evidence.get("value")
                if (
                    field == "request_metadata"
                    and isinstance(restored, dict)
                    and isinstance(hydrated.request_metadata, dict)
                ):
                    restored = {**restored, **hydrated.request_metadata}
                setattr(hydrated, field, restored)
        return hydrated

    @staticmethod
    def _merge_record(
        current: AsyncSubagentTaskRecord, candidate: AsyncSubagentTaskRecord
    ) -> AsyncSubagentTaskRecord:
        if _newer(current, candidate):
            return current
        merged = candidate.model_copy(deep=True)
        merged.created_at = current.created_at
        merged.request_metadata = {**current.request_metadata, **candidate.request_metadata}
        merged.notification_sent_at = current.notification_sent_at or candidate.notification_sent_at
        merged.completed_at = current.completed_at or candidate.completed_at
        return merged

    async def _upsert_record(self, record: AsyncSubagentTaskRecord) -> bool:
        candidate = await self._compact_record(record)

        def mutate(current: Any | None) -> tuple[dict[str, Any], bool]:
            if not isinstance(current, dict):
                return _record_payload(candidate), True
            try:
                existing = AsyncSubagentTaskRecord.model_validate(current)
            except (TypeError, ValueError):
                return _record_payload(candidate), True
            merged = self._merge_record(existing, candidate)
            if _same_semantics(existing, merged):
                return current, False
            return _record_payload(merged), True

        _changed, did_write = await self._access.store.mutate_document_if_changed(
            _task_key(candidate.task_id), mutate
        )
        return did_write

    async def _compact_records(
        self,
    ) -> list[tuple[str, AsyncSubagentTaskRecord, dict[str, Any]]]:
        rows = await self._access.store.list_documents(ASYNC_SUBAGENT_TASK_KEY_PREFIX)
        records: list[tuple[str, AsyncSubagentTaskRecord, dict[str, Any]]] = []
        for key, payload in rows:
            try:
                records.append((key, AsyncSubagentTaskRecord.model_validate(payload), payload))
            except (TypeError, ValueError):
                logger.warning("Skipping invalid async-subagent task document %s", key)
        return records

    @staticmethod
    def _eligible_for_prune(record: AsyncSubagentTaskRecord) -> bool:
        if not record.is_terminal:
            return False
        if record.notify_on_completion and record.notification_sent_at is None:
            return False
        terminal_at = record.completed_at or record.updated_at
        return terminal_at <= utcnow() - _TERMINAL_RETENTION

    async def _prune_terminal_records(
        self, records: list[tuple[str, AsyncSubagentTaskRecord, dict[str, Any]]]
    ) -> list[AsyncSubagentTaskRecord]:
        retained: list[AsyncSubagentTaskRecord] = []
        for key, record, payload in records:
            if not self._eligible_for_prune(record):
                retained.append(record)
                continue
            await archive_task_record(
                self._access.store,
                task_kind=_TASK_KIND,
                task_id=record.task_id,
                record=_record_payload(record),
            )
            deleted = await self._access.store.delete_document_if(
                key, lambda current, expected=payload: current == expected
            )
            if not deleted:
                retained.append(record)
        return retained

    async def get_async_subagent_tasks(self) -> list[AsyncSubagentTaskRecord]:
        """Return compact task records; list/poll readers never load raw runs."""
        records = await self._prune_terminal_records(await self._compact_records())
        return list(records)

    async def save_async_subagent_tasks(self, tasks: list[AsyncSubagentTaskRecord]) -> None:
        """Persist supplied task records individually without rewriting history."""
        for task in tasks:
            await self._upsert_record(task)

    async def get_async_subagent_task(
        self, task_id: str, *, hydrate_evidence: bool = True
    ) -> AsyncSubagentTaskRecord | None:
        """Return one task, hydrating raw evidence only for an explicit detail read."""
        payload = await self._access.store.peek_document(_task_key(task_id))
        if not isinstance(payload, dict):
            return None
        try:
            record = AsyncSubagentTaskRecord.model_validate(payload)
        except (TypeError, ValueError):
            return None
        retained = await self._prune_terminal_records([(_task_key(task_id), record, payload)])
        if not retained:
            return None
        return await self._hydrate_record(retained[0]) if hydrate_evidence else retained[0]

    async def get_async_subagent_task_evidence(self, task_id: str) -> dict[str, Any] | None:
        """Return the complete latest run, including a pruned task's archive."""
        payload = await self._access.store.peek_document(_task_key(task_id))
        record_payload = (
            payload
            if isinstance(payload, dict)
            else await load_archived_task_record(
                self._access.store, task_kind=_TASK_KIND, task_id=task_id
            )
        )
        if not isinstance(record_payload, dict):
            return None
        try:
            record = AsyncSubagentTaskRecord.model_validate(record_payload)
        except (TypeError, ValueError):
            return None
        evidence = await load_task_evidence(
            self._access.store, record.evidence_refs.get("latest_run")
        )
        return evidence if isinstance(evidence, dict) else record.latest_run

    async def upsert_async_subagent_task(self, task: AsyncSubagentTaskRecord) -> None:
        """Insert or merge one task record without touching unrelated tasks."""
        await self._upsert_record(task)

    async def update_async_subagent_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        """Apply field updates without a write for an identical observation."""
        compact_updates, update_evidence = await self._compact_updates(updates)

        def mutate(current: Any | None) -> tuple[Any, bool]:
            if not isinstance(current, dict):
                return current, False
            try:
                existing = AsyncSubagentTaskRecord.model_validate(current)
            except (TypeError, ValueError):
                return current, False
            payload = existing.model_dump()
            payload.update(compact_updates)
            refs = dict(existing.evidence_refs)
            for field in (
                "latest_run",
                "request_text",
                "summary",
                "latest_message",
                "last_error",
                "request_metadata",
            ):
                if field in compact_updates:
                    refs.pop(field, None)
            refs.update(update_evidence)
            payload["evidence_refs"] = refs
            candidate = AsyncSubagentTaskRecord.model_validate(payload)
            if _json_size(_record_payload(candidate)) > _MAX_INLINE_RECORD_BYTES:
                raise ValueError(
                    f"Async-subagent task {task_id!r} update exceeds the inline record limit"
                )
            if _same_semantics(existing, candidate):
                return current, True
            return _record_payload(candidate), True

        found, _did_write = await self._access.store.mutate_document_if_changed(
            _task_key(task_id), mutate
        )
        return bool(found)
