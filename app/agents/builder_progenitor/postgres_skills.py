"""Postgres-backed skill loading for builder_progenitor.

Builder-specific skills (build-agent, git-workflow, task-setup, task-teardown)
used to live on disk under ``data/skills/builder_progenitor/``. They now live
in the external ``tamaw/marketplace`` repo as the ``builder-progenitor``
plugin, installed by Progenitor's plugin reconciler (see
``app/progenitor/plugins/install.py``) straight into the shared Postgres
``store`` table -- the same ``("progenitor", "state")`` namespace,
``skill:<marketplace>__<plugin>/<skill>/SKILL.md`` keys, that Progenitor's own
runtime reads and writes (see ``app/progenitor/content_documents.py``,
``app/progenitor/state/manager.py``).

This module opens its own read-only connection to that same database
(``PROGENITOR_DATABASE_URL``) and never calls ``.setup()``: Progenitor's own
runtime owns schema migration for this table, this agent is a read-only
guest of it, not a co-owner. All Postgres access is async (``AsyncPostgresStore``
over an ``AsyncConnection``) so a skill lookup never blocks the event loop.

The marketplace a plugin was installed from is never hardcoded: the ``plugins``
state document (the same one ``/config/plugins.json`` exposes) is read on every
lookup to find which ``<marketplace>/<plugin>`` key actually provides this
agent's skills, so this module tracks wherever the plugin is really installed
rather than assuming a fixed marketplace name.

Postgres-only, hard failure, no disk fallback: disk-sync from the
marketplace repo was explicitly rejected, so a missing skill or an
unreachable database both raise immediately rather than degrading quietly --
a missing skill means the marketplace plugin providing it needs to be
(re)installed, not that this code should paper over it.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from langgraph.store.postgres.aio import AsyncPostgresStore

STATE_NAMESPACE = ("progenitor", "state")
SKILL_KEY_PREFIX = "skill:"
PLUGINS_DOC_KEY = "plugins"

_store: AsyncPostgresStore | None = None
_conn: Any | None = None
_store_lock = asyncio.Lock()


def _database_url() -> str:
    url = (os.getenv("PROGENITOR_DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "PROGENITOR_DATABASE_URL is not set. builder_progenitor has no disk "
            "fallback for skills -- only Progenitor's shared Postgres store."
        )
    return url


async def ensure_skill_store() -> AsyncPostgresStore:
    """Lazily open (idempotent) the module-level AsyncPostgresStore singleton.

    A single plain connection, not a pool: this agent is a low-throughput
    reader of skill content, not a concurrent writer. Guarded by a lock so
    two concurrent callers racing on first use open exactly one connection.
    """
    global _store, _conn
    if _store is not None:
        return _store
    async with _store_lock:
        if _store is not None:
            return _store
        from psycopg import AsyncConnection
        from psycopg.rows import DictRow, dict_row

        conn = await AsyncConnection[DictRow].connect(
            _database_url(), autocommit=True, prepare_threshold=0, row_factory=dict_row
        )
        _store = AsyncPostgresStore(conn=conn)
        _conn = conn
        return _store


async def close_skill_store() -> None:
    """Close the module-level AsyncPostgresStore singleton, if one was ever opened."""
    global _store, _conn
    if _conn is not None:
        await _conn.close()
    _store = None
    _conn = None


async def resolve_skill_segment(agent_name: str) -> str:
    """Map a package name (e.g. ``builder_progenitor``) to its installed
    marketplace-plugin skill segment (e.g. ``acme-marketplace__builder-progenitor``).

    Looks up the shared ``plugins`` state document to find whichever
    ``<marketplace>/<plugin>`` key actually provides this plugin, rather than
    assuming a fixed marketplace name.
    """
    plugin_name = agent_name.replace("_", "-")
    store = await ensure_skill_store()
    item = await store.aget(STATE_NAMESPACE, PLUGINS_DOC_KEY)
    plugins: dict[str, Any] = {}
    if item is not None and isinstance(item.value, dict):
        payload = item.value.get("payload")
        if isinstance(payload, dict):
            candidate = payload.get("plugins")
            if isinstance(candidate, dict):
                plugins = candidate
    for key in plugins:
        if isinstance(key, str) and key.endswith(f"/{plugin_name}"):
            return key.replace("/", "__", 1)
    raise FileNotFoundError(
        f"No installed plugin provides '{plugin_name}'. Install the marketplace "
        "plugin providing it (see the managing-plugins skill)."
    )


def strip_skill_frontmatter(content: str) -> str:
    """Remove optional markdown frontmatter from SKILL.md files."""
    if not content.startswith("---"):
        return content.strip()
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content.strip()
    return parts[2].strip()


async def _get_skill_content(
    store: AsyncPostgresStore, segment: str, skill_name: str
) -> str | None:
    key = f"{SKILL_KEY_PREFIX}{segment}/{skill_name}/SKILL.md"
    item = await store.aget(STATE_NAMESPACE, key)
    if item is None:
        return None
    value = item.value if isinstance(item.value, dict) else {}
    payload = value.get("payload")
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, str):
            return content
    return None


async def load_skill_context(
    skill_names: list[str],
    *,
    agent_name: str,
    fallback_agents: tuple[str, ...] = (),
) -> str:
    """Load and concatenate skill bodies from Progenitor's shared Postgres store.

    Searches ``agent_name``'s installed marketplace segment first, then each of
    ``fallback_agents`` in order, matching the old disk-based lookup order.
    """
    search_agents: list[str] = []
    for candidate in (agent_name, *fallback_agents):
        if candidate not in search_agents:
            search_agents.append(candidate)

    store = await ensure_skill_store()
    segments: list[str] = []
    for candidate in search_agents:
        try:
            segments.append(await resolve_skill_segment(candidate))
        except FileNotFoundError:
            continue
    if not segments:
        raise FileNotFoundError(
            f"No installed plugin provides skills for any of: {', '.join(search_agents)}. "
            "Install the marketplace plugin providing it (see the managing-plugins skill)."
        )

    sections: list[str] = []
    for skill_name in skill_names:
        content: str | None = None
        searched_keys: list[str] = []
        for segment in segments:
            searched_keys.append(f"{SKILL_KEY_PREFIX}{segment}/{skill_name}/SKILL.md")
            content = await _get_skill_content(store, segment, skill_name)
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
