"""Backend access to Postgres-backed LangGraph documents."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, LiteralString, NamedTuple, Protocol, cast

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolClosed

from src.backend.integrations.runtime_history import RuntimeConversationReader, RuntimeUnavailable
from src.progena.documents.kinds import (
    CANVAS_DOCUMENT_KEY_PREFIX,
    INBOX_MESSAGE_KEY_PREFIX,
    INSTRUCTION_DOCUMENT_KEY_PREFIX,
    SESSION_KEY_PREFIX,
)
from src.progena.routines.store import ROUTINE_GRAPH_KEY_PREFIX

PROGENA_STATE_PREFIX = "progena.state"
PROGENA_LOG_PREFIX = "progena.logs"
BACKEND_STATE_PREFIX = "backend.state"

# Postgres LISTEN/NOTIFY channel used to push ``store`` table changes to the
# backend without polling. Only state-namespace writes notify on it (the
# high-frequency ``progena.logs.*`` rows are deliberately excluded).
STORE_CHANGE_CHANNEL = "store_changed"
_STORE_CHANGE_PREFIXES = (PROGENA_STATE_PREFIX, BACKEND_STATE_PREFIX)
# Parallel runtime reads when the chat list needs messages for many threads.
CONVERSATION_FETCH_CONCURRENCY = 8

# Durable document event log: every create/update/delete of a state-namespace
# document appends one ``document_events`` row (with before/after payloads and
# the writer's origin) and NOTIFYs its sequence number on this channel.
DOCUMENT_EVENT_CHANNEL = "document_events"

DocumentEventOp = Literal["created", "updated", "deleted"]

# Session-scoped writer identity recorded on each document event. A workflow
# run sets it to its run id so events caused by its own writes carry their
# ancestry and cannot re-trigger the workflow that produced them.
_document_write_origin: ContextVar[str | None] = ContextVar(
    "progena_document_write_origin", default=None
)


@contextmanager
def document_write_origin(origin: str) -> Iterator[None]:
    """Tag every document write in this context with ``origin``."""

    token = _document_write_origin.set(origin)
    try:
        yield
    finally:
        _document_write_origin.reset(token)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredDocument:
    """One document loaded from the backend document store."""

    payload: Any
    updated_at: datetime | None = None


class DocumentEventPosition(NamedTuple):
    """A point in commit order: ``(txid, seq)`` of the last processed event.

    ``seq`` alone is allocated before commit, so a later-committing writer can
    hold a lower ``seq`` than events already visible. Ordering by the writing
    transaction id first, and only reading transactions older than every still
    open one, makes the visible prefix of the log final.
    """

    txid: int
    seq: int


EVENT_LOG_START = DocumentEventPosition(0, 0)


@dataclass(frozen=True)
class DocumentEvent:
    """One row of the durable document event log."""

    seq: int
    txid: int
    prefix: str
    key: str
    op: DocumentEventOp
    before: Any
    after: Any
    origin: str | None
    created_at: datetime

    @property
    def position(self) -> DocumentEventPosition:
        return DocumentEventPosition(self.txid, self.seq)


@dataclass(frozen=True)
class CheckpointThreadSnapshot:
    """Latest decoded LangGraph checkpoint messages for one thread."""

    thread_id: str
    checkpoint_id: str | None
    checkpoint_ts: str | None
    messages: list[Any]
    # Whether any checkpoint_writes row for this thread carries LangGraph's
    # own '__error__' channel -- see list_routine_runs for why this, not a
    # deserialized run_meta field, is the reliable failed/success signal.
    failed: bool = False
    # False when the runtime could not be reached to read ``messages`` (which is
    # then empty), so callers can tell "no messages" from "unknown right now".
    messages_available: bool = True


@dataclass(frozen=True)
class CheckpointMessageBlob:
    """One durable LangGraph ``messages`` value and its checkpoint context.

    Values may be immutable channel snapshots or pending message deltas. They
    remain in LangGraph's trusted serialization format; consumers must decode
    them with LangGraph's serializer rather than treating them as application
    JSON or accepting pickle payloads.
    """

    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str
    checkpoint_ts: datetime
    checkpoint_metadata: dict[str, Any]
    blob_type: str
    blob: bytes
    source: Literal["channel_blob", "checkpoint_write"] = "channel_blob"


@dataclass(frozen=True)
class RoutineRunSummary:
    """One routine execution, reconstructed from its checkpoint thread.

    ``thread_id`` follows ``routine:<routine_id>:<revision_id>:<run_id>``
    (see ``src.progena.routines.subagent.create_routine_subagent``);
    ``revision_id``/``run_id`` are parsed out of it, not stored separately.
    """

    thread_id: str
    routine_id: str
    revision_id: str | None
    run_id: str | None
    started_at: str | None
    last_checkpoint_at: str | None
    checkpoint_count: int
    failed: bool


@dataclass(frozen=True)
class RoutineRevisionSummary:
    """One immutable snapshot of a routine's graph document.

    Rows come from ``routine_graph_revisions`` (see
    ``src.backend.storage.documents.PostgresDocumentStore._ensure_routine_revision_history``),
    which a Postgres trigger appends to on every write to a routine's live
    document -- newest-first here mirrors the trigger's own capture order.
    """

    revision_id: str
    routine_id: str
    captured_at: str | None


@dataclass(frozen=True)
class KnowledgePageRevisionSummary:
    revision_id: str
    base_id: str
    path: str
    captured_at: str | None


@dataclass(frozen=True)
class InstructionRevisionSummary:
    revision_id: str
    path: str
    captured_at: str | None


@dataclass(frozen=True)
class CanvasRevision:
    """One immutable file change captured for a UI canvas."""

    id: int
    name: str
    path: str
    op: Literal["write", "delete"]
    content: str | None
    content_hash: str
    captured_at: str | None


def _compact_workflow_run_definition(definition: object) -> dict[str, Any]:
    """Keep only list-view workflow fields needed to name and classify audit spans."""
    if not isinstance(definition, dict):
        return {}
    graph = definition.get("graph")
    raw_nodes = graph.get("nodes") if isinstance(graph, dict) else {}
    nodes = (
        {
            node_id: {
                field: node[field]
                for field in ("kind", "title", "label")
                if isinstance(node.get(field), str)
            }
            for node_id, node in raw_nodes.items()
            if isinstance(node_id, str) and isinstance(node, dict)
        }
        if isinstance(raw_nodes, dict)
        else {}
    )
    return {
        field: definition[field]
        for field in ("id", "title")
        if isinstance(definition.get(field), str)
    } | {"graph": {"nodes": nodes}}


class BackendDocumentStore(Protocol):
    """Minimal document-store API used by backend routes."""

    def get_document(
        self, prefix: str, key: str, default: Any | None = None
    ) -> StoredDocument | None: ...

    def list_documents(self, prefix: str, key_prefix: str) -> list[tuple[str, StoredDocument]]: ...

    def query_workflow_run_page(
        self,
        *,
        status: str,
        q: str,
        sort: Literal["newest", "oldest", "duration", "shortest"],
        cursor: tuple[int | None, str | None, str, str] | None,
        since: str | None = None,
        as_of: str,
        limit: int,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Filter and keyset-page compact workflow metadata in the global run order."""
        ...

    def search_documents(
        self,
        prefix: str,
        key_prefix: str,
        fields: tuple[str, ...],
        source: Literal["json", "front_matter"],
        q: str,
        limit: int = 5,
        key_suffix: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def save_document(self, prefix: str, key: str, payload: Any) -> None: ...

    def merge_document(self, prefix: str, key: str, payload: Any) -> bool: ...

    def delete_document(self, prefix: str, key: str) -> None: ...

    def pop_document(self, prefix: str, key: str) -> Any | None: ...

    def mutate_document(self, prefix: str, key: str, mutator: Callable[[Any], Any]) -> Any: ...

    def read_document_events(
        self, after: DocumentEventPosition, limit: int
    ) -> list[DocumentEvent]: ...

    def has_deferred_document_events(self, after: DocumentEventPosition) -> bool:
        """True when committed events after ``after`` are not yet readable.

        They wait behind a still-open older transaction; once it ends they
        become readable without any new event (or NOTIFY) announcing it.
        """
        ...

    def latest_document_event_position(self) -> DocumentEventPosition: ...

    def get_document_event_cursor(self, consumer: str) -> DocumentEventPosition | None: ...

    def save_document_event_cursor(
        self, consumer: str, position: DocumentEventPosition
    ) -> None: ...

    def prune_document_events(
        self, *, through: DocumentEventPosition, older_than: timedelta
    ) -> int: ...
    def query_logs(
        self,
        *,
        level: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...

    def query_tool_audits(self, tool_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Return tool audit rows keyed by call ID in one indexed query."""
        ...

    def get_log_sources(self) -> list[dict[str, Any]]: ...

    def inspect_schema(self) -> list[dict[str, Any]]: ...

    def list_checkpoint_thread_snapshots(
        self, *, limit: int = 50
    ) -> list[CheckpointThreadSnapshot]: ...

    def list_checkpoint_message_blobs(
        self, *, before: datetime, limit: int | None = None
    ) -> tuple[list[CheckpointMessageBlob], bool]:
        """Read durable ``messages`` snapshots and deltas before ``before``."""
        ...

    def get_checkpoint_thread_snapshot(
        self,
        thread_id: str,
        *,
        checkpoint_id: str | None = None,
    ) -> CheckpointThreadSnapshot | None: ...

    def list_routine_runs(
        self, routine_id: str, *, limit: int = 50, before: str | None = None
    ) -> tuple[list[RoutineRunSummary], bool]: ...

    def list_routine_revisions(
        self, routine_id: str, *, limit: int = 50
    ) -> list[RoutineRevisionSummary]: ...

    def get_routine_revision(self, routine_id: str, revision_id: str) -> dict[str, Any] | None: ...

    def list_knowledge_page_revisions(
        self, base_id: str, path: str, *, limit: int = 50
    ) -> list[KnowledgePageRevisionSummary]: ...

    def get_knowledge_page_revision(
        self, base_id: str, path: str, revision_id: str
    ) -> str | None: ...

    def list_instruction_revisions(
        self, path: str, *, limit: int = 50
    ) -> list[InstructionRevisionSummary]: ...

    def get_instruction_revision(self, path: str, revision_id: str) -> str | None: ...
    def list_canvas_revisions(self, name: str, *, limit: int = 100) -> list[CanvasRevision]: ...
    def close(self) -> None: ...


def _is_session_noise(prefix: str, key: str, before: Any, after: Any) -> bool:
    """A session update touching only ``content``/``last_activity`` (every turn).

    Mirrors the Postgres capture trigger's noise filter: such updates log no event.
    """
    if prefix != PROGENA_STATE_PREFIX or not key.startswith(SESSION_KEY_PREFIX):
        return False
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    noise = ("content", "last_activity")
    return {k: v for k, v in before.items() if k not in noise} == {
        k: v for k, v in after.items() if k not in noise
    }


class InMemoryDocumentStore:
    """Small test/local implementation with the same payload shape as Postgres."""

    def __init__(self) -> None:
        self._documents: dict[tuple[str, str], StoredDocument] = {}
        self._checkpoint_threads: dict[str, CheckpointThreadSnapshot] = {}
        self._routine_revisions: dict[
            tuple[str, str], tuple[RoutineRevisionSummary, dict[str, Any]]
        ] = {}
        self._events: list[DocumentEvent] = []
        # Allocation counter, independent of retained rows so pruning never
        # makes a later event reuse a processed sequence number.
        self._last_event_seq = 0
        self._event_cursors: dict[str, DocumentEventPosition] = {}
        self._event_listeners: list[Callable[[int], None]] = []
        # Serializes every write with its event publication, and event reads
        # with both, so a reader never sees a sequence number past one that is
        # allocated but not yet appended. Reentrant: mutators may read.
        self._lock = threading.RLock()
        self._knowledge_revisions: dict[
            tuple[str, str, str], tuple[KnowledgePageRevisionSummary, str]
        ] = {}
        self._instruction_revisions: dict[
            tuple[str, str], tuple[InstructionRevisionSummary, str]
        ] = {}
        self._canvas_revisions: list[CanvasRevision] = []
        self._last_canvas_revision_id = 0

    def get_document(
        self, prefix: str, key: str, default: Any | None = None
    ) -> StoredDocument | None:
        document = self._documents.get((prefix, key))
        if document is not None:
            return StoredDocument(copy.deepcopy(document.payload), document.updated_at)
        if default is None:
            return None
        return StoredDocument(default, None)

    def list_documents(self, prefix: str, key_prefix: str) -> list[tuple[str, StoredDocument]]:
        return sorted(
            (
                (key, StoredDocument(copy.deepcopy(document.payload), document.updated_at))
                for (doc_prefix, key), document in self._documents.items()
                if doc_prefix == prefix and key.startswith(key_prefix)
            ),
            key=lambda entry: entry[0],
        )

    def search_documents(
        self,
        prefix: str,
        key_prefix: str,
        fields: tuple[str, ...],
        source: Literal["json", "front_matter"],
        q: str,
        limit: int = 5,
        key_suffix: str | None = None,
    ) -> list[dict[str, Any]]:
        q_lower = q.lower()
        rows: list[tuple[str, StoredDocument]] = []
        for (doc_prefix, key), document in self._documents.items():
            if doc_prefix != prefix or not key.startswith(key_prefix):
                continue
            if key_suffix is not None and not key.endswith(key_suffix):
                continue
            payload = document.payload if isinstance(document.payload, dict) else {}
            if source == "front_matter":
                content = str(payload.get("content", ""))
                if q_lower in content.lower():
                    rows.append((key, document))
            else:
                for field in fields:
                    value = str(payload.get(field, ""))
                    if q_lower in value.lower():
                        rows.append((key, document))
                        break

        # Same order as Postgres: JSON kinds put title-prefix matches first, then
        # newest; front-matter kinds are newest first.
        rows.sort(
            key=lambda entry: entry[1].updated_at or datetime.min.replace(tzinfo=UTC), reverse=True
        )
        if source == "json" and fields:
            rows.sort(
                key=lambda entry: (
                    0
                    if str(
                        (entry[1].payload if isinstance(entry[1].payload, dict) else {}).get(
                            fields[0], ""
                        )
                    )
                    .lower()
                    .startswith(q_lower)
                    else 1
                )
            )
        return [
            {"key": key, "payload": document.payload, "updated_at": document.updated_at}
            for key, document in rows[:limit]
        ]

    def query_workflow_run_page(
        self,
        *,
        status: str,
        q: str,
        sort: Literal["newest", "oldest", "duration", "shortest"],
        cursor: tuple[int | None, str | None, str, str] | None,
        since: str | None = None,
        as_of: str,
        limit: int,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        cutoff = (
            datetime.fromisoformat(since.replace("Z", "+00:00")).astimezone(UTC) if since else None
        )
        as_of_time = datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(UTC)

        def duration_us(started: datetime | None, completed_value: object) -> int | None:
            if started is None:
                return None
            try:
                completed = (
                    datetime.fromisoformat(completed_value.replace("Z", "+00:00")).astimezone(UTC)
                    if isinstance(completed_value, str)
                    else as_of_time
                )
            except ValueError:
                completed = as_of_time
            elapsed = max(completed - started, timedelta())
            return (
                elapsed.days * 86_400_000_000 + elapsed.seconds * 1_000_000 + elapsed.microseconds
            )

        def order_key(
            started: datetime | None, elapsed_us: int | None, row_kind: str, run_id: str
        ) -> tuple[Any, ...]:
            timestamp = started.isoformat(timespec="microseconds") if started is not None else ""
            if sort == "oldest":
                return (started is None, timestamp, row_kind, run_id)
            if sort == "duration":
                return (
                    elapsed_us is not None,
                    elapsed_us if elapsed_us is not None else -1,
                    started is not None,
                    timestamp,
                    row_kind,
                    run_id,
                )
            if sort == "shortest":
                return (
                    elapsed_us is None,
                    elapsed_us if elapsed_us is not None else 0,
                    started is None,
                    timestamp,
                    row_kind,
                    run_id,
                )
            return (started is not None, timestamp, row_kind, run_id)

        boundary = None
        if cursor is not None:
            boundary_duration, boundary_started, boundary_kind, boundary_run_id = cursor
            boundary_time = (
                datetime.fromisoformat(boundary_started.replace("Z", "+00:00")).astimezone(UTC)
                if boundary_started is not None
                else None
            )
            boundary = order_key(boundary_time, boundary_duration, boundary_kind, boundary_run_id)

        rows = []
        for _, document in self.list_documents(PROGENA_STATE_PREFIX, "workflow_run:"):
            payload = document.payload
            if not isinstance(payload, dict):
                continue
            started_value = payload.get("started_at") or payload.get("queued_at")
            try:
                started = (
                    datetime.fromisoformat(started_value.replace("Z", "+00:00")).astimezone(UTC)
                    if isinstance(started_value, str)
                    else None
                )
            except ValueError:
                continue
            if cutoff is not None and (started is None or started < cutoff):
                continue
            workflow_id = payload.get("workflow_id")
            run_id = payload.get("run_id")
            if not isinstance(workflow_id, str) or not isinstance(run_id, str) or not run_id:
                continue
            definition_doc = self.get_document(PROGENA_STATE_PREFIX, f"workflow:{workflow_id}", {})
            definition = definition_doc.payload if definition_doc is not None else {}
            definition = definition if isinstance(definition, dict) else {}
            name = definition.get("title") or workflow_id
            source = payload.get("source") or "manual"
            if status and str(payload.get("status", "")).casefold() != status:
                continue
            if q and not any(q in str(value).casefold() for value in (name, source, run_id)):
                continue
            elapsed_us = duration_us(started, payload.get("completed_at"))
            key = order_key(started, elapsed_us, "workflow", run_id)
            if boundary is not None and (
                key <= boundary if sort in {"oldest", "shortest"} else key >= boundary
            ):
                continue
            step_audit = payload.get("step_audit")
            compact_audit = (
                [
                    {
                        field: entry.get(field)
                        for field in (
                            "node_id",
                            "kind",
                            "started_at",
                            "completed_at",
                            "status",
                            "tokens",
                        )
                        if field in entry
                    }
                    for entry in step_audit
                    if isinstance(entry, dict)
                ]
                if isinstance(step_audit, list)
                else []
            )
            outputs = payload.get("node_outputs")
            node_threads = (
                {
                    node_id: {"thread_id": output["thread_id"]}
                    for node_id, output in outputs.items()
                    if isinstance(output, dict) and isinstance(output.get("thread_id"), str)
                }
                if isinstance(outputs, dict)
                else {}
            )
            pending = payload.get("pending_routine")
            pending_ref = (
                {
                    field: pending.get(field)
                    for field in ("node_id", "thread_id")
                    if field in pending
                }
                if isinstance(pending, dict)
                else None
            )
            rows.append(
                (
                    key,
                    {
                        field: payload.get(field)
                        for field in (
                            "run_id",
                            "workflow_id",
                            "status",
                            "source",
                            "started_at",
                            "queued_at",
                            "completed_at",
                            "completed_node_ids",
                            "tokens",
                        )
                    }
                    | {
                        "step_audit": compact_audit,
                        "node_threads": node_threads,
                        "pending_routine": pending_ref,
                    },
                    _compact_workflow_run_definition(definition),
                )
            )
        rows.sort(key=lambda row: row[0], reverse=sort not in {"oldest", "shortest"})
        return [(payload, definition) for _, payload, definition in rows[:limit]]

    def save_document(self, prefix: str, key: str, payload: Any) -> None:
        # Copy like Postgres serialization does, so later in-place edits by the
        # caller cannot rewrite the stored value (or an event's before/after).
        stored = copy.deepcopy(payload)
        with self._lock:
            previous = self._documents.get((prefix, key))
            changed = previous is None or previous.payload != stored
            self._documents[(prefix, key)] = StoredDocument(stored, datetime.now(UTC))
            if previous is None:
                self._record_event(prefix, key, "created", None, stored)
            elif changed:
                self._record_event(prefix, key, "updated", previous.payload, stored)
            if changed:
                self._capture_canvas_revision(prefix, key, "write", stored)
            if prefix == PROGENA_STATE_PREFIX and key.startswith("knowledge:"):
                remainder = key[len("knowledge:") :]
                base_id, separator, path = remainder.partition("/")
                if (
                    separator
                    and isinstance(stored, dict)
                    and isinstance(stored.get("content"), str)
                ):
                    content = stored["content"]
                    revision_id = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    summary = KnowledgePageRevisionSummary(
                        revision_id=revision_id,
                        base_id=base_id,
                        path=path,
                        captured_at=datetime.now(UTC).isoformat(),
                    )
                    self._knowledge_revisions.setdefault(
                        (base_id, path, revision_id), (summary, content)
                    )
            if (
                prefix == PROGENA_STATE_PREFIX
                and key.startswith(INSTRUCTION_DOCUMENT_KEY_PREFIX)
                and isinstance(stored, dict)
                and isinstance(stored.get("content"), str)
            ):
                path = key[len(INSTRUCTION_DOCUMENT_KEY_PREFIX) :]
                content = stored["content"]
                revision_id = hashlib.sha256(content.encode("utf-8")).hexdigest()
                self._instruction_revisions.setdefault(
                    (path, revision_id),
                    (
                        InstructionRevisionSummary(
                            revision_id=revision_id,
                            path=path,
                            captured_at=datetime.now(UTC).isoformat(),
                        ),
                        content,
                    ),
                )

    def _capture_canvas_revision(
        self, prefix: str, key: str, op: Literal["write", "delete"], payload: Any
    ) -> None:
        if prefix != PROGENA_STATE_PREFIX or not key.startswith(CANVAS_DOCUMENT_KEY_PREFIX):
            return
        remainder = key.removeprefix(CANVAS_DOCUMENT_KEY_PREFIX)
        name, separator, path = remainder.partition("/")
        content = payload.get("content") if isinstance(payload, dict) else None
        if not name or not separator or not path or not isinstance(content, str):
            return
        self._last_canvas_revision_id += 1
        self._canvas_revisions.append(
            CanvasRevision(
                id=self._last_canvas_revision_id,
                name=name,
                path=path,
                op=op,
                content=content if op == "write" else None,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                captured_at=datetime.now(UTC).isoformat(),
            )
        )

    def list_canvas_revisions(self, name: str, *, limit: int = 100) -> list[CanvasRevision]:
        rows = [revision for revision in self._canvas_revisions if revision.name == name]
        return sorted(rows, key=lambda revision: revision.id, reverse=True)[:limit]

    def list_instruction_revisions(
        self, path: str, *, limit: int = 50
    ) -> list[InstructionRevisionSummary]:
        rows = [
            summary
            for (row_path, _), (summary, _) in self._instruction_revisions.items()
            if row_path == path
        ]
        return sorted(
            rows,
            key=lambda summary: (summary.captured_at or "", summary.revision_id),
            reverse=True,
        )[:limit]

    def get_instruction_revision(self, path: str, revision_id: str) -> str | None:
        row = self._instruction_revisions.get((path, revision_id))
        return row[1] if row else None

    def list_knowledge_page_revisions(
        self, base_id: str, path: str, *, limit: int = 50
    ) -> list[KnowledgePageRevisionSummary]:
        rows = [
            summary
            for (row_base, row_path, _), (summary, _) in self._knowledge_revisions.items()
            if row_base == base_id and row_path == path
        ]
        return sorted(
            rows,
            key=lambda summary: (summary.captured_at or "", summary.revision_id),
            reverse=True,
        )[:limit]

    def get_knowledge_page_revision(self, base_id: str, path: str, revision_id: str) -> str | None:
        row = self._knowledge_revisions.get((base_id, path, revision_id))
        return row[1] if row else None

    def delete_document(self, prefix: str, key: str) -> None:
        with self._lock:
            previous = self._documents.pop((prefix, key), None)
            if previous is not None:
                self._capture_canvas_revision(prefix, key, "delete", previous.payload)
                self._record_event(prefix, key, "deleted", previous.payload, None)

    def pop_document(self, prefix: str, key: str) -> Any | None:
        """Atomically remove a document and return its previous payload."""

        with self._lock:
            previous = self._documents.pop((prefix, key), None)
            if previous is None:
                return None
            self._capture_canvas_revision(prefix, key, "delete", previous.payload)
            self._record_event(prefix, key, "deleted", previous.payload, None)
            return copy.deepcopy(previous.payload)

    def mutate_document(self, prefix: str, key: str, mutator: Callable[[Any], Any]) -> Any:
        with self._lock:
            document = self._documents.get((prefix, key))
            payload = copy.deepcopy(document.payload) if document is not None else None
            new_payload = mutator(payload)
            stored = copy.deepcopy(new_payload)
            previous = self._documents.get((prefix, key))
            changed = previous is None or previous.payload != stored
            self._documents[(prefix, key)] = StoredDocument(stored, datetime.now(UTC))
            if previous is None:
                self._record_event(prefix, key, "created", None, stored)
            elif changed:
                self._record_event(prefix, key, "updated", previous.payload, stored)
            if changed:
                self._capture_canvas_revision(prefix, key, "write", stored)
            return new_payload

    def merge_document(self, prefix: str, key: str, payload: Any) -> bool:
        with self._lock:
            previous = self._documents.get((prefix, key))
            if previous is None or not isinstance(previous.payload, dict):
                return False
            stored = copy.deepcopy({**previous.payload, **payload})
            self._documents[(prefix, key)] = StoredDocument(stored, datetime.now(UTC))
            if previous.payload != stored:
                self._record_event(prefix, key, "updated", previous.payload, stored)
                self._capture_canvas_revision(prefix, key, "write", stored)
            return True

    def add_document_event_listener(self, listener: Callable[[int], None]) -> None:
        """Call ``listener(seq)`` after each recorded event (the in-memory NOTIFY)."""

        self._event_listeners.append(listener)

    def remove_document_event_listener(self, listener: Callable[[int], None]) -> None:
        if listener in self._event_listeners:
            self._event_listeners.remove(listener)

    def _record_event(
        self, prefix: str, key: str, op: DocumentEventOp, before: Any, after: Any
    ) -> None:
        """Append one event; the caller holds ``self._lock``."""
        if prefix not in _STORE_CHANGE_PREFIXES:
            return
        if op == "updated" and _is_session_noise(prefix, key, before, after):
            return
        self._last_event_seq += 1
        seq = self._last_event_seq
        self._events.append(
            DocumentEvent(
                seq=seq,
                # Every in-memory write commits immediately, in allocation order.
                txid=seq,
                prefix=prefix,
                key=key,
                op=op,
                before=copy.deepcopy(before),
                after=copy.deepcopy(after),
                origin=_document_write_origin.get(),
                created_at=datetime.now(UTC),
            )
        )
        for listener in list(self._event_listeners):
            listener(seq)

    def read_document_events(self, after: DocumentEventPosition, limit: int) -> list[DocumentEvent]:
        with self._lock:
            return [event for event in self._events if event.position > after][:limit]

    def has_deferred_document_events(self, after: DocumentEventPosition) -> bool:
        # In-memory writes are readable the moment they are recorded.
        return False

    def latest_document_event_position(self) -> DocumentEventPosition:
        with self._lock:
            return DocumentEventPosition(self._last_event_seq, self._last_event_seq)

    def get_document_event_cursor(self, consumer: str) -> DocumentEventPosition | None:
        return self._event_cursors.get(consumer)

    def save_document_event_cursor(self, consumer: str, position: DocumentEventPosition) -> None:
        self._event_cursors[consumer] = position

    def prune_document_events(
        self, *, through: DocumentEventPosition, older_than: timedelta
    ) -> int:
        cutoff = datetime.now(UTC) - older_than
        with self._lock:
            kept = [
                event
                for event in self._events
                if event.position > through or event.created_at >= cutoff
            ]
            removed = len(self._events) - len(kept)
            self._events = kept
        return removed

    def query_logs(
        self,
        *,
        level: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        source_prefix = f"{PROGENA_LOG_PREFIX}."
        for (prefix, key), document in self._documents.items():
            if not prefix.startswith(source_prefix):
                continue
            row = dict(document.payload) if isinstance(document.payload, dict) else {}
            row.setdefault("id", key)
            row.setdefault("source", prefix.removeprefix(source_prefix))
            if level and str(row.get("level", "")).lower() != level.lower():
                continue
            if source and str(row.get("source", "")) != source:
                continue
            rows.append(row)
        rows.sort(key=lambda row: str(row.get("timestamp") or row.get("time") or ""))
        return rows[-limit:] if limit > 0 else rows

    def query_tool_audits(self, tool_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        wanted = set(tool_ids)
        if not wanted:
            return {}
        audits: dict[str, dict[str, Any]] = {}
        for row in self.query_logs(source="tool_calls", limit=0):
            payload = row.get("payload")
            if not isinstance(payload, dict):
                payload = row
            tool_id = payload.get("tool_id") if isinstance(payload, dict) else None
            if isinstance(tool_id, str) and tool_id in wanted:
                audits[tool_id] = payload
        return audits

    def get_log_sources(self) -> list[dict[str, Any]]:
        sources = sorted(
            {
                prefix.removeprefix(f"{PROGENA_LOG_PREFIX}.")
                for prefix, _key in self._documents
                if prefix.startswith(f"{PROGENA_LOG_PREFIX}.")
            }
        )
        return [{"source": source, "count": 0} for source in sources]

    def inspect_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "table": "store",
                "columns": [
                    {"name": "prefix", "type": "TEXT", "nullable": False},
                    {"name": "key", "type": "TEXT", "nullable": False},
                    {"name": "value", "type": "JSONB", "nullable": False},
                    {"name": "created_at", "type": "TIMESTAMPTZ", "nullable": False},
                    {"name": "updated_at", "type": "TIMESTAMPTZ", "nullable": False},
                    {"name": "expires_at", "type": "TIMESTAMPTZ", "nullable": True},
                    {"name": "ttl_minutes", "type": "DOUBLE PRECISION", "nullable": True},
                ],
                "source": "in_memory",
            }
        ]

    def list_checkpoint_thread_snapshots(
        self, *, limit: int = 50
    ) -> list[CheckpointThreadSnapshot]:
        snapshots = sorted(
            self._checkpoint_threads.values(),
            key=lambda snapshot: snapshot.checkpoint_ts or "",
            reverse=True,
        )
        return snapshots[:limit] if limit > 0 else snapshots

    def list_checkpoint_message_blobs(
        self, *, before: datetime, limit: int | None = None
    ) -> tuple[list[CheckpointMessageBlob], bool]:
        """In-memory stores do not synthesize LangGraph serialization."""
        return [], False

    def get_checkpoint_thread_snapshot(
        self,
        thread_id: str,
        *,
        checkpoint_id: str | None = None,
    ) -> CheckpointThreadSnapshot | None:
        snapshot = self._checkpoint_threads.get(thread_id)
        if snapshot is None:
            return None
        if checkpoint_id and snapshot.checkpoint_id != checkpoint_id:
            return None
        return snapshot

    def list_routine_runs(
        self, routine_id: str, *, limit: int = 50, before: str | None = None
    ) -> tuple[list[RoutineRunSummary], bool]:
        prefix = f"routine:{routine_id}:"
        summaries = [
            RoutineRunSummary(
                thread_id=snapshot.thread_id,
                routine_id=routine_id,
                revision_id=(snapshot.thread_id.split(":", 3) + [None, None])[2],
                run_id=(snapshot.thread_id.split(":", 3) + [None, None, None])[3],
                started_at=snapshot.checkpoint_ts,
                last_checkpoint_at=snapshot.checkpoint_ts,
                checkpoint_count=1,
                failed=snapshot.failed,
            )
            for snapshot in self._checkpoint_threads.values()
            if snapshot.thread_id.startswith(prefix)
        ]
        summaries.sort(key=lambda summary: summary.last_checkpoint_at or "", reverse=True)
        if before:
            summaries = [s for s in summaries if (s.last_checkpoint_at or "") < before]
        if limit <= 0:
            return summaries, False
        page = summaries[:limit]
        return page, len(summaries) > limit

    def list_routine_revisions(
        self, routine_id: str, *, limit: int = 50
    ) -> list[RoutineRevisionSummary]:
        summaries = [
            summary
            for (rid, _revision_id), (summary, _graph) in self._routine_revisions.items()
            if rid == routine_id
        ]
        summaries.sort(key=lambda summary: summary.captured_at or "", reverse=True)
        return summaries[:limit] if limit > 0 else summaries

    def get_routine_revision(self, routine_id: str, revision_id: str) -> dict[str, Any] | None:
        entry = self._routine_revisions.get((routine_id, revision_id))
        return entry[1] if entry is not None else None

    def upsert_routine_revision(
        self,
        routine_id: str,
        revision_id: str,
        graph: dict[str, Any],
        *,
        captured_at: str | None = None,
    ) -> None:
        self._routine_revisions[(routine_id, revision_id)] = (
            RoutineRevisionSummary(
                revision_id=revision_id, routine_id=routine_id, captured_at=captured_at
            ),
            graph,
        )

    def upsert_checkpoint_thread_snapshot(self, snapshot: CheckpointThreadSnapshot) -> None:
        self._checkpoint_threads[snapshot.thread_id] = snapshot

    def close(self) -> None:
        return None


class PostgresDocumentStore:
    """Synchronous Postgres client for the LangGraph ``store`` table."""

    def __init__(self, database_url: str, *, runtime_url: str | None = None) -> None:
        self._database_url = database_url
        self._pool = self._create_pool()
        self._conversation_reader = RuntimeConversationReader(runtime_url) if runtime_url else None
        self._runs_trigger_ready = False

    @property
    def database_url(self) -> str:
        return self._database_url

    def _create_pool(self) -> ConnectionPool[psycopg.Connection[dict[str, Any]]]:
        return ConnectionPool(
            conninfo=self._database_url,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=False,
        )

    def open(self) -> None:
        self._pool.open()
        # Concurrent startups (backend workers, seed scripts) otherwise race on
        # the same CREATE OR REPLACE FUNCTION/TRIGGER catalog rows. A session
        # lock on one pooled connection serializes setup; the steps below use
        # other pooled connections.
        with self._pool.connection() as conn:
            conn.execute("SELECT pg_advisory_lock(hashtext('progena_document_store_setup'))")
            try:
                self._ensure_store_table()
                self._ensure_change_trigger()
                self._ensure_document_event_log()
                self._ensure_routine_revision_history()
                self._ensure_inbox_revision_history()
                self._ensure_policy_revision_history()
                self._ensure_knowledge_page_revision_history()
                self._ensure_instruction_revision_history()
                self._ensure_canvas_revision_history()
            finally:
                conn.execute("SELECT pg_advisory_unlock(hashtext('progena_document_store_setup'))")

    def close(self) -> None:
        self._pool.close()
        if self._conversation_reader is not None:
            self._conversation_reader.close()

    def ensure_runs_checkpoint_trigger(self) -> None:
        """Install backend-owned checkpoint wakeups after LangGraph tables exist.

        Checkpoint readers also call this if the runtime schema was not ready
        at backend startup. Once installed, subsequent reads perform no DDL.
        """
        if self._runs_trigger_ready:
            return
        with self._cursor() as cur:
            cur.execute("SELECT to_regclass('public.checkpoints') AS checkpoints")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("Checkpoint schema lookup returned no row")
            if row["checkpoints"] is None:
                return
            cur.execute(
                """
                SELECT pg_advisory_xact_lock(hashtextextended('runs_checkpoint_schema', 0));
                CREATE OR REPLACE FUNCTION progena_notify_run_checkpoint()
                RETURNS trigger AS $$
                BEGIN
                    PERFORM pg_notify(
                        'run_checkpoints',
                        json_build_object('thread_id', NEW.thread_id)::text
                    );
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                DROP TRIGGER IF EXISTS progena_notify_run_checkpoint ON checkpoints;
                CREATE TRIGGER progena_notify_run_checkpoint
                    AFTER INSERT OR UPDATE ON checkpoints
                    FOR EACH ROW EXECUTE FUNCTION progena_notify_run_checkpoint();
                CREATE INDEX IF NOT EXISTS progena_tool_logs_tool_id_idx
                    ON store ((value->'payload'->>'tool_id'))
                    WHERE prefix = 'progena.logs.tool_calls';
                """
            )
        self._runs_trigger_ready = True

    def _recreate_pool(self) -> None:
        self._pool = self._create_pool()
        self._pool.open()

    def _checkpoint_messages(self, *, thread_id: str, checkpoint_id: str | None) -> list[Any]:
        """Messages of one thread checkpoint, read through the runtime.

        Raises ``RuntimeUnavailable`` when the runtime cannot be reached and
        no earlier copy of the thread is known.
        """
        if self._conversation_reader is None:
            return []
        return self._conversation_reader.get_messages(thread_id, checkpoint_id)

    @contextmanager
    def _cursor(self):
        """Yield a cursor, reopening the pool once if it was unexpectedly closed."""
        try:
            with self._pool.connection() as conn, conn.cursor() as cur:
                yield cur
                return
        except PoolClosed:
            logger.warning("Backend document store pool was closed; recreating it")
            self._recreate_pool()
            self._ensure_store_table()
            with self._pool.connection() as conn, conn.cursor() as cur:
                yield cur

    def _ensure_store_table(self) -> None:
        """Create the LangGraph-compatible store table when running in a fresh DB."""
        with self._cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS store (
                    prefix TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    expires_at TIMESTAMPTZ,
                    ttl_minutes DOUBLE PRECISION,
                    PRIMARY KEY (prefix, key)
                )
                """
            )

    def _ensure_change_trigger(self) -> None:
        """Install a NOTIFY trigger so state writes push to listeners without polling.

        Fires for both backend writes and the runtime's LangGraph store writes
        (same ``store`` table), including deletes (e.g. the agent removing a
        task from ``/config/schedule.json`` in the separate runtime process,
        which has no other way to tell the backend a row disappeared). Scoped
        to state-namespace prefixes so the noisy append-only
        ``progena.logs.*`` rows do not flood the channel. ``CREATE OR
        REPLACE`` + ``DROP ... IF EXISTS`` keep this idempotent.
        """
        prefixes = ", ".join(f"'{prefix}'" for prefix in _STORE_CHANGE_PREFIXES)
        with self._cursor() as cur:
            cur.execute(
                f"""
                CREATE OR REPLACE FUNCTION progena_notify_store_change()
                RETURNS trigger AS $$
                DECLARE
                    row_prefix TEXT := COALESCE(NEW.prefix, OLD.prefix);
                    row_key TEXT := COALESCE(NEW.key, OLD.key);
                    row_action TEXT := CASE WHEN TG_OP = 'DELETE' THEN 'deleted' ELSE 'upserted' END;
                BEGIN
                    IF row_prefix IN ({prefixes}) THEN
                        PERFORM pg_notify(
                            '{STORE_CHANGE_CHANNEL}',
                            json_build_object('prefix', row_prefix, 'key', row_key, 'action', row_action)::text
                        );
                    END IF;
                    RETURN COALESCE(NEW, OLD);
                END;
                $$ LANGUAGE plpgsql;
                """
            )
            cur.execute(
                """
                CREATE OR REPLACE TRIGGER progena_store_change
                AFTER INSERT OR UPDATE OR DELETE ON store
                FOR EACH ROW EXECUTE FUNCTION progena_notify_store_change()
                """
            )

    def _ensure_document_event_log(self) -> None:
        """Install the durable document event log and its capture trigger.

        Every INSERT/UPDATE/DELETE of a state-namespace ``store`` row appends one
        ``document_events`` row carrying ``created``/``updated``/``deleted``, the
        before/after payloads, and the writer origin (``progena.origin``,
        set transaction-locally by ``document_write_origin``). Rewrites of an
        identical value are skipped. The row's sequence number is NOTIFYed on
        ``document_events`` so consumers wake up, then catch up from their
        stored cursor -- missed notifications never lose events. Like the
        revision histories, this lives in separate tables so LangGraph's own
        ``store`` schema is never altered.
        """
        prefixes = ", ".join(f"'{prefix}'" for prefix in _STORE_CHANGE_PREFIXES)
        with self._cursor() as cur, cur.connection.transaction():
            # ``txid`` is the writing transaction (commit-order key, see
            # ``DocumentEventPosition``). Added columns on existing tables are
            # filled in this one transaction, so every pre-existing event and
            # cursor shares one txid and keeps its relative ``seq`` order.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS document_events (
                    seq BIGSERIAL PRIMARY KEY,
                    txid XID8 NOT NULL DEFAULT pg_current_xact_id(),
                    prefix TEXT NOT NULL,
                    key TEXT NOT NULL,
                    op TEXT NOT NULL CHECK (op IN ('created', 'updated', 'deleted')),
                    before JSONB,
                    after JSONB,
                    origin TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                "ALTER TABLE document_events ADD COLUMN IF NOT EXISTS "
                "txid XID8 NOT NULL DEFAULT pg_current_xact_id()"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS document_events_created_at_idx "
                "ON document_events (created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS document_events_position_idx "
                "ON document_events (txid, seq)"
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS document_event_cursors (
                    consumer TEXT PRIMARY KEY,
                    txid XID8 NOT NULL,
                    seq BIGINT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                "ALTER TABLE document_event_cursors ADD COLUMN IF NOT EXISTS "
                "txid XID8 NOT NULL DEFAULT pg_current_xact_id()"
            )
            cur.execute("ALTER TABLE document_event_cursors ALTER COLUMN txid DROP DEFAULT")
            # Placeholders are filled from module constants only, never input.
            cur.execute(
                """
                CREATE OR REPLACE FUNCTION progena_record_document_event()
                RETURNS trigger AS $$
                DECLARE
                    row_prefix TEXT := COALESCE(NEW.prefix, OLD.prefix);
                    event_seq BIGINT;
                BEGIN
                    IF row_prefix NOT IN (__PREFIXES__) THEN
                        RETURN NULL;
                    END IF;
                    IF TG_OP = 'UPDATE' AND OLD.value IS NOT DISTINCT FROM NEW.value THEN
                        RETURN NULL;
                    END IF;
                    -- Noise filter: content/last_activity-only updates on session docs
                    -- do not log an event, because they happen on every turn.
                    IF TG_OP = 'UPDATE'
                        AND row_prefix = 'progena.state'
                        AND COALESCE(NEW.key, OLD.key) LIKE '__SESSION_PREFIX__%'
                        AND (OLD.value -> 'payload') - 'content' - 'last_activity'
                            IS NOT DISTINCT FROM
                            (NEW.value -> 'payload') - 'content' - 'last_activity'
                    THEN
                        RETURN NULL;
                    END IF;
                    INSERT INTO document_events (prefix, key, op, before, after, origin)
                    VALUES (
                        row_prefix,
                        COALESCE(NEW.key, OLD.key),
                        CASE TG_OP WHEN 'INSERT' THEN 'created'
                                   WHEN 'UPDATE' THEN 'updated'
                                   ELSE 'deleted' END,
                        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.value -> 'payload' END,
                        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.value -> 'payload' END,
                        NULLIF(current_setting('progena.origin', true), '')
                    )
                    RETURNING seq INTO event_seq;
                    PERFORM pg_notify('__CHANNEL__', event_seq::text);
                    RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;
                """.replace("__PREFIXES__", prefixes)  # nosec B608
                .replace("__CHANNEL__", DOCUMENT_EVENT_CHANNEL)
                .replace("__SESSION_PREFIX__", SESSION_KEY_PREFIX)
            )
            cur.execute(
                """
                CREATE OR REPLACE TRIGGER progena_document_event
                AFTER INSERT OR UPDATE OR DELETE ON store
                FOR EACH ROW EXECUTE FUNCTION progena_record_document_event()
                """
            )

    def _ensure_routine_revision_history(self) -> None:
        """Capture immutable routine graph revisions via a Postgres trigger.

        The ``store`` table is LangGraph's own; this never alters its schema.
        A row trigger on ``store`` inserts into a separate
        ``routine_graph_revisions`` table whenever a routine document is
        written, so a chat thread that pinned a revision keeps resolving the
        graph it started on even after the routine is edited or deleted.
        DELETE captures OLD in the same transaction, preserving the final live
        snapshot even when it predates installation of the capture trigger.
        """
        state_prefix = PROGENA_STATE_PREFIX
        key_prefix = ROUTINE_GRAPH_KEY_PREFIX
        with self._cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS routine_graph_revisions (
                    routine_id  TEXT NOT NULL,
                    graph       JSONB NOT NULL,
                    revision_id TEXT NOT NULL GENERATED ALWAYS AS (encode(sha256(jsonb_send(graph)), 'hex')) STORED,
                    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (routine_id, revision_id)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS routine_graph_revisions_latest
                    ON routine_graph_revisions (routine_id, captured_at DESC, revision_id DESC)
                """
            )
            capture_sql = """
                CREATE OR REPLACE FUNCTION progena_capture_routine_revision()
                RETURNS trigger AS $$
                DECLARE
                    graph_doc JSONB;
                    source_row store%ROWTYPE;
                BEGIN
                    IF TG_OP = 'DELETE' THEN
                        source_row := OLD;
                    ELSE
                        source_row := NEW;
                    END IF;
                    IF source_row.prefix = '__STATE_PREFIX__'
                        AND source_row.key LIKE '__KEY_PREFIX__%'
                        AND length(source_row.key) > length('__KEY_PREFIX__') THEN
                        graph_doc := source_row.value -> 'payload';
                        IF graph_doc IS NOT NULL AND jsonb_typeof(graph_doc) = 'object' THEN
                            INSERT INTO routine_graph_revisions (routine_id, graph)
                            VALUES (substring(source_row.key from length('__KEY_PREFIX__') + 1), graph_doc)
                            ON CONFLICT (routine_id, revision_id) DO NOTHING;
                        END IF;
                    END IF;
                    RETURN source_row;
                END;
                $$ LANGUAGE plpgsql;
                """.replace("__STATE_PREFIX__", state_prefix).replace("__KEY_PREFIX__", key_prefix)  # nosec B608
            cur.execute(capture_sql)
            cur.execute(
                """
                CREATE OR REPLACE TRIGGER progena_capture_routine_revision
                AFTER INSERT OR UPDATE ON store
                FOR EACH ROW EXECUTE FUNCTION progena_capture_routine_revision()
                """
            )
            cur.execute(
                """
                CREATE OR REPLACE TRIGGER progena_capture_deleted_routine_revision
                BEFORE DELETE ON store
                FOR EACH ROW EXECUTE FUNCTION progena_capture_routine_revision()
                """
            )
            cur.execute(
                """
                CREATE OR REPLACE FUNCTION progena_routine_revisions_immutable()
                RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'routine_graph_revisions rows are immutable (attempted % on %/%)',
                        TG_OP, OLD.routine_id, OLD.revision_id;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
            cur.execute(
                """
                CREATE OR REPLACE TRIGGER progena_routine_revisions_immutable
                BEFORE UPDATE ON routine_graph_revisions
                FOR EACH ROW EXECUTE FUNCTION progena_routine_revisions_immutable()
                """
            )
            backfill_sql = """
                INSERT INTO routine_graph_revisions (routine_id, graph)
                SELECT substring(key from length('__KEY_PREFIX__') + 1), value -> 'payload'
                FROM store
                WHERE prefix = '__STATE_PREFIX__'
                  AND key LIKE '__KEY_PREFIX__%'
                  AND length(key) > length('__KEY_PREFIX__')
                  AND jsonb_typeof(value -> 'payload') = 'object'
                ON CONFLICT (routine_id, revision_id) DO NOTHING
                """.replace("__STATE_PREFIX__", state_prefix).replace("__KEY_PREFIX__", key_prefix)  # nosec B608
            cur.execute(backfill_sql)

    def _ensure_instruction_revision_history(self) -> None:
        state_prefix = PROGENA_STATE_PREFIX
        key_prefix = INSTRUCTION_DOCUMENT_KEY_PREFIX
        with self._cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS instruction_revisions (
                    path TEXT NOT NULL, content TEXT NOT NULL, revision_id TEXT NOT NULL,
                    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (path, revision_id)
                );
                CREATE INDEX IF NOT EXISTS instruction_revisions_latest
                    ON instruction_revisions (path, captured_at DESC, revision_id DESC);
                CREATE OR REPLACE FUNCTION progena_capture_instruction_revision()
                RETURNS trigger AS $$
                DECLARE source_row store%ROWTYPE; body TEXT;
                BEGIN
                    IF TG_OP = 'DELETE' THEN source_row := OLD; ELSE source_row := NEW; END IF;
                    body := source_row.value -> 'payload' ->> 'content';
                    IF source_row.prefix = '__STATE_PREFIX__'
                       AND source_row.key LIKE '__KEY_PREFIX__%' AND body IS NOT NULL THEN
                        INSERT INTO instruction_revisions(path, content, revision_id)
                        VALUES (
                            substring(source_row.key from length('__KEY_PREFIX__') + 1),
                            body,
                            encode(sha256(convert_to(body, 'UTF8')), 'hex')
                        ) ON CONFLICT DO NOTHING;
                    END IF;
                    RETURN source_row;
                END;
                $$ LANGUAGE plpgsql;
                CREATE OR REPLACE TRIGGER progena_capture_instruction_revision
                    AFTER INSERT OR UPDATE ON store FOR EACH ROW
                    EXECUTE FUNCTION progena_capture_instruction_revision();
                CREATE OR REPLACE TRIGGER progena_capture_deleted_instruction_revision
                    BEFORE DELETE ON store FOR EACH ROW
                    EXECUTE FUNCTION progena_capture_instruction_revision();
                CREATE OR REPLACE FUNCTION progena_instruction_revisions_immutable()
                RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'instruction_revisions rows are immutable';
                END;
                $$ LANGUAGE plpgsql;
                CREATE OR REPLACE TRIGGER progena_instruction_revisions_immutable
                    BEFORE UPDATE OR DELETE ON instruction_revisions FOR EACH ROW
                    EXECUTE FUNCTION progena_instruction_revisions_immutable();
                INSERT INTO instruction_revisions(path, content, revision_id)
                SELECT substring(key from length('__KEY_PREFIX__') + 1),
                       value -> 'payload' ->> 'content',
                       encode(sha256(convert_to(value -> 'payload' ->> 'content', 'UTF8')), 'hex')
                FROM store
                WHERE prefix = '__STATE_PREFIX__' AND key LIKE '__KEY_PREFIX__%'
                  AND value -> 'payload' ->> 'content' IS NOT NULL
                ON CONFLICT DO NOTHING;
                """.replace("__STATE_PREFIX__", state_prefix).replace("__KEY_PREFIX__", key_prefix)  # nosec B608
            )

    def _ensure_canvas_revision_history(self) -> None:
        """Capture an immutable timeline for every canvas file."""

        state_prefix = PROGENA_STATE_PREFIX
        key_prefix = CANVAS_DOCUMENT_KEY_PREFIX
        with self._cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS canvas_revisions (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    op TEXT NOT NULL CHECK (op IN ('write', 'delete')),
                    content TEXT,
                    content_hash TEXT NOT NULL,
                    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CHECK (
                        (op = 'write' AND content IS NOT NULL)
                        OR (op = 'delete' AND content IS NULL)
                    )
                );
                CREATE INDEX IF NOT EXISTS canvas_revisions_name_latest
                    ON canvas_revisions (name, captured_at DESC, id DESC);
                CREATE OR REPLACE FUNCTION progena_capture_canvas_revision()
                RETURNS trigger AS $$
                DECLARE
                    source_row store%ROWTYPE;
                    body TEXT;
                    remainder TEXT;
                    canvas_name TEXT;
                    canvas_path TEXT;
                BEGIN
                    IF TG_OP = 'DELETE' THEN source_row := OLD; ELSE source_row := NEW; END IF;
                    IF source_row.prefix <> '__STATE_PREFIX__'
                       OR source_row.key NOT LIKE '__KEY_PREFIX__%' THEN
                        RETURN source_row;
                    END IF;
                    IF TG_OP = 'UPDATE'
                       AND OLD.value -> 'payload' ->> 'content'
                           IS NOT DISTINCT FROM NEW.value -> 'payload' ->> 'content' THEN
                        RETURN source_row;
                    END IF;
                    body := source_row.value -> 'payload' ->> 'content';
                    remainder := substring(source_row.key from length('__KEY_PREFIX__') + 1);
                    canvas_name := split_part(remainder, '/', 1);
                    canvas_path := substring(remainder from position('/' in remainder) + 1);
                    IF body IS NULL OR canvas_name = '' OR position('/' in remainder) = 0
                       OR canvas_path = '' THEN
                        RETURN source_row;
                    END IF;
                    INSERT INTO canvas_revisions(name, path, op, content, content_hash)
                    VALUES (
                        canvas_name,
                        canvas_path,
                        CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'write' END,
                        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE body END,
                        encode(sha256(convert_to(body, 'UTF8')), 'hex')
                    );
                    RETURN source_row;
                END;
                $$ LANGUAGE plpgsql;
                CREATE OR REPLACE TRIGGER progena_capture_canvas_revision
                    AFTER INSERT OR UPDATE ON store FOR EACH ROW
                    EXECUTE FUNCTION progena_capture_canvas_revision();
                CREATE OR REPLACE TRIGGER progena_capture_deleted_canvas_revision
                    BEFORE DELETE ON store FOR EACH ROW
                    EXECUTE FUNCTION progena_capture_canvas_revision();
                CREATE OR REPLACE FUNCTION progena_canvas_revisions_immutable()
                RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'canvas_revisions rows are immutable';
                END;
                $$ LANGUAGE plpgsql;
                CREATE OR REPLACE TRIGGER progena_canvas_revisions_immutable
                    BEFORE UPDATE OR DELETE ON canvas_revisions FOR EACH ROW
                    EXECUTE FUNCTION progena_canvas_revisions_immutable();
                INSERT INTO canvas_revisions(name, path, op, content, content_hash)
                SELECT
                    split_part(substring(key from length('__KEY_PREFIX__') + 1), '/', 1),
                    substring(
                        substring(key from length('__KEY_PREFIX__') + 1)
                        from position('/' in substring(key from length('__KEY_PREFIX__') + 1)) + 1
                    ),
                    'write',
                    value -> 'payload' ->> 'content',
                    encode(sha256(convert_to(value -> 'payload' ->> 'content', 'UTF8')), 'hex')
                FROM store
                WHERE prefix = '__STATE_PREFIX__'
                  AND key LIKE '__KEY_PREFIX__%'
                  AND position('/' in substring(key from length('__KEY_PREFIX__') + 1)) > 1
                  AND value -> 'payload' ->> 'content' IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM canvas_revisions)
                """.replace("__STATE_PREFIX__", state_prefix).replace("__KEY_PREFIX__", key_prefix)  # nosec B608
            )

    def _ensure_knowledge_page_revision_history(self) -> None:
        state_prefix = PROGENA_STATE_PREFIX
        with self._cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_page_revisions (
                    base_id TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (base_id, path, revision_id)
                );
                CREATE INDEX IF NOT EXISTS knowledge_page_revisions_latest
                    ON knowledge_page_revisions (base_id, path, captured_at DESC, revision_id DESC);
                CREATE OR REPLACE FUNCTION progena_capture_knowledge_page_revision()
                RETURNS trigger AS $$
                DECLARE source_row store%ROWTYPE; rest TEXT; slash INTEGER; page_content TEXT;
                BEGIN
                    IF TG_OP = 'DELETE' THEN source_row := OLD; ELSE source_row := NEW; END IF;
                    rest := substring(source_row.key from 11);
                    slash := position('/' in rest);
                    page_content := source_row.value -> 'payload' ->> 'content';
                    IF source_row.prefix = '__STATE_PREFIX__' AND source_row.key LIKE 'knowledge:%'
                       AND slash > 1 AND page_content IS NOT NULL THEN
                        INSERT INTO knowledge_page_revisions(base_id, path, content, revision_id)
                        VALUES (
                            split_part(rest, '/', 1),
                            substring(rest from slash + 1),
                            page_content,
                            encode(sha256(convert_to(page_content, 'UTF8')), 'hex')
                        ) ON CONFLICT DO NOTHING;
                    END IF;
                    RETURN source_row;
                END;
                $$ LANGUAGE plpgsql;
                CREATE OR REPLACE TRIGGER progena_capture_knowledge_page_revision
                    AFTER INSERT OR UPDATE ON store FOR EACH ROW
                    EXECUTE FUNCTION progena_capture_knowledge_page_revision();
                CREATE OR REPLACE TRIGGER progena_capture_deleted_knowledge_page_revision
                    BEFORE DELETE ON store FOR EACH ROW
                    EXECUTE FUNCTION progena_capture_knowledge_page_revision();
                CREATE OR REPLACE FUNCTION progena_knowledge_page_revisions_immutable()
                RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'knowledge_page_revisions rows are immutable';
                END;
                $$ LANGUAGE plpgsql;
                CREATE OR REPLACE TRIGGER progena_knowledge_page_revisions_immutable
                    BEFORE UPDATE OR DELETE ON knowledge_page_revisions FOR EACH ROW
                    EXECUTE FUNCTION progena_knowledge_page_revisions_immutable();
                INSERT INTO knowledge_page_revisions(base_id, path, content, revision_id)
                SELECT split_part(substring(key from 11), '/', 1),
                       substring(substring(key from 11) from position('/' in substring(key from 11)) + 1),
                       value -> 'payload' ->> 'content',
                       encode(sha256(convert_to(value -> 'payload' ->> 'content', 'UTF8')), 'hex')
                FROM store
                WHERE prefix = '__STATE_PREFIX__' AND key LIKE 'knowledge:%'
                  AND position('/' in substring(key from 11)) > 1
                  AND value -> 'payload' ->> 'content' IS NOT NULL
                ON CONFLICT DO NOTHING;
                """.replace("__STATE_PREFIX__", state_prefix)  # nosec B608
            )

    def _ensure_inbox_revision_history(self) -> None:
        """Capture full inbox payloads, including frontmatter, on every store write.

        Mirrors routine graph history: immutable, content-addressed snapshots
        captured by a store trigger, with a backfill for existing messages.
        The delete trigger preserves the last live version.
        """
        with self._cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS inbox_message_revisions (
                    message_id TEXT NOT NULL,
                    document JSONB NOT NULL,
                    revision_id TEXT GENERATED ALWAYS AS (encode(sha256(jsonb_send(document)), 'hex')) STORED,
                    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (message_id, revision_id)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS inbox_message_revisions_latest
                    ON inbox_message_revisions (message_id, captured_at DESC, revision_id DESC)
                """
            )
            cur.execute(
                sql.SQL("""
                CREATE OR REPLACE FUNCTION progena_capture_inbox_revision()
                RETURNS trigger AS $$
                DECLARE source_row store%ROWTYPE;
                BEGIN
                    IF TG_OP = 'DELETE' THEN source_row := OLD; ELSE source_row := NEW; END IF;
                    IF source_row.prefix = {state_prefix}
                        AND left(source_row.key, length({key_prefix})) = {key_prefix}
                        AND length(source_row.key) > length({key_prefix})
                        AND jsonb_typeof(source_row.value->'payload') = 'object' THEN
                        INSERT INTO inbox_message_revisions (message_id, document)
                        VALUES (substring(source_row.key from length({key_prefix}) + 1), source_row.value->'payload')
                        ON CONFLICT (message_id, revision_id) DO NOTHING;
                    END IF;
                    RETURN source_row;
                END;
                $$ LANGUAGE plpgsql;
                """).format(
                    state_prefix=sql.Literal(PROGENA_STATE_PREFIX),
                    key_prefix=sql.Literal(INBOX_MESSAGE_KEY_PREFIX),
                )
            )
            cur.execute(
                """
                CREATE OR REPLACE TRIGGER progena_capture_inbox_revision
                AFTER INSERT OR UPDATE ON store
                FOR EACH ROW EXECUTE FUNCTION progena_capture_inbox_revision()
                """
            )
            cur.execute(
                """
                CREATE OR REPLACE TRIGGER progena_capture_deleted_inbox_revision
                BEFORE DELETE ON store
                FOR EACH ROW EXECUTE FUNCTION progena_capture_inbox_revision()
                """
            )
            cur.execute(
                """
                CREATE OR REPLACE FUNCTION progena_inbox_revisions_immutable()
                RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'inbox_message_revisions rows are immutable'; END;
                $$ LANGUAGE plpgsql;
                """
            )
            cur.execute(
                """
                CREATE OR REPLACE TRIGGER progena_inbox_revisions_immutable
                BEFORE UPDATE OR DELETE ON inbox_message_revisions
                FOR EACH ROW EXECUTE FUNCTION progena_inbox_revisions_immutable()
                """
            )
            cur.execute(
                """
                INSERT INTO inbox_message_revisions (message_id, document)
                SELECT substring(key from length(%s) + 1), value->'payload'
                FROM store
                WHERE prefix = %s AND left(key, length(%s)) = %s AND length(key) > length(%s)
                    AND jsonb_typeof(value->'payload') = 'object'
                ON CONFLICT (message_id, revision_id) DO NOTHING
                """,
                (
                    INBOX_MESSAGE_KEY_PREFIX,
                    PROGENA_STATE_PREFIX,
                    INBOX_MESSAGE_KEY_PREFIX,
                    INBOX_MESSAGE_KEY_PREFIX,
                    INBOX_MESSAGE_KEY_PREFIX,
                ),
            )

    def _ensure_policy_revision_history(self) -> None:
        from src.progena.settings.policies import ExecutionPolicy

        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS policy_revisions (
                    policy_id TEXT NOT NULL, document JSONB NOT NULL,
                    revision_id TEXT GENERATED ALWAYS AS (encode(sha256(jsonb_send(document)), 'hex')) STORED,
                    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (policy_id, revision_id)
                );
                CREATE OR REPLACE FUNCTION progena_capture_policy_revision() RETURNS trigger AS $$
                DECLARE source_row store%ROWTYPE;
                BEGIN
                    IF TG_OP = 'DELETE' THEN source_row := OLD; ELSE source_row := NEW; END IF;
                    IF source_row.prefix = 'progena.state' AND source_row.key LIKE 'policy:%'
                        AND jsonb_typeof(source_row.value->'payload') = 'object' THEN
                        INSERT INTO policy_revisions(policy_id, document)
                        VALUES (substring(source_row.key from 8), source_row.value->'payload')
                        ON CONFLICT DO NOTHING;
                    END IF;
                    RETURN source_row;
                END;
                $$ LANGUAGE plpgsql;
                CREATE OR REPLACE TRIGGER progena_capture_policy_revision AFTER INSERT OR UPDATE OR DELETE ON store
                FOR EACH ROW EXECUTE FUNCTION progena_capture_policy_revision();
                CREATE OR REPLACE FUNCTION progena_policy_revisions_immutable() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'policy revisions are immutable'; END;
                $$ LANGUAGE plpgsql;
                CREATE OR REPLACE TRIGGER progena_policy_revisions_immutable BEFORE UPDATE OR DELETE ON policy_revisions
                FOR EACH ROW EXECUTE FUNCTION progena_policy_revisions_immutable();
            """)
            cur.execute(
                """INSERT INTO store(prefix,key,value,created_at,updated_at)
                VALUES (%s,%s,%s,now(),now()) ON CONFLICT(prefix,key) DO NOTHING""",
                (
                    PROGENA_STATE_PREFIX,
                    "policy:default",
                    Jsonb(
                        {
                            "payload": ExecutionPolicy(id="default", name="Default").model_dump(
                                mode="json"
                            )
                        }
                    ),
                ),
            )

    def get_document(
        self, prefix: str, key: str, default: Any | None = None
    ) -> StoredDocument | None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT value, updated_at FROM store WHERE prefix = %s AND key = %s",
                (prefix, key),
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        if row is None:
            if default is None:
                return None
            return StoredDocument(default, None)
        value = row.get("value")
        payload = value.get("payload", default) if isinstance(value, dict) else default
        return StoredDocument(payload, row.get("updated_at"))

    def list_documents(self, prefix: str, key_prefix: str) -> list[tuple[str, StoredDocument]]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT key, value, updated_at FROM store "
                "WHERE prefix = %s AND key LIKE %s ORDER BY key",
                (prefix, _escape_like(key_prefix) + "%"),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        results: list[tuple[str, StoredDocument]] = []
        for row in rows:
            value = row.get("value")
            payload = value.get("payload") if isinstance(value, dict) else None
            results.append((str(row["key"]), StoredDocument(payload, row.get("updated_at"))))
        return results

    def query_workflow_run_page(
        self,
        *,
        status: str,
        q: str,
        sort: Literal["newest", "oldest", "duration", "shortest"],
        cursor: tuple[int | None, str | None, str, str] | None,
        since: str | None = None,
        as_of: str,
        limit: int,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        # Do not return trigger_event/node_outputs: these can be megabytes per run.
        boundary_sql = ""
        boundary_params: list[Any] = []
        if sort == "newest":
            order_by = "started IS NULL ASC, started DESC, p->>'run_id' DESC"
            if cursor is not None:
                _, started_at, row_kind, run_id = cursor
                if started_at is None:
                    boundary_sql = "AND started IS NULL AND ('workflow'::text, p->>'run_id') < (%s::text, %s::text)"
                    boundary_params = [row_kind, run_id]
                else:
                    boundary_sql = (
                        "AND (started IS NULL OR (started, 'workflow'::text, p->>'run_id') "
                        "< (%s::timestamptz, %s::text, %s::text))"
                    )
                    boundary_params = [started_at, row_kind, run_id]
        elif sort == "oldest":
            order_by = "started IS NULL ASC, started ASC, p->>'run_id' ASC"
            if cursor is not None:
                _, started_at, row_kind, run_id = cursor
                if started_at is None:
                    boundary_sql = "AND started IS NULL AND ('workflow'::text, p->>'run_id') > (%s::text, %s::text)"
                    boundary_params = [row_kind, run_id]
                else:
                    boundary_sql = (
                        "AND (started IS NULL OR (started, 'workflow'::text, p->>'run_id') "
                        "> (%s::timestamptz, %s::text, %s::text))"
                    )
                    boundary_params = [started_at, row_kind, run_id]
        elif sort == "duration":
            order_by = (
                "duration_us IS NULL ASC, duration_us DESC, started IS NULL ASC, "
                "started DESC, p->>'run_id' DESC"
            )
            if cursor is not None:
                duration_us, started_at, row_kind, run_id = cursor
                if duration_us is None:
                    if started_at is None:
                        boundary_sql = (
                            "AND duration_us IS NULL AND started IS NULL "
                            "AND ('workflow'::text, p->>'run_id') < (%s::text, %s::text)"
                        )
                        boundary_params = [row_kind, run_id]
                    else:
                        boundary_sql = (
                            "AND duration_us IS NULL AND (started IS NULL "
                            "OR (started, 'workflow'::text, p->>'run_id') "
                            "< (%s::timestamptz, %s::text, %s::text))"
                        )
                        boundary_params = [started_at, row_kind, run_id]
                else:
                    boundary_sql = (
                        "AND (duration_us IS NULL OR duration_us < %s "
                        "OR (duration_us = %s AND (started IS NULL "
                        "OR (started, 'workflow'::text, p->>'run_id') "
                        "< (%s::timestamptz, %s::text, %s::text))))"
                    )
                    boundary_params = [duration_us, duration_us, started_at, row_kind, run_id]
        elif sort == "shortest":
            order_by = (
                "duration_us IS NULL ASC, duration_us ASC, started IS NULL ASC, "
                "started ASC, p->>'run_id' ASC"
            )
            if cursor is not None:
                duration_us, started_at, row_kind, run_id = cursor
                if duration_us is None:
                    if started_at is None:
                        boundary_sql = (
                            "AND duration_us IS NULL AND started IS NULL "
                            "AND ('workflow'::text, p->>'run_id') > (%s::text, %s::text)"
                        )
                        boundary_params = [row_kind, run_id]
                    else:
                        boundary_sql = (
                            "AND duration_us IS NULL AND (started IS NULL "
                            "OR (started, 'workflow'::text, p->>'run_id') "
                            "> (%s::timestamptz, %s::text, %s::text))"
                        )
                        boundary_params = [started_at, row_kind, run_id]
                else:
                    boundary_sql = (
                        "AND (duration_us IS NULL OR duration_us > %s "
                        "OR (duration_us = %s AND (started IS NULL "
                        "OR (started, 'workflow'::text, p->>'run_id') "
                        "> (%s::timestamptz, %s::text, %s::text))))"
                    )
                    boundary_params = [duration_us, duration_us, started_at, row_kind, run_id]
        else:
            raise ValueError(f"Unsupported workflow run sort: {sort}")
        with self._cursor() as cur:
            cur.execute(
                f"""
                WITH runs AS (
                    SELECT s.value->'payload' AS p, d.value->'payload' AS definition,
                           COALESCE(
                               NULLIF(s.value#>>'{{payload,started_at}}', ''),
                               NULLIF(s.value#>>'{{payload,queued_at}}', '')
                           )::timestamptz AS started,
                           NULLIF(s.value#>>'{{payload,completed_at}}', '')::timestamptz AS completed
                    FROM store s LEFT JOIN store d
                      ON d.prefix = s.prefix
                     AND d.key = 'workflow:' || (s.value#>>'{{payload,workflow_id}}')
                    WHERE s.prefix = %s
                      AND s.key LIKE %s
                      AND (
                          %s::timestamptz IS NULL
                          OR COALESCE(
                              NULLIF(s.value#>>'{{payload,started_at}}', ''),
                              NULLIF(s.value#>>'{{payload,queued_at}}', '')
                          )::timestamptz >= %s::timestamptz
                      )
                ),
                ranked AS (
                    SELECT *,
                           CASE WHEN started IS NULL THEN NULL ELSE GREATEST(
                               0::bigint,
                               floor(extract(epoch FROM (
                                   COALESCE(completed, %s::timestamptz) - started
                               )) * 1000000)::bigint
                           ) END AS duration_us
                    FROM runs
                )
                SELECT jsonb_build_object(
                    'run_id', p->'run_id', 'workflow_id', p->'workflow_id',
                    'status', p->'status', 'source', p->'source',
                    'started_at', p->'started_at', 'queued_at', p->'queued_at',
                    'completed_at', p->'completed_at',
                    'completed_node_ids', p->'completed_node_ids',
                    'tokens', p->'tokens',
                    'node_threads', COALESCE((
                        SELECT jsonb_object_agg(node_id, jsonb_build_object(
                            'thread_id', details->'thread_id'
                        ))
                        FROM jsonb_each(
                            CASE WHEN jsonb_typeof(p->'node_outputs') = 'object'
                                 THEN p->'node_outputs' ELSE '{{}}'::jsonb END
                        ) AS output(node_id, details)
                        WHERE jsonb_typeof(details) = 'object' AND details ? 'thread_id'
                    ), '{{}}'::jsonb),
                    'pending_routine', CASE
                        WHEN jsonb_typeof(p->'pending_routine') = 'object'
                        THEN jsonb_strip_nulls(jsonb_build_object(
                            'node_id', p->'pending_routine'->'node_id',
                            'thread_id', p->'pending_routine'->'thread_id'
                        ))
                        ELSE NULL
                    END,
                    'step_audit', COALESCE((
                        SELECT jsonb_agg(jsonb_build_object(
                            'node_id', entry->'node_id',
                            'kind', entry->'kind',
                            'started_at', entry->'started_at',
                            'completed_at', entry->'completed_at',
                            'status', entry->'status',
                            'tokens', entry->'tokens'
                        ))
                        FROM jsonb_array_elements(
                            CASE WHEN jsonb_typeof(p->'step_audit') = 'array'
                                 THEN p->'step_audit' ELSE '[]'::jsonb END
                        ) AS entry
                    ), '[]'::jsonb)
                ) AS payload,
                jsonb_strip_nulls(jsonb_build_object(
                    'id', definition->'id',
                    'title', definition->'title',
                    'graph', jsonb_build_object('nodes', COALESCE((
                        SELECT jsonb_object_agg(
                            node_id,
                            jsonb_strip_nulls(jsonb_build_object(
                                'kind', details->'kind',
                                'title', details->'title',
                                'label', details->'label'
                            ))
                        )
                        FROM jsonb_each(
                            CASE WHEN jsonb_typeof(definition->'graph'->'nodes') = 'object'
                                 THEN definition->'graph'->'nodes' ELSE '{{}}'::jsonb END
                        ) AS node(node_id, details)
                        WHERE jsonb_typeof(details) = 'object'
                    ), '{{}}'::jsonb))
                )) AS definition
                FROM ranked
                WHERE (%s = '' OR lower(p->>'status') = %s)
                  AND (%s = '' OR strpos(lower(COALESCE(definition->>'title', p->>'workflow_id')), %s) > 0
                       OR strpos(lower(COALESCE(p->>'source', 'manual')), %s) > 0
                       OR strpos(lower(p->>'run_id'), %s) > 0)
                  {boundary_sql}
                ORDER BY {order_by}
                LIMIT %s
                """,  # nosec B608 - interpolation is constrained to the static sort clauses above
                (
                    [
                        PROGENA_STATE_PREFIX,
                        "workflow\\_run:%",
                        since,
                        since,
                        as_of,
                        status,
                        status,
                        q,
                        q,
                        q,
                        q,
                    ]
                    + boundary_params
                    + [limit]
                ),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [(row["payload"], row["definition"]) for row in rows]

    def search_documents(
        self,
        prefix: str,
        key_prefix: str,
        fields: tuple[str, ...],
        source: Literal["json", "front_matter"],
        q: str,
        limit: int = 5,
        key_suffix: str | None = None,
    ) -> list[dict[str, Any]]:
        like_prefix = _escape_like(key_prefix) + "%"
        like_suffix = "%" + _escape_like(key_suffix) if key_suffix is not None else None
        params: list[Any] = [prefix, like_prefix]
        if like_suffix is not None:
            params.append(like_suffix)

        ilike_pattern = f"%{_escape_like(q)}%"

        if source == "front_matter":
            # Search inside the markdown content; front matter is parsed later.
            match_sql = sql.SQL("value->'payload'->>'content' ILIKE %s")
            params.append(ilike_pattern)
        else:
            # A row matches when any searched field contains the query.
            match_sql = sql.SQL("({})").format(
                sql.SQL(" OR ").join(
                    sql.SQL("value->'payload'->>{} ILIKE %s").format(sql.Literal(field))
                    for field in fields
                )
            )
            params.extend(ilike_pattern for _ in fields)

        key_clause = sql.SQL("key LIKE %s")
        if like_suffix is not None:
            key_clause = sql.SQL("key LIKE %s AND key LIKE %s")

        where_sql = sql.SQL("prefix = %s AND {} AND {}").format(key_clause, match_sql)

        # Order: title prefix match first, then updated_at desc.
        if source == "json" and fields:
            order_sql = sql.SQL(
                "CASE WHEN value->'payload'->>{} ILIKE %s THEN 0 ELSE 1 END, updated_at DESC"
            ).format(sql.Literal(fields[0]))
            params.append(f"{_escape_like(q)}%")
        else:
            order_sql = sql.SQL("updated_at DESC")

        query = sql.SQL(
            "SELECT key, value, updated_at FROM store WHERE {} ORDER BY {} LIMIT %s"
        ).format(where_sql, order_sql)
        params.append(limit)

        with self._cursor() as cur:
            cur.execute(query, params)
            rows = cast(list[dict[str, Any]], cur.fetchall())

        results: list[dict[str, Any]] = []
        for row in rows:
            value = row.get("value")
            payload = value.get("payload") if isinstance(value, dict) else None
            results.append(
                {
                    "key": str(row["key"]),
                    "payload": payload,
                    "updated_at": row.get("updated_at"),
                }
            )
        return results

    def save_document(self, prefix: str, key: str, payload: Any) -> None:
        value = {"payload": payload}
        self._write_with_origin(
            """
            INSERT INTO store (prefix, key, value, created_at, updated_at)
            VALUES (%s, %s, %s, now(), now())
            ON CONFLICT (prefix, key) DO UPDATE
                SET value = EXCLUDED.value,
                    updated_at = now()
            """,
            (prefix, key, Jsonb(value)),
        )

    def merge_document(self, prefix: str, key: str, payload: Any) -> bool:
        """Merge ``payload`` into an existing document's payload; ``False`` if it is missing."""

        return (
            self._write_with_origin(
                """
            UPDATE store
            SET value = jsonb_set(value, '{payload}', (value->'payload') || %s::jsonb),
                updated_at = now()
            WHERE prefix = %s AND key = %s
            """,
                (Jsonb(payload), prefix, key),
            )
            > 0
        )

    def delete_document(self, prefix: str, key: str) -> None:
        self._write_with_origin(
            "DELETE FROM store WHERE prefix = %s AND key = %s",
            (prefix, key),
        )

    def pop_document(self, prefix: str, key: str) -> Any | None:
        """Atomically remove a document and return its previous payload."""

        origin = _document_write_origin.get()
        with self._cursor() as cur:
            if origin is None:
                cur.execute(
                    "DELETE FROM store WHERE prefix = %s AND key = %s RETURNING value",
                    (prefix, key),
                )
            else:
                with cur.connection.transaction():
                    cur.execute("SELECT set_config('progena.origin', %s, true)", (origin,))
                    cur.execute(
                        "DELETE FROM store WHERE prefix = %s AND key = %s RETURNING value",
                        (prefix, key),
                    )
            row = cast(dict[str, Any] | None, cur.fetchone())
        if row is None:
            return None
        value = row.get("value")
        return value.get("payload") if isinstance(value, dict) else None

    def mutate_document(self, prefix: str, key: str, mutator: Callable[[Any], Any]) -> Any:
        try:
            return self._mutate_document_impl(prefix, key, mutator)
        except PoolClosed:
            logger.warning("Backend document store pool was closed; recreating it")
            self._recreate_pool()
            self._ensure_store_table()
            return self._mutate_document_impl(prefix, key, mutator)

    def _mutate_document_impl(self, prefix: str, key: str, mutator: Callable[[Any], Any]) -> Any:
        origin = _document_write_origin.get()
        with self._pool.connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                # A transaction-scoped lock on the key, so two first writes of
                # a missing document serialize; plus a row lock on an existing
                # document, so plain saves/merges (which skip the advisory
                # lock) cannot commit between this read and the write below.
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                    (prefix, key),
                )
                cur.execute(
                    "SELECT value FROM store WHERE prefix = %s AND key = %s FOR UPDATE",
                    (prefix, key),
                )
                row = cast(dict[str, Any] | None, cur.fetchone())
                payload = row["value"]["payload"] if row is not None else None
                new_payload = mutator(payload)
                value = {"payload": new_payload}
                if origin is not None:
                    cur.execute("SELECT set_config('progena.origin', %s, true)", (origin,))
                cur.execute(
                    """
                        INSERT INTO store (prefix, key, value, created_at, updated_at)
                        VALUES (%s, %s, %s, now(), now())
                        ON CONFLICT (prefix, key) DO UPDATE
                            SET value = EXCLUDED.value,
                                updated_at = now()
                        """,
                    (prefix, key, Jsonb(value)),
                )
            return new_payload

    def _write_with_origin(self, query: LiteralString, params: tuple[Any, ...]) -> int:
        """Run one store write, tagging it with the context's writer origin.

        ``set_config(..., true)`` is transaction-local, so the tagged write runs
        in an explicit transaction where the event-log trigger can read it.
        """
        origin = _document_write_origin.get()
        with self._cursor() as cur:
            if origin is None:
                cur.execute(query, params)
                return cur.rowcount
            with cur.connection.transaction():
                cur.execute("SELECT set_config('progena.origin', %s, true)", (origin,))
                cur.execute(query, params)
            return cur.rowcount

    def read_document_events(self, after: DocumentEventPosition, limit: int) -> list[DocumentEvent]:
        """Events after ``after`` from transactions older than every open one.

        Every transaction below the snapshot xmin has committed or rolled back,
        so this prefix of the ``(txid, seq)`` order can never gain a new row.
        """

        with self._cursor() as cur:
            cur.execute(
                """
                SELECT seq, txid::text::bigint AS txid, prefix, key, op, before, after,
                       origin, created_at
                FROM document_events
                WHERE txid < pg_snapshot_xmin(pg_current_snapshot())
                  AND (txid, seq) > (%s::text::xid8, %s)
                ORDER BY txid, seq
                LIMIT %s
                """,
                (str(after.txid), after.seq, limit),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [
            DocumentEvent(
                seq=int(row["seq"]),
                txid=int(row["txid"]),
                prefix=str(row["prefix"]),
                key=str(row["key"]),
                op=cast(DocumentEventOp, row["op"]),
                before=row.get("before"),
                after=row.get("after"),
                origin=row.get("origin"),
                created_at=row["created_at"].astimezone(UTC),
            )
            for row in rows
        ]

    def has_deferred_document_events(self, after: DocumentEventPosition) -> bool:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM document_events
                    WHERE (txid, seq) > (%s::text::xid8, %s)
                      AND txid >= pg_snapshot_xmin(pg_current_snapshot())
                ) AS deferred
                """,
                (str(after.txid), after.seq),
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        return bool(row and row["deferred"])

    def latest_document_event_position(self) -> DocumentEventPosition:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT txid::text::bigint AS txid, seq
                FROM document_events
                WHERE txid < pg_snapshot_xmin(pg_current_snapshot())
                ORDER BY txid DESC, seq DESC
                LIMIT 1
                """
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        if row is None:
            return EVENT_LOG_START
        return DocumentEventPosition(int(row["txid"]), int(row["seq"]))

    def get_document_event_cursor(self, consumer: str) -> DocumentEventPosition | None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT txid::text::bigint AS txid, seq FROM document_event_cursors "
                "WHERE consumer = %s",
                (consumer,),
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        if row is None:
            return None
        return DocumentEventPosition(int(row["txid"]), int(row["seq"]))

    def save_document_event_cursor(self, consumer: str, position: DocumentEventPosition) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO document_event_cursors (consumer, txid, seq, updated_at)
                VALUES (%s, %s::text::xid8, %s, now())
                ON CONFLICT (consumer) DO UPDATE
                    SET txid = EXCLUDED.txid, seq = EXCLUDED.seq, updated_at = now()
                """,
                (consumer, str(position.txid), position.seq),
            )

    def prune_document_events(
        self, *, through: DocumentEventPosition, older_than: timedelta
    ) -> int:
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM document_events "
                "WHERE (txid, seq) <= (%s::text::xid8, %s) AND created_at < now() - %s",
                (str(through.txid), through.seq, older_than),
            )
            return cur.rowcount

    def query_logs(
        self,
        *,
        level: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = [sql.SQL("prefix LIKE %s")]
        params: list[Any] = [f"{PROGENA_LOG_PREFIX}.%"]
        if source:
            clauses.append(sql.SQL("prefix = %s"))
            params.append(f"{PROGENA_LOG_PREFIX}.{source}")
        if level:
            clauses.append(sql.SQL("lower(value->>'level') = %s"))
            params.append(level.lower())
        if limit > 0:
            params.append(limit)
        where_clause = sql.SQL(" AND ").join(clauses)
        limit_clause = sql.SQL("LIMIT %s") if limit > 0 else sql.SQL("")
        with self._cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                SELECT prefix, key, value, updated_at
                FROM store
                WHERE {}
                ORDER BY COALESCE(value->>'timestamp', value->>'time', updated_at::text) DESC
                {}
                """
                ).format(where_clause, limit_clause),
                params,
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [_log_row_to_entry(row) for row in reversed(rows)]

    def query_tool_audits(self, tool_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = list({tool_id for tool_id in tool_ids if tool_id})
        if not ids:
            return {}
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT value->'payload' AS payload
                FROM store
                WHERE prefix = %s AND value->'payload'->>'tool_id' = ANY(%s)
                """,
                (f"{PROGENA_LOG_PREFIX}.tool_calls", ids),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return {
            str(row["payload"]["tool_id"]): dict(row["payload"])
            for row in rows
            if isinstance(row.get("payload"), dict)
            and isinstance(row["payload"].get("tool_id"), str)
        }

    def get_log_sources(self) -> list[dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT
                    replace(prefix, %s, '') AS source,
                    count(*) AS count,
                    max(updated_at) AS updated_at
                FROM store
                WHERE prefix LIKE %s
                GROUP BY prefix
                ORDER BY source
                """,
                (f"{PROGENA_LOG_PREFIX}.", f"{PROGENA_LOG_PREFIX}.%"),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [dict(row) for row in rows]

    def inspect_schema(self) -> list[dict[str, Any]]:
        """Return table/column metadata for backend and LangGraph persistence tables."""
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND (
                    table_name = 'store'
                    OR table_name LIKE 'checkpoint%'
                    OR table_name LIKE 'langgraph%'
                  )
                ORDER BY table_name, ordinal_position
                """
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())

        tables: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            table_name = str(row["table_name"])
            tables.setdefault(table_name, []).append(
                {
                    "name": row["column_name"],
                    "type": row["data_type"],
                    "nullable": row["is_nullable"] == "YES",
                }
            )
        return [
            {"table": table_name, "columns": columns, "source": "postgres"}
            for table_name, columns in tables.items()
        ]

    def list_checkpoint_message_blobs(
        self, *, before: datetime, limit: int | None = None
    ) -> tuple[list[CheckpointMessageBlob], bool]:
        """Return recent durable message snapshots and persisted message deltas.

        LangGraph stores a compact state snapshot in ``checkpoint_blobs`` and
        writes individual channel updates to ``checkpoint_writes``. Both are
        needed: a delta can contain a completed AI message before a later state
        snapshot references it. Accounting reads all retained history unless a
        caller explicitly supplies a limit; silently capping rows could both
        undercount calls and move first-observed messages into the wrong range.
        """
        if limit is not None and limit <= 0:
            return [], False
        with self._cursor() as cur:
            cur.execute(
                """
                WITH message_versions AS (
                    SELECT DISTINCT ON (
                        c.thread_id,
                        c.checkpoint_ns,
                        c.checkpoint->'channel_versions'->>'messages'
                    )
                        c.thread_id,
                        c.checkpoint_ns,
                        c.checkpoint_id,
                        (c.checkpoint->>'ts')::timestamptz AS checkpoint_ts,
                        c.metadata AS checkpoint_metadata,
                        c.checkpoint->'channel_versions'->>'messages' AS version
                    FROM checkpoints c
                    WHERE c.checkpoint ? 'channel_versions'
                      AND c.checkpoint->'channel_versions' ? 'messages'
                      AND (c.checkpoint->>'ts')::timestamptz < %s
                    ORDER BY
                        c.thread_id,
                        c.checkpoint_ns,
                        c.checkpoint->'channel_versions'->>'messages',
                        (c.checkpoint->>'ts')::timestamptz ASC,
                        c.checkpoint_id ASC
                ),
                message_values AS (
                    SELECT
                        v.thread_id,
                        v.checkpoint_ns,
                        v.checkpoint_id,
                        v.checkpoint_ts,
                        v.checkpoint_metadata,
                        b.type AS blob_type,
                        b.blob,
                        'channel_blob'::text AS source,
                        0 AS source_rank
                    FROM message_versions v
                    JOIN checkpoint_blobs b
                      ON b.thread_id = v.thread_id
                     AND b.checkpoint_ns = v.checkpoint_ns
                     AND b.channel = 'messages'
                     AND b.version = v.version
                ),
                message_deltas AS (
                    SELECT
                        c.thread_id,
                        c.checkpoint_ns,
                        c.checkpoint_id,
                        (c.checkpoint->>'ts')::timestamptz AS checkpoint_ts,
                        c.metadata AS checkpoint_metadata,
                        w.type AS blob_type,
                        w.blob,
                        'checkpoint_write'::text AS source,
                        1 AS source_rank
                    FROM checkpoint_writes w
                    JOIN checkpoints c
                      ON c.thread_id = w.thread_id
                     AND c.checkpoint_ns = w.checkpoint_ns
                     AND c.checkpoint_id = w.checkpoint_id
                    WHERE w.channel = 'messages'
                      AND (c.checkpoint->>'ts')::timestamptz < %s
                ),
                all_message_values AS (
                    SELECT * FROM message_values
                    UNION ALL
                    SELECT * FROM message_deltas
                )
                SELECT
                    thread_id,
                    checkpoint_ns,
                    checkpoint_id,
                    checkpoint_ts,
                    checkpoint_metadata,
                    blob_type,
                    blob,
                    source
                FROM all_message_values
                ORDER BY checkpoint_ts DESC, source_rank DESC, thread_id DESC, checkpoint_id DESC
                LIMIT %s
                """,
                (before, before, limit + 1 if limit is not None else None),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        has_more = limit is not None and len(rows) > limit
        return (
            [
                CheckpointMessageBlob(
                    thread_id=str(row["thread_id"]),
                    checkpoint_ns=str(row["checkpoint_ns"]),
                    checkpoint_id=str(row["checkpoint_id"]),
                    checkpoint_ts=cast(datetime, row["checkpoint_ts"]),
                    checkpoint_metadata=(
                        dict(row["checkpoint_metadata"])
                        if isinstance(row.get("checkpoint_metadata"), dict)
                        else {}
                    ),
                    blob_type=str(row["blob_type"]),
                    blob=bytes(row["blob"]),
                    source=cast(Literal["channel_blob", "checkpoint_write"], row["source"]),
                )
                for row in rows[:limit]
            ],
            has_more,
        )

    def list_checkpoint_thread_snapshots(
        self, *, limit: int = 50
    ) -> list[CheckpointThreadSnapshot]:
        """Return latest reconstructed message snapshots for LangGraph threads."""
        with self._cursor() as cur:
            cur.execute(
                """
                WITH ranked AS (
                    SELECT
                        c.thread_id,
                        c.checkpoint_ns,
                        c.checkpoint_id,
                        c.checkpoint->>'ts' AS checkpoint_ts,
                        row_number() OVER (
                            PARTITION BY c.thread_id
                            ORDER BY (c.checkpoint->>'ts')::timestamptz DESC, c.checkpoint_id DESC
                        ) AS rn
                    FROM checkpoints c
                    -- Filters on channel_versions, not channel_values.
                    WHERE c.checkpoint_ns = ''
                      AND c.checkpoint ? 'channel_versions'
                      AND c.checkpoint->'channel_versions' ? 'messages'
                )
                SELECT thread_id, checkpoint_ns, checkpoint_id, checkpoint_ts
                FROM ranked
                WHERE rn = 1
                ORDER BY checkpoint_ts DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        targets = [
            (
                str(row["thread_id"]),
                str(row["checkpoint_id"]) if row.get("checkpoint_id") else None,
                row,
            )
            for row in rows
            if isinstance(row.get("thread_id"), str) and row.get("thread_id")
        ]

        def _messages_or_unavailable(target: tuple[str, str | None, Any]) -> list[Any] | None:
            # The list must still render while the runtime is down; None marks
            # the thread's messages as unknown rather than empty.
            thread_id, checkpoint_id, _row = target
            try:
                return self._checkpoint_messages(thread_id=thread_id, checkpoint_id=checkpoint_id)
            except RuntimeUnavailable:
                return None

        with ThreadPoolExecutor(max_workers=CONVERSATION_FETCH_CONCURRENCY) as pool:
            all_messages = list(pool.map(_messages_or_unavailable, targets))
        return [
            CheckpointThreadSnapshot(
                thread_id=thread_id,
                checkpoint_id=checkpoint_id,
                checkpoint_ts=str(row["checkpoint_ts"]) if row.get("checkpoint_ts") else None,
                messages=messages or [],
                messages_available=messages is not None,
            )
            for (thread_id, checkpoint_id, row), messages in zip(targets, all_messages, strict=True)
        ]

    def get_checkpoint_thread_snapshot(
        self,
        thread_id: str,
        *,
        checkpoint_id: str | None = None,
    ) -> CheckpointThreadSnapshot | None:
        # Same channel_versions-vs-channel_values caveat as
        # list_checkpoint_thread_snapshots() above.
        clauses = [
            sql.SQL("c.thread_id = %s"),
            sql.SQL("c.checkpoint_ns = ''"),
            sql.SQL("c.checkpoint ? 'channel_versions'"),
            sql.SQL("c.checkpoint->'channel_versions' ? 'messages'"),
        ]
        params: list[Any] = [thread_id]
        if checkpoint_id:
            clauses.append(sql.SQL("c.checkpoint_id = %s"))
            params.append(checkpoint_id)
        where_clause = sql.SQL(" AND ").join(clauses)
        with self._cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                SELECT c.thread_id, c.checkpoint_id, c.checkpoint->>'ts' AS checkpoint_ts
                     , c.checkpoint_ns
                FROM checkpoints c
                WHERE {}
                ORDER BY (c.checkpoint->>'ts')::timestamptz DESC, c.checkpoint_id DESC
                LIMIT 1
                """
                ).format(where_clause),
                params,
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        if row is None:
            return None
        resolved_thread_id = row.get("thread_id")
        if not isinstance(resolved_thread_id, str) or not resolved_thread_id:
            return None
        resolved_checkpoint_id = str(row["checkpoint_id"]) if row.get("checkpoint_id") else None
        messages = self._checkpoint_messages(
            thread_id=resolved_thread_id, checkpoint_id=resolved_checkpoint_id
        )
        return CheckpointThreadSnapshot(
            thread_id=resolved_thread_id,
            checkpoint_id=resolved_checkpoint_id,
            checkpoint_ts=str(row["checkpoint_ts"]) if row.get("checkpoint_ts") else None,
            messages=messages,
        )

    def list_routine_runs(
        self, routine_id: str, *, limit: int = 50, before: str | None = None
    ) -> tuple[list[RoutineRunSummary], bool]:
        """Every distinct routine-run checkpoint thread for one routine.

        A run's thread_id is ``routine:<routine_id>:<revision_id>:<run_id>``
        (see ``src.progena.routines.subagent.create_routine_subagent``).
        Groups raw checkpoint rows by thread_id -- one row per run, newest
        last-checkpoint first -- rather than reconstructing full message
        history, which the Runs panel doesn't need.

        ``before`` is a keyset cursor: the ``last_checkpoint_at`` of the
        oldest run already loaded by the caller (verbatim string this same
        method previously returned), so paging never relies on OFFSET
        against a live-growing table. Fetches one extra row over ``limit``
        purely to answer ``has_more`` without a second COUNT query, then
        trims it back off before returning.

        ``failed`` is a per-run ``EXISTS`` against ``checkpoint_writes`` for
        LangGraph's own ``__error__`` pending-write channel -- written
        unconditionally by LangGraph's task runner the instant any node
        raises, *before* any of our own error-handling code runs. This is
        deliberately NOT derived from the ``run_meta`` channel's own
        per-node ``error`` field: that field only exists when
        subagent.py's narrow exception catch fires AND its own backfill
        write succeeds (both best-effort), so it under-reports real
        failures. The EXISTS runs once per already-LIMIT-bounded row,
        against ``checkpoint_writes_thread_id_idx`` -- no checkpoint blob
        deserialization, no extra connection, one round trip alongside the
        existing GROUP BY.
        """
        escaped = routine_id.translate({ord("\\"): "\\\\", ord("%"): "\\%", ord("_"): "\\_"})
        pattern = f"routine:{escaped}:%"
        params: list[Any] = [pattern, before is None, before, limit + 1]
        with self._cursor() as cur:
            cur.execute(
                """
                WITH runs AS (
                    SELECT
                        thread_id,
                        min((checkpoint->>'ts')::timestamptz) AS started_at,
                        max((checkpoint->>'ts')::timestamptz) AS last_checkpoint_at,
                        count(*) AS checkpoint_count
                    FROM checkpoints
                    WHERE thread_id LIKE %s ESCAPE '\\'
                    GROUP BY thread_id
                    HAVING %s OR max((checkpoint->>'ts')::timestamptz) < %s::timestamptz
                    ORDER BY last_checkpoint_at DESC
                    LIMIT %s
                )
                SELECT
                    runs.*,
                    EXISTS (
                        SELECT 1 FROM checkpoint_writes cw
                        WHERE cw.thread_id = runs.thread_id AND cw.channel = '__error__'
                    ) AS failed
                FROM runs
                ORDER BY last_checkpoint_at DESC
                """,
                params,
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        has_more = len(rows) > limit
        rows = rows[:limit]
        summaries: list[RoutineRunSummary] = []
        for row in rows:
            thread_id = row.get("thread_id")
            if not isinstance(thread_id, str):
                continue
            parts = thread_id.split(":", 3)
            revision_id = parts[2] if len(parts) > 2 else None
            run_id = parts[3] if len(parts) > 3 else None
            summaries.append(
                RoutineRunSummary(
                    thread_id=thread_id,
                    routine_id=routine_id,
                    revision_id=revision_id,
                    run_id=run_id,
                    started_at=str(row["started_at"]) if row.get("started_at") else None,
                    last_checkpoint_at=(
                        str(row["last_checkpoint_at"]) if row.get("last_checkpoint_at") else None
                    ),
                    checkpoint_count=int(row["checkpoint_count"] or 0),
                    failed=bool(row.get("failed")),
                )
            )
        return summaries, has_more

    def list_routine_revisions(
        self, routine_id: str, *, limit: int = 50
    ) -> list[RoutineRevisionSummary]:
        """Every captured revision of one routine's graph document, newest first.

        Mirrors ``src.progena.routines.revisions.PostgresRoutineRevisionReader``,
        reading the same ``routine_graph_revisions`` table this store's own
        ``_ensure_routine_revision_history`` DDL/trigger populates.
        """
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT revision_id, captured_at
                FROM routine_graph_revisions
                WHERE routine_id = %s
                ORDER BY captured_at DESC, revision_id DESC
                LIMIT %s
                """,
                (routine_id, limit),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [
            RoutineRevisionSummary(
                revision_id=row["revision_id"],
                routine_id=routine_id,
                captured_at=str(row["captured_at"]) if row.get("captured_at") else None,
            )
            for row in rows
        ]

    def get_routine_revision(self, routine_id: str, revision_id: str) -> dict[str, Any] | None:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT graph
                FROM routine_graph_revisions
                WHERE routine_id = %s AND revision_id = %s
                """,
                (routine_id, revision_id),
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        return cast(dict[str, Any], row["graph"]) if row is not None else None

    def list_knowledge_page_revisions(
        self, base_id: str, path: str, *, limit: int = 50
    ) -> list[KnowledgePageRevisionSummary]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT revision_id, captured_at FROM knowledge_page_revisions "
                "WHERE base_id = %s AND path = %s "
                "ORDER BY captured_at DESC, revision_id DESC LIMIT %s",
                (base_id, path, limit),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [
            KnowledgePageRevisionSummary(
                revision_id=row["revision_id"],
                base_id=base_id,
                path=path,
                captured_at=str(row["captured_at"]) if row.get("captured_at") else None,
            )
            for row in rows
        ]

    def get_knowledge_page_revision(self, base_id: str, path: str, revision_id: str) -> str | None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT content FROM knowledge_page_revisions "
                "WHERE base_id = %s AND path = %s AND revision_id = %s",
                (base_id, path, revision_id),
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        return str(row["content"]) if row is not None else None

    def list_instruction_revisions(
        self, path: str, *, limit: int = 50
    ) -> list[InstructionRevisionSummary]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT revision_id, captured_at FROM instruction_revisions "
                "WHERE path = %s ORDER BY captured_at DESC, revision_id DESC LIMIT %s",
                (path, limit),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [
            InstructionRevisionSummary(
                revision_id=row["revision_id"],
                path=path,
                captured_at=str(row["captured_at"]) if row.get("captured_at") else None,
            )
            for row in rows
        ]

    def get_instruction_revision(self, path: str, revision_id: str) -> str | None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT content FROM instruction_revisions WHERE path = %s AND revision_id = %s",
                (path, revision_id),
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        return str(row["content"]) if row is not None else None

    def list_canvas_revisions(self, name: str, *, limit: int = 100) -> list[CanvasRevision]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT id, name, path, op, content, content_hash, captured_at
                FROM canvas_revisions
                WHERE name = %s
                ORDER BY captured_at DESC, id DESC
                LIMIT %s
                """,
                (name, limit),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [
            CanvasRevision(
                id=int(row["id"]),
                name=str(row["name"]),
                path=str(row["path"]),
                op=cast(Literal["write", "delete"], row["op"]),
                content=str(row["content"]) if row.get("content") is not None else None,
                content_hash=str(row["content_hash"]),
                captured_at=str(row["captured_at"]) if row.get("captured_at") else None,
            )
            for row in rows
        ]


def store_database_url(store: BackendDocumentStore | None) -> str | None:
    """Return the Postgres DSN backing ``store``, or None for non-Postgres stores."""
    url = getattr(store, "database_url", None)
    return url if isinstance(url, str) else None


def ensure_runs_checkpoint_trigger(store: BackendDocumentStore) -> None:
    """Install the optional Runs checkpoint trigger for a Postgres-backed store."""
    installer = getattr(store, "ensure_runs_checkpoint_trigger", None)
    if callable(installer):
        installer()


class StoreChangeListener:
    """Listen for ``store`` table changes over Postgres LISTEN/NOTIFY.

    Fully event-driven: on every (re)connect it fires ``on_resync`` so callers
    can recover any changes missed while disconnected, then streams NOTIFY
    events to ``on_change``. There is no polling timer.
    """

    def __init__(
        self,
        database_url: str,
        *,
        on_change: Callable[[str], None],
        on_resync: Callable[[], None] | None = None,
        reconnect_delay: float = 2.0,
        channel: str = STORE_CHANGE_CHANNEL,
    ) -> None:
        self._database_url = database_url
        self._on_change = on_change
        self._on_resync = on_resync
        self._reconnect_delay = reconnect_delay
        self._channel = channel
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._listen_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - keep listening across DB blips
                logger.warning(
                    "Store change listener disconnected (%s); reconnecting in %ss",
                    exc,
                    self._reconnect_delay,
                )
                try:
                    await asyncio.sleep(self._reconnect_delay)
                except asyncio.CancelledError:
                    break

    async def _listen_once(self) -> None:
        aconn = await psycopg.AsyncConnection.connect(self._database_url, autocommit=True)
        try:
            # Subscribe first so changes during resync are queued, not lost.
            await aconn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(self._channel)))
            if self._on_resync is not None:
                self._on_resync()
            logger.info("Listening for store changes on %r", self._channel)
            async for notify in aconn.notifies():
                self._on_change(notify.payload)
        finally:
            await aconn.close()


def _escape_like(text: str) -> str:
    """Escape LIKE wildcards so a key prefix matches literally (ids may contain ``_``)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _log_row_to_entry(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("value")
    entry = dict(value) if isinstance(value, dict) else {}
    source = str(row.get("prefix") or "").removeprefix(f"{PROGENA_LOG_PREFIX}.")
    entry.setdefault("id", row.get("key"))
    entry.setdefault("source", source)
    entry.setdefault("timestamp", _timestamp_text(entry.get("timestamp") or row.get("updated_at")))
    entry.setdefault("time", entry["timestamp"])
    entry.setdefault("level", "info")
    entry.setdefault("message", "")
    entry.setdefault("relative_path", f"{entry['source']}.jsonl")
    return entry


def _timestamp_text(value: Any) -> str:
    if isinstance(value, datetime):
        timestamp = value if value.tzinfo else value.replace(tzinfo=UTC)
        return timestamp.isoformat()
    if isinstance(value, str) and value:
        return value
    return datetime.now(UTC).isoformat()


_default_store: BackendDocumentStore = InMemoryDocumentStore()


def configure_default_document_store(
    database_url: str | None, *, runtime_url: str | None = None
) -> BackendDocumentStore:
    """Configure the process-wide backend document store."""
    global _default_store
    _default_store.close()
    if database_url:
        pg_store = PostgresDocumentStore(database_url, runtime_url=runtime_url)
        pg_store.open()
        store: BackendDocumentStore = pg_store
        logger.info("Backend document store connected to Postgres")
    else:
        store = InMemoryDocumentStore()
        logger.warning("PROGENA_DATABASE_URL is not set; backend document store is in-memory")
    _default_store = store
    return _default_store


def set_default_document_store(store: BackendDocumentStore) -> None:
    """Install a store explicitly, primarily for tests."""
    global _default_store
    _default_store.close()
    _default_store = store


def get_default_document_store() -> BackendDocumentStore:
    return _default_store


def seed_documents(
    documents: Iterable[tuple[str, str, Any]],
    *,
    store: BackendDocumentStore | None = None,
) -> BackendDocumentStore:
    target = store or _default_store
    for prefix, key, payload in documents:
        target.save_document(prefix, key, payload)
    return target
