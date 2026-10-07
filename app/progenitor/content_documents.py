"""Postgres-backed content documents: prompts, runtime memories, agent
manifests, and installed marketplace-plugin skills.

Prompts and memories used to be plain files under ``data/{prompts,memories}/``,
bind-mounted from the host into both the ``progenitor`` and ``backend``
containers so edits made through the backend UI were visible to the agent
without a rebuild. That required every deployment target to have those
directories seeded and kept in sync outside of git.

They now persist as documents in the shared Postgres ``store`` table (the
same one used for ``mcp.json``/``schedule.json``/etc, see
``state/manager.py``), keyed by kind + relative path. The only remaining
prompt disk dependency is the one-time seed: the git-tracked ``data/prompts/``
directory is baked into the container image (see ``docker/*/Dockerfile``) and
used to populate Postgres on first boot, when no prompt documents exist yet.
After that, the database is authoritative and the container never reads that
directory again.

Native agent manifests remain read-only files in ``data/agents/objects/``.
They are not imported into Postgres; user and plugin agents use documents.

Progenitor's own skills (``data/skills/``) are never seeded here -- they're a
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
    MEMORY_DOCUMENT_KEY_PREFIX,
    PROMPT_DOCUMENT_KEY_PREFIX,
    SKILL_DOCUMENT_KEY_PREFIX,
)
from .document_kinds import (
    AGENT_SELF_EDIT_TOUCH_KEY_PREFIX as AGENT_SELF_EDIT_TOUCH_KEY_PREFIX,
)

logger = logging.getLogger(__name__)

# AGENT_SELF_EDIT_TOUCH_KEY_PREFIX: not a content-document kind itself -- a
# legacy self-edit notification key. All manifests now persist as agent:
# documents, whose writes directly trigger Postgres NOTIFY. Kept exported
# for compatibility with older runtime writers and notification consumers.

_KIND_PREFIXES: dict[str, str] = {
    "prompt": PROMPT_DOCUMENT_KEY_PREFIX,
    "skill": SKILL_DOCUMENT_KEY_PREFIX,
    "memory": MEMORY_DOCUMENT_KEY_PREFIX,
    "agent": AGENT_DOCUMENT_KEY_PREFIX,
}


def content_document_prefix(kind: str) -> str:
    """Return the store key prefix for one content-document kind."""
    try:
        return _KIND_PREFIXES[kind]
    except KeyError as exc:
        raise ValueError(f"Unknown content document kind: {kind!r}") from exc


def content_document_key(kind: str, relative_path: str) -> str:
    """Build the store key for one content document (e.g. ``prompt:progenitor/system.md``)."""
    return f"{content_document_prefix(kind)}{relative_path}"


async def ensure_content_documents_seeded(
    state_manager: Any,
    kind: str,
    source_dir: Path | str,
) -> None:
    """Bulk-load ``source_dir``'s ``*.md`` files into Postgres if none exist yet.

    Idempotent and cheap to call on every startup: it only touches disk and
    writes when the store has zero documents of this kind (fresh database).
    Once seeded, this is a no-op forever after — the database is the source
    of truth and further edits happen through the virtual mount / backend
    API, never by re-reading disk.
    """
    existing = await state_manager.list_content_documents(kind)
    if existing:
        return

    root = Path(source_dir)
    if not root.is_dir():
        logger.warning("Skipping %s seed: source directory missing: %s", kind, root)
        return

    seeded = 0
    for file_path in sorted(root.rglob("*.md")):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(root).as_posix()
        content = file_path.read_text(encoding="utf-8")
        await state_manager.save_content_document(kind, relative_path, content)
        seeded += 1

    if seeded:
        logger.info("Seeded %d %s document(s) from %s into Postgres", seeded, kind, root)
