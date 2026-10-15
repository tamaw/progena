"""Instruction documents for the Instructions page.

Instructions are ``instruction:<group>/<file>.md`` store documents synced from
``data/instructions/`` by the runtime (see ``content_documents``). The
``progenitor`` group is read by the runtime and editable here; every other
group is bundled with its A2A agent, which reads its own files from disk, so
those are read-only. Every write is captured in ``instruction_revisions``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from app.progenitor.content_documents import content_document_key, content_document_prefix
from app.progenitor.instructions import (
    instruction_group,
    is_runtime_instruction,
    parse_instruction,
)

from .document_store import (
    PROGENITOR_STATE_PREFIX,
    BackendDocumentStore,
    StoredDocument,
    get_default_document_store,
)

_KIND = "instruction"


class ReadOnlyInstructionError(PermissionError):
    """Raised when a bundled (agent-owned) instruction is edited."""


def _content(document: StoredDocument | None) -> str | None:
    payload = document.payload if document is not None else None
    if isinstance(payload, dict) and isinstance(payload.get("content"), str):
        return payload["content"]
    return None


def _summary(path: str, content: str, updated_at: datetime | None) -> dict[str, Any]:
    front_matter, _ = parse_instruction(content)
    return {
        "path": path,
        "group": instruction_group(path),
        "title": front_matter["title"],
        "description": front_matter["description"],
        "variables": list(front_matter.get("variables", [])),
        "editable": is_runtime_instruction(path),
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def _detail(path: str, content: str, updated_at: datetime | None) -> dict[str, Any]:
    front_matter, body = parse_instruction(content)
    return {
        **_summary(path, content, updated_at),
        "content": content,
        "front_matter": front_matter,
        "body": body,
        "revision_id": revision_id(content),
    }


def revision_id(content: str) -> str:
    """The id ``instruction_revisions`` gives this exact content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def list_instructions(store: BackendDocumentStore | None = None) -> list[dict[str, Any]]:
    """Summaries of every stored instruction, sorted by path."""
    store = store or get_default_document_store()
    prefix = content_document_prefix(_KIND)
    summaries = []
    for key, document in store.list_documents(PROGENITOR_STATE_PREFIX, prefix):
        content = _content(document)
        if content is not None:
            summaries.append(_summary(key[len(prefix) :], content, document.updated_at))
    return sorted(summaries, key=lambda summary: summary["path"])


def get_instruction(path: str, store: BackendDocumentStore | None = None) -> dict[str, Any]:
    """The full instruction; raises ``FileNotFoundError`` when it does not exist."""
    store = store or get_default_document_store()
    document = store.get_document(PROGENITOR_STATE_PREFIX, content_document_key(_KIND, path), None)
    content = _content(document)
    if document is None or content is None:
        raise FileNotFoundError(path)
    return _detail(path, content, document.updated_at)


def save_instruction(
    path: str, content: str, store: BackendDocumentStore | None = None
) -> dict[str, Any]:
    """Validate and store a new version of an editable instruction."""
    store = store or get_default_document_store()
    current = get_instruction(path, store)
    if not current["editable"]:
        raise ReadOnlyInstructionError(
            f"'{path}' is bundled with {current['group']}, which reads it from disk; "
            "change it in data/instructions/ instead"
        )
    front_matter, _ = parse_instruction(content)
    variables = sorted(front_matter.get("variables", []))
    if variables != sorted(current["variables"]):
        raise ValueError(
            "variables are fixed by the code that fills them in; keep 'variables: ["
            + ", ".join(sorted(current["variables"]))
            + "]'"
        )
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        content_document_key(_KIND, path),
        {"content": content, "updated_at": datetime.now(UTC).isoformat()},
    )
    return get_instruction(path, store)


def list_revisions(path: str, store: BackendDocumentStore | None = None) -> list[dict[str, Any]]:
    """Captured revisions of one instruction, newest first, marking the live one."""
    store = store or get_default_document_store()
    live = get_instruction(path, store)["revision_id"]
    return [
        {
            "revision_id": summary.revision_id,
            "captured_at": summary.captured_at,
            "current": summary.revision_id == live,
        }
        for summary in store.list_instruction_revisions(path)
    ]


def get_revision(
    path: str, revision: str, store: BackendDocumentStore | None = None
) -> dict[str, Any]:
    """One captured revision; raises ``FileNotFoundError`` when it does not exist."""
    store = store or get_default_document_store()
    content = store.get_instruction_revision(path, revision)
    if content is None:
        raise FileNotFoundError(f"{path}@{revision}")
    front_matter, body = parse_instruction(content)
    return {
        "path": path,
        "revision_id": revision,
        "content": content,
        "front_matter": front_matter,
        "body": body,
    }


def restore_revision(
    path: str, revision: str, store: BackendDocumentStore | None = None
) -> dict[str, Any]:
    """Store a captured revision's content as the live instruction."""
    store = store or get_default_document_store()
    return save_instruction(path, get_revision(path, revision, store)["content"], store)
