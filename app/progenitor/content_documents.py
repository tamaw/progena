"""Postgres-backed content documents: instructions, runtime memories, agent
manifests, installed marketplace-plugin skills, and knowledge pages.

Each persists as a document in the shared Postgres ``store`` table (the same
one used for ``mcp.json``/``schedule.json``/etc, see ``state/manager.py``),
keyed by kind + relative path.

Instructions are synced from the git-tracked ``data/instructions/`` directory
(baked into the container image) on every runtime boot, see
``sync_instruction_documents``. The ``progenitor`` group is read by the runtime
from Postgres and edited through the Instructions page, so a stored copy is
never overwritten; every other group is bundled with its A2A agent, which reads
its own files from disk, so the stored copy mirrors the file.

Native agent manifests remain read-only files in ``data/agents/objects/``.
They are not imported into Postgres; user and plugin agents use documents.

Progenitor's own skills (``data/skills/``) are never stored here -- they're a
plain read-only disk mount (see ``agent/backends.py``), the same pattern as
``data/schemas/``, since that content is fully static. The ``"skill"`` kind is
still used for installed *marketplace-plugin* skills, fetched at runtime and
written directly by ``plugins/install.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .document_kinds import (
    AGENT_DOCUMENT_KEY_PREFIX,
    INSTRUCTION_DOCUMENT_KEY_PREFIX,
    KNOWLEDGE_PAGE_KEY_PREFIX,
    MEMORY_DOCUMENT_KEY_PREFIX,
    SKILL_DOCUMENT_KEY_PREFIX,
)
from .document_kinds import (
    AGENT_SELF_EDIT_TOUCH_KEY_PREFIX as AGENT_SELF_EDIT_TOUCH_KEY_PREFIX,
)
from .instructions.documents import is_runtime_instruction, parse_instruction

logger = logging.getLogger(__name__)

# AGENT_SELF_EDIT_TOUCH_KEY_PREFIX: not a content-document kind itself -- a
# legacy self-edit notification key. All manifests now persist as agent:
# documents, whose writes directly trigger Postgres NOTIFY. Kept exported
# for compatibility with older runtime writers and notification consumers.

_KIND_PREFIXES: dict[str, str] = {
    "instruction": INSTRUCTION_DOCUMENT_KEY_PREFIX,
    "skill": SKILL_DOCUMENT_KEY_PREFIX,
    "memory": MEMORY_DOCUMENT_KEY_PREFIX,
    "agent": AGENT_DOCUMENT_KEY_PREFIX,
    "knowledge": KNOWLEDGE_PAGE_KEY_PREFIX,
}


def content_document_prefix(kind: str) -> str:
    """Return the store key prefix for one content-document kind."""
    try:
        return _KIND_PREFIXES[kind]
    except KeyError as exc:
        raise ValueError(f"Unknown content document kind: {kind!r}") from exc


def content_document_key(kind: str, relative_path: str) -> str:
    """Build the store key for one content document (e.g. ``instruction:progenitor/genesis.md``)."""
    return f"{content_document_prefix(kind)}{relative_path}"


async def sync_instruction_documents(state_manager: Any, source_dir: Path | str) -> None:
    """Bring the stored instructions in line with ``source_dir`` (``data/instructions/``).

    Runtime instructions (the ``progenitor`` group) are added when missing and
    otherwise left alone, since they are edited in place. Bundled instructions
    mirror their file. Stored instructions with no file are removed.
    """
    root = Path(source_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Instructions directory not found: {root}")
    files = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.md"))
        if path.is_file()
    }
    for relative_path, content in files.items():
        try:
            parse_instruction(content)
        except ValueError as exc:
            raise ValueError(f"{root / relative_path}: {exc}") from exc
    stored = dict(await state_manager.list_content_documents("instruction"))
    written = 0
    for relative_path, content in files.items():
        current = stored.get(relative_path)
        if current == content or (current is not None and is_runtime_instruction(relative_path)):
            continue
        await state_manager.save_content_document("instruction", relative_path, content)
        written += 1
    removed = [path for path in stored if path not in files]
    for relative_path in removed:
        await state_manager.delete_content_document("instruction", relative_path)
    if written or removed:
        logger.info(
            "Synced instructions from %s: %d written, %d removed", root, written, len(removed)
        )
