"""Tasks service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any, cast

from fastapi import HTTPException

import src.backend.events.contracts as events_contracts
import src.backend.events.documents as events_documents
import src.backend.events.stream as events_stream
import src.backend.monitoring.state as monitoring_state
import src.backend.schedules.models as schedules_models
import src.backend.storage.access as storage_access
import src.backend.storage.documents as storage_documents
import src.backend.tasks.models as tasks_models
from src.progenitor.documents.kinds import KANBAN_CARD_KEY_PREFIX, KANBAN_TASK_CONTENT_KEY_PREFIX
from src.progenitor.utils import utcnow

logger = logging.getLogger(__name__)


def _kanban_default_columns() -> list[dict]:
    """Return the default kanban workflow columns."""
    return [
        {
            "id": "backlog",
            "label": "Backlog",
            "description": "New user requests waiting to be shaped into delivery work.",
            "agent_name": "user",
            "allows_create": True,
            "stage_kind": "intake",
        },
        {
            "id": "plan",
            "label": "Plan",
            "description": "Turn the chosen approach into specs and implementation notes.",
            "agent_name": "planner_agent",
            "allows_create": False,
            "stage_kind": "planning",
        },
        {
            "id": "build",
            "label": "Build",
            "description": "Implement the change, create code, and prepare a pull request.",
            "agent_name": "unassigned",
            "allows_create": False,
            "stage_kind": "build",
        },
        {
            "id": "validate",
            "label": "Validate",
            "description": "User-facing validation and final approval before the work is considered done.",
            "agent_name": "validate_agent",
            "allows_create": False,
            "stage_kind": "validate",
        },
        {
            "id": "done",
            "label": "Done",
            "description": "Merged and deployed work that no longer needs active handling.",
            "agent_name": "release_agent",
            "allows_create": False,
            "stage_kind": "done",
        },
    ]


def _canonical_kanban_column_id(column_id: object) -> str:
    """Map legacy kanban columns onto the current workflow."""
    normalized = str(column_id or "backlog").strip().lower()
    return {
        "research": "plan",
        "review": "validate",
        "test": "validate",
    }.get(normalized, normalized or "backlog")


def _kanban_status_options() -> list[tasks_models.KanbanStatusOptionResponse]:
    """Return allowed kanban card statuses."""
    return [
        tasks_models.KanbanStatusOptionResponse(id="draft", label="Draft", tone="neutral"),
        tasks_models.KanbanStatusOptionResponse(id="idle", label="Idle", tone="neutral"),
        tasks_models.KanbanStatusOptionResponse(id="running", label="Running", tone="info"),
        tasks_models.KanbanStatusOptionResponse(id="complete", label="Complete", tone="success"),
        tasks_models.KanbanStatusOptionResponse(id="failed", label="Failed", tone="error"),
        tasks_models.KanbanStatusOptionResponse(id="blocked", label="Blocked", tone="warning"),
    ]


def _kanban_card_schema(columns: list[dict]) -> tasks_models.KanbanCardSchemaResponse:
    """Return field schema for kanban cards."""
    status_options = _kanban_status_options()
    return tasks_models.KanbanCardSchemaResponse(
        statuses=status_options,
        fields=[
            tasks_models.KanbanFieldSchemaResponse(key="title", label="Title"),
            tasks_models.KanbanFieldSchemaResponse(key="summary", label="Summary"),
            tasks_models.KanbanFieldSchemaResponse(
                key="description", label="Description", control="textarea"
            ),
            tasks_models.KanbanFieldSchemaResponse(
                key="column_id",
                label="Column",
                control="select",
                options=[
                    schedules_models.WidgetFieldOptionResponse(
                        value=column["id"], label=column["label"]
                    )
                    for column in columns
                ],
            ),
            tasks_models.KanbanFieldSchemaResponse(
                key="status",
                label="Status",
                control="select",
                options=[
                    schedules_models.WidgetFieldOptionResponse(value=status.id, label=status.label)
                    for status in status_options
                ],
            ),
            tasks_models.KanbanFieldSchemaResponse(key="agent_name", label="Assigned Agent"),
            tasks_models.KanbanFieldSchemaResponse(key="owner", label="Owner"),
            tasks_models.KanbanFieldSchemaResponse(key="project_id", label="Project"),
            tasks_models.KanbanFieldSchemaResponse(key="spec_path", label="Spec File"),
            tasks_models.KanbanFieldSchemaResponse(key="branch_name", label="Branch"),
            tasks_models.KanbanFieldSchemaResponse(key="repo_id", label="Repository"),
            tasks_models.KanbanFieldSchemaResponse(key="acp_task_id", label="ACP Task"),
            tasks_models.KanbanFieldSchemaResponse(key="pr_url", label="Pull Request URL"),
            tasks_models.KanbanFieldSchemaResponse(
                key="last_error", label="Last Error", control="textarea"
            ),
        ],
    )


def _kanban_default_board() -> dict:
    """Return a default kanban board payload."""
    return {
        "id": "progenitor-kanban",
        "title": "Delivery Kanban",
        "updated_at": utcnow().isoformat(),
        "columns": _kanban_default_columns(),
        "cards": [],
    }


def _normalize_string_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []

    normalized: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _normalize_non_negative_int(value: object) -> int:
    """Normalize persisted integer fields that should never be negative."""
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _normalize_kanban_card(card: dict, columns: dict[str, dict]) -> dict:
    """Normalize raw kanban card data into a stable API shape."""
    now = utcnow().isoformat()
    column_id = _canonical_kanban_column_id(card.get("column_id"))
    if column_id not in columns:
        column_id = "backlog"

    allowed_statuses = {status.id for status in _kanban_status_options()}
    status = str(card.get("status") or "idle").strip().lower()
    if status not in allowed_statuses:
        status = "idle"

    title = str(card.get("title") or "").strip() or "Untitled card"
    metadata = card.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    return {
        "id": str(card.get("id") or f"card_{uuid.uuid4().hex[:12]}"),
        "title": title,
        "summary": str(card.get("summary") or "").strip(),
        "description": str(card.get("description") or ""),
        "column_id": column_id,
        "status": status,
        "agent_name": str(card.get("agent_name") or columns[column_id]["agent_name"]),
        "owner": str(card.get("owner") or "user"),
        "project_id": str(card.get("project_id") or "").strip() or None,
        "labels": _normalize_string_list(card.get("labels")),
        "spec_path": str(card.get("spec_path") or "").strip() or None,
        "branch_name": str(card.get("branch_name") or "").strip() or None,
        "repo_id": str(card.get("repo_id") or "").strip() or None,
        "acp_task_id": str(card.get("acp_task_id") or "").strip() or None,
        "pr_url": str(card.get("pr_url") or "").strip() or None,
        "started_at": str(card.get("started_at") or "").strip() or None,
        "completed_at": str(card.get("completed_at") or "").strip() or None,
        "last_error": str(card.get("last_error") or "").strip() or None,
        "created_at": str(card.get("created_at") or now),
        "updated_at": str(card.get("updated_at") or now),
        "position": _normalize_non_negative_int(card.get("position")),
        "metadata": metadata,
    }


def _resequence_kanban_cards(cards: list[dict], columns: list[dict]) -> list[dict]:
    """Sort cards into column order and assign dense per-column positions."""
    column_order = {column["id"]: index for index, column in enumerate(columns)}
    grouped_cards: dict[str, list[dict]] = {column["id"]: [] for column in columns}

    ordered_cards = sorted(
        cards,
        key=lambda card: (
            column_order.get(card["column_id"], len(columns)),
            _normalize_non_negative_int(card.get("position")),
            str(card.get("created_at") or ""),
            card["id"],
        ),
    )
    for card in ordered_cards:
        grouped_cards.setdefault(card["column_id"], []).append(dict(card))

    normalized_cards: list[dict] = []
    for column in columns:
        for index, card in enumerate(grouped_cards.get(column["id"], [])):
            card["position"] = index
            normalized_cards.append(card)
    return normalized_cards


def _reorder_kanban_cards(
    cards: list[dict],
    columns: list[dict],
    card_id: str,
    target_column_id: str,
    target_position: int,
) -> list[dict]:
    """Move a card to a specific position within a target column."""
    grouped_cards: dict[str, list[dict]] = {column["id"]: [] for column in columns}
    moving_card: dict | None = None

    for card in _resequence_kanban_cards(cards, columns):
        if card["id"] == card_id:
            moving_card = dict(card)
            continue
        grouped_cards.setdefault(card["column_id"], []).append(dict(card))

    if moving_card is None:
        raise HTTPException(status_code=404, detail="Kanban card not found")

    moving_card["column_id"] = target_column_id
    target_cards = grouped_cards.setdefault(target_column_id, [])
    insert_at = max(0, min(target_position, len(target_cards)))
    target_cards.insert(insert_at, moving_card)

    normalized_cards: list[dict] = []
    for column in columns:
        for index, card in enumerate(grouped_cards.get(column["id"], [])):
            card["position"] = index
            normalized_cards.append(card)
    return normalized_cards


def _normalize_kanban_board(data: object) -> dict:
    """Normalize persisted kanban board data."""
    defaults = _kanban_default_board()
    raw = data if isinstance(data, dict) else {}
    columns = _kanban_default_columns()
    column_map = {column["id"]: column for column in columns}
    cards = [
        _normalize_kanban_card(card, column_map)
        for card in raw.get("cards", [])
        if isinstance(card, dict)
    ]
    cards = _resequence_kanban_cards(cards, columns)
    return {
        "id": str(raw.get("id") or defaults["id"]),
        "title": str(raw.get("title") or defaults["title"]),
        "updated_at": str(raw.get("updated_at") or defaults["updated_at"]),
        "columns": columns,
        "cards": cards,
    }


def _kanban_default_board_document() -> dict:
    """Return a freshly normalized empty board for first-time seeding."""
    return _normalize_kanban_board(None)


def _kanban_board_store_key() -> str:
    """Return the shared-store key for the kanban board structure document.

    Holds only board-level data (columns + ``card_order``); each card lives in
    its own ``kanban_card:<id>`` document so the agent edits one task per file
    and writes touch a single row.
    """
    return "kanban_board"


_KANBAN_CARD_KEY_PREFIX = KANBAN_CARD_KEY_PREFIX


def _kanban_card_store_key(card_id: str) -> str:
    """Return the shared-store key for one kanban card document."""
    return f"{_KANBAN_CARD_KEY_PREFIX}{card_id}"


def _append_kanban_card_diff_activity(
    state_reader: monitoring_state.StateReader,
    *,
    previous_card: dict,
    refreshed_card: dict,
    now: str,
) -> None:
    """Record task-activity entries and broadcast them for one card's before/after diff."""
    card_id = str(refreshed_card.get("id") or "").strip()
    if not card_id:
        return
    if previous_card.get("status") != refreshed_card.get("status"):
        _append_task_activity_entry(
            state_reader,
            card_id=card_id,
            kind="status",
            actor="system",
            message=(
                f"Status changed from {previous_card.get('status') or 'unknown'} "
                f"to {refreshed_card.get('status') or 'unknown'}."
            ),
            metadata={
                "from_status": previous_card.get("status"),
                "to_status": refreshed_card.get("status"),
            },
            created_at=now,
        )
    if previous_card.get("column_id") != refreshed_card.get("column_id"):
        _append_task_activity_entry(
            state_reader,
            card_id=card_id,
            kind="move",
            actor="system",
            message=(
                f"Moved from {previous_card.get('column_id') or 'unknown'} "
                f"to {refreshed_card.get('column_id') or 'unknown'}."
            ),
            metadata={
                "from_column_id": previous_card.get("column_id"),
                "to_column_id": refreshed_card.get("column_id"),
            },
            created_at=now,
        )
    if previous_card.get("title") != refreshed_card.get("title"):
        _append_task_activity_entry(
            state_reader,
            card_id=card_id,
            kind="update",
            actor="system",
            message=f"Renamed task to {refreshed_card.get('title') or 'Untitled task'}.",
            metadata={"field": "title"},
            created_at=now,
        )
    if (
        previous_card.get("status") != refreshed_card.get("status")
        or previous_card.get("column_id") != refreshed_card.get("column_id")
        or previous_card.get("title") != refreshed_card.get("title")
    ):
        broadcast_task_activity(card_id)


