"""Postgres-backed content documents: instructions, runtime memories, agent
manifests, installed marketplace-plugin skills, and knowledge pages.

Each persists as a document in the shared Postgres ``store`` table (the same
one used for ``mcp.json``/``schedule.json``/etc, see ``state/manager.py``),
keyed by kind + relative path.

Instructions are seeded from the git-tracked ``data/instructions/`` directory
(baked into the container image), see ``sync_instruction_documents``.
Top-level files belong to the logical ``progenitor/`` instruction group;
explicit subdirectories retain their group for custom instruction sources.
Existing documents remain user-owned: boot never overwrites edited instructions
or deletes documents removed from the source tree. The one exception is the
legacy ``progenitor/genesis.md`` system prompt, which boot moves (edits
included) to ``progenitor/SYSTEM.md`` before seeding.

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

from src.progenitor.documents.kinds import (
    AGENT_DOCUMENT_KEY_PREFIX,
    ARTIFACT_DOCUMENT_KEY_PREFIX,
    CANVAS_DOCUMENT_KEY_PREFIX,
    INSTRUCTION_DOCUMENT_KEY_PREFIX,
    KNOWLEDGE_PAGE_KEY_PREFIX,
    MEMORY_DOCUMENT_KEY_PREFIX,
    SKILL_DOCUMENT_KEY_PREFIX,
)
from src.progenitor.instructions.documents import RUNTIME_INSTRUCTION_GROUP, SYSTEM_INSTRUCTION
from src.progenitor.instructions.templates import parse_instruction_template

logger = logging.getLogger(__name__)

# Stored path of the system prompt before it was renamed to SYSTEM_INSTRUCTION.
# Only the boot migration reads it; nothing else resolves this path.
_LEGACY_SYSTEM_INSTRUCTION = "progenitor/genesis.md"

_KIND_PREFIXES: dict[str, str] = {
    "instruction": INSTRUCTION_DOCUMENT_KEY_PREFIX,
    "skill": SKILL_DOCUMENT_KEY_PREFIX,
    "memory": MEMORY_DOCUMENT_KEY_PREFIX,
    "agent": AGENT_DOCUMENT_KEY_PREFIX,
    "knowledge": KNOWLEDGE_PAGE_KEY_PREFIX,
    "canvas": CANVAS_DOCUMENT_KEY_PREFIX,
    "artifact": ARTIFACT_DOCUMENT_KEY_PREFIX,
}


def content_document_prefix(kind: str) -> str:
    """Return the store key prefix for one content-document kind."""
    try:
        return _KIND_PREFIXES[kind]
    except KeyError as exc:
        raise ValueError(f"Unknown content document kind: {kind!r}") from exc


def content_document_key(kind: str, relative_path: str) -> str:
    """Build the store key for one content document (e.g. ``instruction:progenitor/SYSTEM.md``)."""
    return f"{content_document_prefix(kind)}{relative_path}"


async def _migrate_legacy_system_instruction(state_manager: Any, stored: dict[str, str]) -> None:
    """Move a stored ``progenitor/genesis.md`` to ``SYSTEM_INSTRUCTION``, keeping edits.

    Runs before seeding so an edited prompt becomes ``SYSTEM.md`` rather than
    being shadowed by a fresh copy from disk. Writing the new key before deleting
    the old one keeps an interrupted move recoverable; once the legacy key is
    gone this is a no-op. If both keys hold different text, ``SYSTEM.md`` stays
    authoritative and the legacy document is left for the user to remove.
    ``stored`` is updated in place.
    """
    legacy = stored.get(_LEGACY_SYSTEM_INSTRUCTION)
    if legacy is None:
        return
    current = stored.get(SYSTEM_INSTRUCTION)
    if current is None:
        await state_manager.save_content_document("instruction", SYSTEM_INSTRUCTION, legacy)
        stored[SYSTEM_INSTRUCTION] = legacy
    elif current != legacy:
        logger.warning(
            "Both %s and legacy %s are stored with different content; using %s and "
            "keeping %s unread",
            SYSTEM_INSTRUCTION,
            _LEGACY_SYSTEM_INSTRUCTION,
            SYSTEM_INSTRUCTION,
            _LEGACY_SYSTEM_INSTRUCTION,
        )
        return
    await state_manager.delete_content_document("instruction", _LEGACY_SYSTEM_INSTRUCTION)
    del stored[_LEGACY_SYSTEM_INSTRUCTION]
    logger.info("Moved stored instruction %s to %s", _LEGACY_SYSTEM_INSTRUCTION, SYSTEM_INSTRUCTION)


async def sync_instruction_documents(state_manager: Any, source_dir: Path | str) -> None:
    """Seed missing checked-in instructions without changing stored user content."""
    root = Path(source_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Instructions directory not found: {root}")
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root).as_posix()
        document_path = (
            f"{RUNTIME_INSTRUCTION_GROUP}/{relative_path}"
            if "/" not in relative_path
            else relative_path
        )
        if document_path in files:
            raise ValueError(f"Duplicate instruction source for '{document_path}': {path}")
        content = path.read_text(encoding="utf-8")
        try:
            parse_instruction_template(content)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        files[document_path] = content
    stored = dict(await state_manager.list_content_documents("instruction"))
    await _migrate_legacy_system_instruction(state_manager, stored)
    written = 0
    for relative_path, content in files.items():
        if relative_path in stored:
            continue
        await state_manager.save_content_document("instruction", relative_path, content)
        written += 1
    if written:
        logger.info("Seeded instructions from %s: %d written", root, written)
