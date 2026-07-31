"""Postgres-backed skill loading for builder_agent.

Builder-specific skills (build-agent, git-workflow) used to live on disk
under ``data/skills/builder_agent/``. They now live in the external
``tamaw/marketplace`` repo as the ``builder-agent`` plugin, installed by
Progenitor's plugin reconciler (see ``app/progenitor/plugins/install.py``)
straight into the shared Postgres ``store`` table -- the same
``("progenitor", "state")`` namespace, ``skill:<marketplace>__<plugin>/<skill>/SKILL.md``
keys, that Progenitor's own runtime reads and writes (see
``app/progenitor/content_documents.py``, ``app/progenitor/state/manager.py``).

This module opens its own read-only connection to that same database
(``PROGENITOR_DATABASE_URL``) and never calls ``.setup()``: Progenitor's own
runtime owns schema migration for this table, this agent is a read-only
guest of it, not a co-owner.

Postgres-only, hard failure, no disk fallback: disk-sync from the
marketplace repo was explicitly rejected, so a missing skill or an
unreachable database both raise immediately rather than degrading quietly --
a missing skill means the marketplace plugin providing it needs to be
(re)installed, not that this code should paper over it.
"""

from __future__ import annotations

import os
from typing import Any

from langgraph.store.postgres import PostgresStore

STATE_NAMESPACE = ("progenitor", "state")
SKILL_KEY_PREFIX = "skill:"
DEFAULT_MARKETPLACE = os.getenv("BUILDER_SKILLS_MARKETPLACE", "tamaw-marketplace")

_store: PostgresStore | None = None
_conn: Any | None = None


def _database_url() -> str:
    url = (os.getenv("PROGENITOR_DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "PROGENITOR_DATABASE_URL is not set. builder_agent has no disk "
            "fallback for skills -- only Progenitor's shared Postgres store."
        )
    return url


def ensure_skill_store() -> PostgresStore:
    """Lazily open (idempotent) the module-level PostgresStore singleton.

    A single plain connection, not a pool: this agent is a low-throughput
    reader of skill content, not a concurrent writer.
    """
    global _store, _conn
    if _store is not None:
        return _store
    from psycopg import Connection
    from psycopg.rows import dict_row

    conn = Connection.connect(
        _database_url(), autocommit=True, prepare_threshold=0, row_factory=dict_row
    )
    _store = PostgresStore(conn=conn)
    _conn = conn
    return _store


def close_skill_store() -> None:
    """Close the module-level PostgresStore singleton, if one was ever opened."""
    global _store, _conn
    if _conn is not None:
        _conn.close()
    _store = None
    _conn = None


def resolve_skill_segment(agent_name: str) -> str:
    """Map a package name (e.g. ``builder_agent``) to its installed
    marketplace-plugin skill segment (e.g. ``tamaw-marketplace__builder-agent``)."""
    return f"{DEFAULT_MARKETPLACE}__{agent_name.replace('_', '-')}"


def strip_skill_frontmatter(content: str) -> str:
    """Remove optional markdown frontmatter from SKILL.md files."""
    if not content.startswith("---"):
        return content.strip()
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content.strip()
    return parts[2].strip()


def _get_skill_content(store: PostgresStore, segment: str, skill_name: str) -> str | None:
    key = f"{SKILL_KEY_PREFIX}{segment}/{skill_name}/SKILL.md"
    item = store.get(STATE_NAMESPACE, key)
    if item is None:
        return None
    value = item.value if isinstance(item.value, dict) else {}
    payload = value.get("payload")
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, str):
            return content
    return None


def load_skill_context(
    skill_names: list[str],
    *,
    agent_name: str,
    fallback_agents: tuple[str, ...] = (),
) -> str:
    """Load and concatenate skill bodies from Progenitor's shared Postgres store.

    Searches ``agent_name``'s marketplace segment first, then each of
    ``fallback_agents`` in order, matching the old disk-based lookup order.
    """
    search_agents: list[str] = []
    for candidate in (agent_name, *fallback_agents):
        if candidate not in search_agents:
            search_agents.append(candidate)
    segments = [resolve_skill_segment(candidate) for candidate in search_agents]

    store = ensure_skill_store()
    sections: list[str] = []
    for skill_name in skill_names:
        content: str | None = None
        searched_keys: list[str] = []
        for segment in segments:
            searched_keys.append(f"{SKILL_KEY_PREFIX}{segment}/{skill_name}/SKILL.md")
            content = _get_skill_content(store, segment, skill_name)
            if content is not None:
                break
        if content is None:
            raise FileNotFoundError(
                f"Skill '{skill_name}' not found in Postgres. Searched keys: "
                f"{', '.join(searched_keys)}. Ensure the marketplace plugin "
                "providing it is installed (see the managing-plugins skill)."
            )
        body = strip_skill_frontmatter(content)
        if body:
            sections.append(body)
    return "\n\n".join(sections)