def _task_content_store_key(card_id: str) -> str:
    """Return the shared-store key for one task body."""
    return f"{KANBAN_TASK_CONTENT_KEY_PREFIX}{card_id}"


def _task_content_public_path(card_id: str) -> str:
    """Return the user-facing relative path for one task body."""
    return f"tasks/{card_id}.md"


def _task_activity_store_key(card_id: str) -> str:
    """Return the shared-store key for task activity."""
    return f"kanban_task_activity:{card_id}"


def _render_task_content_value(value: object) -> str:
    """Normalize stored task-body payloads into plain text."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("content") or "")
    return ""


def _default_task_content(card: dict, columns: list[dict] | None = None) -> str:
    """Build a markdown task brief from the kanban card metadata."""
    column_map = {column["id"]: column for column in columns or _kanban_default_columns()}
    column = column_map.get(str(card.get("column_id") or "backlog"), {})
    summary = str(card.get("summary") or "").strip() or "_No summary yet._"
    description = str(card.get("description") or "").strip() or (
        "_Add the implementation brief, acceptance criteria, or notes here._"
    )
    labels = ", ".join(_normalize_string_list(card.get("labels"))) or "none"
    references = [
        ("Spec", str(card.get("spec_path") or "").strip() or "not linked"),
        ("Branch", str(card.get("branch_name") or "").strip() or "not linked"),
        ("Pull request", str(card.get("pr_url") or "").strip() or "not linked"),
    ]
    reference_lines = "\n".join(f"- {label}: {value}" for label, value in references)
    return "\n".join(
        [
            f"# {str(card.get('title') or 'Untitled task').strip()}",
            "",
            "## Summary",
            "",
            summary,
            "",
            "## Task",
            "",
            description,
            "",
            "## Workflow",
            "",
            f"- Column: {column.get('label') or str(card.get('column_id') or 'backlog')}",
            f"- Status: {str(card.get('status') or 'idle')}",
            f"- Owner: {str(card.get('owner') or 'user')}",
            f"- Agent: {str(card.get('agent_name') or column.get('agent_name') or 'user')}",
            f"- Labels: {labels}",
            "",
            "## References",
            "",
            reference_lines,
        ]
    ).strip()


def _read_task_content(
    state_reader: monitoring_state.StateReader, card: dict
) -> tasks_models.TaskContentResponse:
    """Read a task body from backend state, falling back to derived markdown."""
    card_id = str(card.get("id") or "").strip()
    if not card_id:
        raise HTTPException(status_code=400, detail="Kanban card is missing an id")

    store = storage_access._require_document_store(state_reader)
    updated_at: str | None = None
    content: str | None = None
    document = store.get_document(
        storage_documents.PROGENITOR_STATE_PREFIX,
        _task_content_store_key(card_id),
        None,
    )
    if document is not None:
        content = _render_task_content_value(document.payload)
        if document.updated_at is not None:
            updated_at = document.updated_at.isoformat()

    if content is None:
        content = _default_task_content(card)

    return tasks_models.TaskContentResponse(
        card_id=card_id,
        path=_task_content_public_path(card_id),
        updated_at=updated_at,
        content=content,
    )


def _normalize_task_activity_entries(value: object, card_id: str) -> list[dict[str, Any]]:
    """Normalize persisted task activity entries into a stable API shape."""
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        created_at = str(entry.get("created_at") or utcnow().isoformat())
        normalized.append(
            {
                "id": str(entry.get("id") or f"task_activity_{uuid.uuid4().hex[:12]}"),
                "card_id": card_id,
                "kind": str(entry.get("kind") or "note"),
                "actor": str(entry.get("actor") or "system"),
                "message": str(entry.get("message") or "").strip(),
                "created_at": created_at,
                "metadata": entry.get("metadata")
                if isinstance(entry.get("metadata"), dict)
                else {},
            }
        )
    normalized.sort(key=lambda item: str(item.get("created_at") or ""))
    return [item for item in normalized if item["message"]]


def _read_task_activity_entries(
    state_reader: monitoring_state.StateReader, card_id: str
) -> list[dict[str, Any]]:
    """Read persisted task activity entries."""
    store = storage_access._require_document_store(state_reader)
    document = store.get_document(
        storage_documents.PROGENITOR_STATE_PREFIX,
        _task_activity_store_key(card_id),
        [],
    )
    entries = document.payload if document is not None else []
    return _normalize_task_activity_entries(entries, card_id)


def _write_task_activity_entries(
    state_reader: monitoring_state.StateReader,
    card_id: str,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Persist task activity entries."""
    store = storage_access._require_document_store(state_reader)
    normalized = _normalize_task_activity_entries(entries, card_id)
    store.save_document(
        storage_documents.PROGENITOR_STATE_PREFIX,
        _task_activity_store_key(card_id),
        normalized,
    )
    return normalized


def _append_task_activity_entry(
    state_reader: monitoring_state.StateReader,
    *,
    card_id: str,
    message: str,
    actor: str = "system",
    kind: str = "note",
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> tasks_models.TaskActivityEntryResponse:
    """Append one activity entry to the task timeline."""
    normalized_message = str(message or "").strip()
    if not normalized_message:
        raise HTTPException(status_code=400, detail="Task activity message is required")

    entries = _read_task_activity_entries(state_reader, card_id)
    entry: dict[str, Any] = {
        "id": f"task_activity_{uuid.uuid4().hex[:12]}",
        "card_id": card_id,
        "kind": kind,
        "actor": actor,
        "message": normalized_message,
        "created_at": created_at or utcnow().isoformat(),
        "metadata": metadata or {},
    }
    entries.append(entry)
    _write_task_activity_entries(state_reader, card_id, entries)
    return tasks_models.TaskActivityEntryResponse(**entry)


def _task_activity_response(
    state_reader: monitoring_state.StateReader, card_id: str
) -> tasks_models.TaskActivityResponse:
    """Build a task activity response for one card."""
    items = [
        tasks_models.TaskActivityEntryResponse(**entry)
        for entry in _read_task_activity_entries(state_reader, card_id)
    ]
    return tasks_models.TaskActivityResponse(card_id=card_id, items=items, total=len(items))


def _write_task_content(
    state_reader: monitoring_state.StateReader, card_id: str, content: str
) -> tasks_models.TaskContentResponse:
    """Persist a task body to the shared document store."""
    store = storage_access._require_document_store(state_reader)
    normalized = content.rstrip()
    store.save_document(
        storage_documents.PROGENITOR_STATE_PREFIX,
        _task_content_store_key(card_id),
        {"content": normalized},
    )
    document = store.get_document(
        storage_documents.PROGENITOR_STATE_PREFIX,
        _task_content_store_key(card_id),
        None,
    )
    updated_at = document.updated_at.isoformat() if document and document.updated_at else None

    return tasks_models.TaskContentResponse(
        card_id=card_id,
        path=_task_content_public_path(card_id),
        updated_at=updated_at,
        content=normalized,
    )


def _find_kanban_card(state_reader: monitoring_state.StateReader, card_id: str) -> dict[str, Any]:
    """Look up one kanban card or raise a 404."""
    board = _read_kanban_board(state_reader)
    card = next((item for item in board["cards"] if item["id"] == card_id), None)
    if card is None:
        raise HTTPException(status_code=404, detail="Kanban card not found")
    return cast(dict[str, Any], card)


def _kanban_card_response(
    state_reader: monitoring_state.StateReader, card: dict
) -> tasks_models.KanbanCardResponse:
    """Attach task-body metadata to a kanban card response."""
    task_content = _read_task_content(state_reader, card)
    return tasks_models.KanbanCardResponse(
        **card,
        task_path=task_content.path,
        task_format=task_content.format,
        task_content_updated_at=task_content.updated_at,
    )


def _kanban_board_response(
    state_reader: monitoring_state.StateReader, board: dict | None = None
) -> tasks_models.KanbanBoardResponse:
    """Build the canonical kanban response payload."""
    snapshot = _read_kanban_board(state_reader) if board is None else board
    return tasks_models.KanbanBoardResponse(
        id=snapshot["id"],
        title=snapshot["title"],
        updated_at=snapshot["updated_at"],
        columns=[tasks_models.KanbanColumnResponse(**column) for column in snapshot["columns"]],
        cards=[_kanban_card_response(state_reader, card) for card in snapshot["cards"]],
        card_schema=_kanban_card_schema(snapshot["columns"]),
    )


def _read_kanban_card_doc(store: Any, card_id: str) -> dict | None:
    """Read one persisted card document (the ``kanban_card:<id>`` row)."""
    document = store.get_document(
        storage_documents.PROGENITOR_STATE_PREFIX,
        _kanban_card_store_key(card_id),
        None,
    )
    if document is None or not isinstance(document.payload, dict):
        return None
    return document.payload


def _migrate_inline_cards(store: Any, structure: dict, inline_cards: list) -> list[str]:
    """Split a legacy inline ``cards`` array into per-card documents.

    Older boards stored every card inside the single ``kanban_board`` document.
    The first read after upgrade writes each card to its own ``kanban_card:<id>``
    row, records the ordering in ``card_order``, and drops the inline array.
    """
    card_order: list[str] = []
    for card in inline_cards:
        if not isinstance(card, dict):
            continue
        card_id = str(card.get("id") or "").strip()
        if not card_id or card_id in card_order:
            continue
        store.save_document(
            storage_documents.PROGENITOR_STATE_PREFIX,
            _kanban_card_store_key(card_id),
            card,
        )
        card_order.append(card_id)
    structure["card_order"] = card_order
    structure.pop("cards", None)
    store.save_document(
        storage_documents.PROGENITOR_STATE_PREFIX, _kanban_board_store_key(), structure
    )
    return card_order


def _read_kanban_board(state_reader: monitoring_state.StateReader) -> dict:
    """Assemble the kanban board from its structure + per-card documents.

    The board structure (columns + ``card_order``) is one document under the
    shared ``("progenitor", "state")`` namespace; each card is its own
    ``kanban_card:<id>`` document so the runtime agent edits one task per file
    and writes touch a single row. On first read we seed an empty board, and we
    transparently migrate any legacy inline-``cards`` board into per-card rows.
    """
    store = storage_access._require_document_store(state_reader)
    document = store.get_document(
        storage_documents.PROGENITOR_STATE_PREFIX,
        _kanban_board_store_key(),
        None,
    )
    if document is None:
        normalized = _kanban_default_board_document()
        structure = {k: v for k, v in normalized.items() if k != "cards"}
        structure["card_order"] = []
        store.save_document(
            storage_documents.PROGENITOR_STATE_PREFIX, _kanban_board_store_key(), structure
        )
        return normalized

    structure = dict(document.payload) if isinstance(document.payload, dict) else {}
    inline_cards = structure.get("cards")
    if isinstance(inline_cards, list) and inline_cards and "card_order" not in structure:
        card_order = _migrate_inline_cards(store, structure, inline_cards)
    else:
        order = structure.get("card_order")
        card_order = (
            [str(cid).strip() for cid in order if str(cid).strip()]
            if isinstance(order, list)
            else []
        )

    cards = [card for cid in card_order if (card := _read_kanban_card_doc(store, cid)) is not None]
    assembled = {**structure, "cards": cards}
    assembled.pop("card_order", None)
    return _normalize_kanban_board(assembled)


def _write_kanban_board(state_reader: monitoring_state.StateReader, board: dict) -> dict:
    """Persist the kanban board as a structure document + per-card documents.

    Diff-aware: only cards whose payload actually changed are rewritten, and
    cards dropped from the board have their ``kanban_card:<id>`` row deleted.
    Each touched row fires the shared-store NOTIFY trigger, so a single card
    edit produces a single ``kanban.snapshot`` rebroadcast rather than a storm.
    """
    store = storage_access._require_document_store(state_reader)
    normalized = _normalize_kanban_board(board)
    normalized["updated_at"] = utcnow().isoformat()
    cards = normalized.get("cards", [])

    previous = store.get_document(
        storage_documents.PROGENITOR_STATE_PREFIX, _kanban_board_store_key(), None
    )
    previous_order = []
    if previous is not None and isinstance(previous.payload, dict):
        order = previous.payload.get("card_order")
        if isinstance(order, list):
            previous_order = [str(cid).strip() for cid in order if str(cid).strip()]

    card_order: list[str] = []
    for card in cards:
        card_id = str(card.get("id") or "").strip()
        if not card_id:
            continue
        card_order.append(card_id)
        existing = _read_kanban_card_doc(store, card_id)
        if existing != card:
            store.save_document(
                storage_documents.PROGENITOR_STATE_PREFIX, _kanban_card_store_key(card_id), card
            )

    for stale_id in previous_order:
        if stale_id not in card_order:
            store.delete_document(
                storage_documents.PROGENITOR_STATE_PREFIX, _kanban_card_store_key(stale_id)
            )

    structure = {k: v for k, v in normalized.items() if k != "cards"}
    structure["card_order"] = card_order
    store.save_document(
        storage_documents.PROGENITOR_STATE_PREFIX, _kanban_board_store_key(), structure
    )
    return normalized


async def _record_kanban_card_activity(typed: events_documents.TypedDocumentEvent) -> None:
    """Derive task-activity timeline entries from kanban card updates."""

    event = typed.event
    if typed.document_type is None or typed.document_type.id != "kanban_card":
        return
    if event.op != "updated" or not isinstance(event.before, dict):
        return
    if not isinstance(event.after, dict):
        return
    _append_kanban_card_diff_activity(
        monitoring_state.get_state_reader(),
        previous_card=event.before,
        refreshed_card=event.after,
        now=event.created_at.isoformat(),
    )


async def _sync_kanban_card_order(typed: events_documents.TypedDocumentEvent) -> None:
    """Keep the board's ``card_order`` in step with the card documents.

    The board lists only cards in ``card_order``. The card routes and the
    agent's ``/tasks/`` mount maintain it themselves; this covers every other
    writer (workflow ``document_create``/``document_delete``, extensions), so a
    created card appears on the board and a deleted one leaves no stale id.
    The update carries the card event's origin, keeping run ancestry intact.
    """

    await asyncio.to_thread(_apply_kanban_card_order_event, typed.event)


def _apply_kanban_card_order_event(event: storage_documents.DocumentEvent) -> None:
    if not event.key.startswith(_KANBAN_CARD_KEY_PREFIX) or event.op == "updated":
        return
    card_id = event.key[len(_KANBAN_CARD_KEY_PREFIX) :]
    store = storage_documents.get_default_document_store()
    board = store.get_document(
        storage_documents.PROGENITOR_STATE_PREFIX, _kanban_board_store_key(), None
    )
    order = board.payload.get("card_order") if board and isinstance(board.payload, dict) else None
    listed = isinstance(order, list) and card_id in order
    if listed == (event.op == "created"):
        return

    def update(payload: Any) -> Any:
        structure = dict(payload) if isinstance(payload, dict) else {}
        current = structure.get("card_order")
        ids = [str(cid) for cid in current] if isinstance(current, list) else []
        if event.op == "created" and card_id not in ids:
            ids.append(card_id)
        elif event.op == "deleted":
            ids = [cid for cid in ids if cid != card_id]
        structure["card_order"] = ids
        return structure

    origin = (
        storage_documents.document_write_origin(event.origin)
        if event.origin
        else contextlib.nullcontext()
    )
    with origin:
        store.mutate_document(
            storage_documents.PROGENITOR_STATE_PREFIX, _kanban_board_store_key(), update
        )


def broadcast_kanban_snapshot(
    board: tasks_models.KanbanBoardResponse | None = None, *, request_id: str | None = None
) -> events_contracts.UiEventEnvelope:
    """Broadcast the current Tasks board to /api/ui/stream subscribers."""
    state_reader = monitoring_state.get_state_reader()
    payload = (board or _kanban_board_response(state_reader)).model_dump(mode="json")
    return events_stream.broadcast_ui_event("kanban.snapshot", payload, request_id=request_id)


def broadcast_task_content(
    card_id: str,
    content: tasks_models.TaskContentResponse | None = None,
    *,
    request_id: str | None = None,
) -> events_contracts.UiEventEnvelope:
    """Broadcast the canonical task-body payload for one card."""
    state_reader = monitoring_state.get_state_reader()
    if content is None:
        card = _find_kanban_card(state_reader, card_id)
        content = _read_task_content(state_reader, card)
    return events_stream.broadcast_ui_event(
        "task.content.updated", content.model_dump(mode="json"), request_id=request_id
    )


def broadcast_task_activity(
    card_id: str,
    activity: tasks_models.TaskActivityResponse | None = None,
    *,
    request_id: str | None = None,
) -> events_contracts.UiEventEnvelope:
    """Broadcast the canonical task activity payload for one card."""
    state_reader = monitoring_state.get_state_reader()
    payload = activity or _task_activity_response(state_reader, card_id)
    return events_stream.broadcast_ui_event(
        "task.activity.updated", payload.model_dump(mode="json"), request_id=request_id
    )
