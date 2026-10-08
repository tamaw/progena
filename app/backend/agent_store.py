"""Backend-owned agent storage.

Agent catalog metadata (name, role, status, etc.) is the backend's own data, so
it lives in a separate database on the *same* Postgres server as the
Progenitor runtime (only the database name differs). The store is seeded from
routine definitions. Manifest-backed object agents and the shared Postgres
``registry`` document supply the other catalog resources; live runtime status
is merged in at read time by the route layer.

An agent's *editable routine graph* is not backend-owned data, though: it is
a document in the Progenitor runtime's own document store (the same Postgres
``store`` table workflow definitions live in), keyed ``routine:<agent_id>``
(see ``app.progenitor.routines.store``). This gives the agent the same
read/write/edit experience over routine graphs as it has over workflow
definitions (mounted ``/routines/<agent_id>.json`` by
``app.progenitor.agent.state_backend.RoutineDirectoryBackend``), and lets the
Agents page and the agent edit the exact same document. Only graph-editing
bookkeeping that isn't part of the routine schema itself (``status``,
``summary``) stays in the backend's own ``agent_graph`` table.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.progenitor.agent.object_agent_manifest import (
    discover_native_object_agents,
    parse_front_matter,
    parse_object_agent_manifest,
)
from app.progenitor.content_documents import AGENT_DOCUMENT_KEY_PREFIX, content_document_key
from app.progenitor.routines.store import (
    ROUTINE_GRAPH_KEY_PREFIX,
    default_routine_graph,
    routine_graph_key,
)
from app.progenitor.utils import utcnow_isoformat

from .document_store import PROGENITOR_STATE_PREFIX, BackendDocumentStore

logger = logging.getLogger(__name__)

# Accent palette reused for agent avatars/name colour. Chosen deterministically
# per agent id so colours stay stable across restarts.
_AGENT_COLORS = [
    "#2f6ff6",
    "#22a06b",
    "#a78bfa",
    "#c98e5a",
    "#58a6ff",
    "#ef6c2a",
    "#6f86c4",
]

_EMPTY_METRICS = {
    "invocations": "0",
    "sessions": "0",
    "tasks": "0",
    "memories": "0",
}

# Default graph-editing bookkeeping (``agent_graph.status``/``.summary``) for a
# freshly seeded subagent. The routine graph document itself (nodes/entry/edges)
# is seeded separately via ``app.progenitor.routines.store.default_routine_graph``
# into the shared document store -- see ``AgentStore.ensure_graph``.
DEFAULT_AGENT_GRAPH_STATUS: dict[str, Any] = {
    "status": "Draft",
    "summary": "A single step. Edit this graph to build out the routine.",
}

# Progenitor's own skills live on disk at ``data/skills/<name>/SKILL.md``,
# read-only and baked into the image (see ``app.progenitor.content_documents``).
_NATIVE_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "data" / "skills"
_NATIVE_OBJECT_AGENTS_ROOT = (
    Path(os.getenv("PROGENITOR_DATA_DIR", str(Path(__file__).resolve().parents[2] / "data")))
    / "agents"
    / "objects"
)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "agent"


def _first_sentence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    head = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return head.rstrip(".")


def _color_for(agent_id: str) -> str:
    return _AGENT_COLORS[sum(ord(ch) for ch in agent_id) % len(_AGENT_COLORS)]


def _is_acp_registry_entry(info: Any) -> bool:
    transport = str(getattr(info, "transport", "") or "").strip().lower()
    if transport == "acp":
        return True

    kind = str(getattr(info, "kind", "") or "").strip().lower()
    if kind == "acp_agent":
        return True

    acp_cfg = getattr(info, "acp", None)
    return isinstance(acp_cfg, dict) and bool(acp_cfg)


def _empty_details(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    details: dict[str, Any] = {
        "tools": [],
        "tasks": [],
        "metrics": dict(_EMPTY_METRICS),
    }
    if extra:
        details.update(extra)
    return details


def ensure_database_exists(admin_url: str, db_name: str) -> None:
    """Create ``db_name`` on the shared Postgres server if it does not exist.

    ``admin_url`` must point at an existing, connectable database on the same
    server (the Progenitor runtime database). ``CREATE DATABASE`` cannot run in a
    transaction, so the admin connection uses autocommit.
    """
    if not admin_url or not db_name:
        raise ValueError("admin_url and db_name are required to ensure the backend database")
    with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        if cur.fetchone() is None:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
            logger.info("Created backend database %r", db_name)


class AgentStore:
    """Synchronous Postgres client for backend-owned agent data."""

    def __init__(self, database_url: str, document_store: BackendDocumentStore) -> None:
        self._pool = ConnectionPool(
            conninfo=database_url,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=False,
        )
        self._document_store = document_store

    def open(self) -> None:
        self._pool.open()
        self._ensure_tables()

    def close(self) -> None:
        self._pool.close()

    def _ensure_tables(self) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    handle TEXT,
                    display_group TEXT,
                    color TEXT,
                    model TEXT,
                    role TEXT,
                    version TEXT,
                    summary TEXT,
                    status TEXT NOT NULL DEFAULT 'draft',
                    system_prompt TEXT,
                    details JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_graph (
                    agent_id TEXT PRIMARY KEY REFERENCES agent(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'Draft',
                    summary TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    def count_agents(self) -> int:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM agent")
            row = cast(dict[str, Any] | None, cur.fetchone())
        return int(row["n"]) if row else 0

    def list_agents(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM agent
                ORDER BY display_group NULLS LAST, name
                """
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [
            _row_to_agent(row)
            for row in rows
            if row.get("kind") != "routine" or self.get_routine_document(row["id"]) is not None
        ]

    def list_routine_catalog_ids(self) -> list[str]:
        """Return raw routine row IDs, including rows awaiting document cleanup."""
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM agent WHERE kind = 'routine' ORDER BY id")
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [row["id"] for row in rows]

    def _iter_object_agent_locations(
        self,
    ) -> list[tuple[str, str, str, Any, str | None]]:
        """Yield ``(source, name, location, manifest, raw_content)`` for every
        manifest-backed object agent, native-first so a native manifest always
        wins a name collision (mirrors the old ``list_object_agents`` dedup
        rule). Native locations are read-only files; other locations are
        Postgres document relative paths (``<owner>/<slug>/SYSTEM.md``).
        """
        entries: list[tuple[str, str, str, Any, str | None]] = []
        known_names: set[str] = set()
        for source in discover_native_object_agents(_NATIVE_OBJECT_AGENTS_ROOT):
            if source.name in known_names:
                continue
            assert source.file_path is not None
            known_names.add(source.name)
            entries.append(
                (
                    "native",
                    source.name,
                    str(source.file_path),
                    source.manifest,
                    source.file_path.read_text(encoding="utf-8"),
                )
            )
        documents = self._document_store.list_documents(
            PROGENITOR_STATE_PREFIX,
            AGENT_DOCUMENT_KEY_PREFIX,
        )
        for key, document in sorted(
            documents,
            key=lambda row: (not row[0].startswith(f"{AGENT_DOCUMENT_KEY_PREFIX}native/"), row[0]),
        ):
            relative_path = key.removeprefix(AGENT_DOCUMENT_KEY_PREFIX)
            parts = relative_path.split("/")
            if len(parts) != 3 or parts[-1] != "SYSTEM.md" or parts[0] == "native":
                continue
            payload = document.payload
            if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
                logger.warning("Skipping malformed object agent document %s", relative_path)
                continue
            try:
                manifest = parse_object_agent_manifest(
                    payload["content"], source=f"agent:{relative_path}"
                )
            except ValueError as exc:
                logger.warning("Skipping invalid object agent document %s: %s", relative_path, exc)
                continue
            if manifest.name in known_names:
                logger.warning(
                    "Skipping object agent %r from %s because a native agent owns the name",
                    manifest.name,
                    relative_path,
                )
                continue
            known_names.add(manifest.name)
            source = "marketplace" if "__" in parts[0] else "user"
            entries.append((source, manifest.name, relative_path, manifest, payload["content"]))
        return entries

    def list_object_agents(self) -> list[dict[str, Any]]:
        """Read manifest-backed object agents without copying them into this DB."""
        records = [
            _object_agent_profile(manifest, source=source, raw_content=raw_content)
            for source, _name, _location, manifest, raw_content in self._iter_object_agent_locations()
        ]
        return sorted(records, key=lambda record: str(record["name"]).lower())

    def get_object_agent(self, agent_id: str) -> dict[str, Any] | None:
        return next(
            (record for record in self.list_object_agents() if record["id"] == agent_id),
            None,
        )

    def save_user_object_agent(self, content: str) -> dict[str, Any]:
        """Persist one author-created ``SYSTEM.md`` as an agent content document."""
        manifest = parse_object_agent_manifest(content, source="user object agent")
        for record in self.list_object_agents():
            if record["name"] == manifest.name and record["card"].get("source") != "user":
                raise ValueError(
                    f"object agent name {manifest.name!r} is already owned by {record['card']['source']}"
                )
        relative_path = f"user/{_slug(manifest.name)}/SYSTEM.md"
        self._document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            content_document_key("agent", relative_path),
            {"content": content, "updated_at": utcnow_isoformat()},
        )
        return _object_agent_profile(manifest, source="user", raw_content=content)

    def update_object_agent(self, agent_id: str, content: str) -> dict[str, Any]:
        """Update any manifest-backed object agent's literal ``SYSTEM.md`` text.

        User and marketplace renames move the Postgres document. Bundled
        native definitions cannot be edited through the API.
        """
        entries = self._iter_object_agent_locations()
        match = next((entry for entry in entries if f"object-{_slug(entry[1])}" == agent_id), None)
        if match is None:
            raise LookupError(f"object agent {agent_id!r} not found")
        source, _old_name, location, _old_manifest, _old_raw = match
        if source == "native":
            raise ValueError("native object agent manifests are read-only")
        manifest = parse_object_agent_manifest(content, source="object agent")
        new_id = f"object-{_slug(manifest.name)}"
        if new_id != agent_id and any(f"object-{_slug(entry[1])}" == new_id for entry in entries):
            raise ValueError(f"object agent name {manifest.name!r} is already in use")
        parts = location.split("/")
        new_relative_path = f"{parts[0]}/{_slug(manifest.name)}/SYSTEM.md"
        if new_relative_path != location:
            self._document_store.delete_document(
                PROGENITOR_STATE_PREFIX, content_document_key("agent", location)
            )
        self._document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            content_document_key("agent", new_relative_path),
            {"content": content, "updated_at": utcnow_isoformat()},
        )
        return _object_agent_profile(manifest, source=source, raw_content=content)

    def delete_user_object_agent(self, agent_id: str) -> bool:
        """Delete an author-created (``source == "user"``) object agent's
        ``SYSTEM.md`` document. Native agents are bundled sources
        and marketplace agents are plugin-managed (Plugins >
        Uninstall) -- neither is deletable here. Returns False when the
        agent id has no match at all; raises when it matches but is not
        ``user``-sourced, so the route layer can tell "not found" from
        "not allowed" apart.
        """
        entries = self._iter_object_agent_locations()
        match = next((entry for entry in entries if f"object-{_slug(entry[1])}" == agent_id), None)
        if match is None:
            return False
        source, _name, location, _manifest, _raw = match
        if source != "user":
            raise ValueError(
                f"object agent {agent_id!r} is a {source} agent and cannot be deleted here"
            )
        self._document_store.delete_document(
            PROGENITOR_STATE_PREFIX, content_document_key("agent", cast(str, location))
        )
        return True

    def resolve_skill(self, skill_path: str) -> dict[str, Any] | None:
        """Resolve one skill's ``SKILL.md`` by its manifest ``skills:`` entry.

        ``skill_path`` is the same absolute virtual-mount path stored in a
        manifest's ``skills`` list and read by the DeepAgent runtime (see
        ``app.progenitor.agent.backends``): ``/skills/<name>`` for one of
        Progenitor's own native skills (``data/skills/<name>/SKILL.md``,
        read-only, baked into the image), or
        ``/skills/<marketplace>__<plugin>/<name>`` for an installed
        marketplace-plugin skill (Postgres document store).
        """
        segments = skill_path.removeprefix("/skills/").split("/")
        if len(segments) == 1:
            name = segments[0]
            native_path = _NATIVE_SKILLS_ROOT / name / "SKILL.md"
            if not native_path.is_file():
                return None
            return _skill_profile(
                skill_path,
                name=name,
                source="native",
                location=None,
                content=native_path.read_text(encoding="utf-8"),
            )
        if len(segments) == 2:
            plugin_segment, name = segments
            relative_path = f"{plugin_segment}/{name}/SKILL.md"
            document = self._document_store.get_document(
                PROGENITOR_STATE_PREFIX, content_document_key("skill", relative_path)
            )
            payload = document.payload if document is not None else None
            if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
                return None
            return _skill_profile(
                skill_path,
                name=name,
                source="marketplace",
                location=relative_path,
                content=payload["content"],
            )
        return None

    def update_skill(self, skill_path: str, content: str) -> dict[str, Any]:
        """Update one skill's ``SKILL.md`` text, matching ``update_object_agent``.

        ``native`` skills are repo files under ``data/skills/<name>/SKILL.md``
        and are rewritten on disk; ``marketplace`` skills are document-store
        entries and are rewritten there. Renaming a skill (changing its
        front-matter ``name``) is refused for both sources: the skill's
        identity is its virtual mount path (``skill_path``), which callers
        reference from an agent manifest's ``skills:`` list, so silently
        changing it would orphan that reference.
        """
        existing = self.resolve_skill(skill_path)
        if existing is None:
            raise LookupError(f"skill {skill_path!r} not found")
        try:
            front_matter = parse_front_matter(content, source=f"skill:{skill_path}")
        except ValueError as exc:
            raise ValueError(f"invalid SKILL.md front matter: {exc}") from exc
        new_name = front_matter.get("name") if isinstance(front_matter, Mapping) else None
        if isinstance(new_name, str) and new_name.strip() and new_name.strip() != existing["name"]:
            raise ValueError(
                f"renaming skill {skill_path!r} is not supported; "
                "its mount path is referenced by agent manifests"
            )
        if existing["source"] == "native":
            name = cast(str, existing["name"])
            (_NATIVE_SKILLS_ROOT / name / "SKILL.md").write_text(content, encoding="utf-8")
        else:
            self._document_store.save_document(
                PROGENITOR_STATE_PREFIX,
                content_document_key("skill", cast(str, existing["location"])),
                {"content": content, "updated_at": utcnow_isoformat()},
            )
        return _skill_profile(
            skill_path,
            name=cast(str, existing["name"]),
            source=existing["source"],
            location=existing["location"],
            content=content,
        )

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM agent WHERE id = %s", (agent_id,))
            row = cast(dict[str, Any] | None, cur.fetchone())
            if row is None:
                return None
            agent = _row_to_agent(row)
            cur.execute(
                "SELECT status, summary FROM agent_graph WHERE agent_id = %s",
                (agent_id,),
            )
            graph_row = cast(dict[str, Any] | None, cur.fetchone())
        agent["graph"] = self._graph_payload(agent_id, graph_row)
        if agent.get("kind") == "routine" and agent["graph"] is None:
            return None
        return agent

    def upsert_agent(self, record: dict[str, Any]) -> None:
        details = record.get("details") or _empty_details()
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent (
                    id, kind, name, handle, display_group, color, model, role,
                    version, summary, status, system_prompt, details, updated_at
                )
                VALUES (
                    %(id)s, %(kind)s, %(name)s, %(handle)s, %(display_group)s,
                    %(color)s, %(model)s, %(role)s, %(version)s, %(summary)s,
                    %(status)s, %(system_prompt)s, %(details)s, now()
                )
                ON CONFLICT (id) DO UPDATE SET
                    kind = EXCLUDED.kind,
                    name = EXCLUDED.name,
                    handle = EXCLUDED.handle,
                    display_group = EXCLUDED.display_group,
                    color = EXCLUDED.color,
                    model = EXCLUDED.model,
                    role = EXCLUDED.role,
                    version = EXCLUDED.version,
                    summary = EXCLUDED.summary,
                    status = EXCLUDED.status,
                    system_prompt = EXCLUDED.system_prompt,
                    details = EXCLUDED.details,
                    updated_at = now()
                """,
                {**record, "details": Jsonb(details)},
            )

    def insert_agent_if_absent(self, record: dict[str, Any]) -> bool:
        """Insert a seed agent only when its id is not already present."""
        details = record.get("details") or _empty_details()
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent (
                    id, kind, name, handle, display_group, color, model, role,
                    version, summary, status, system_prompt, details
                )
                VALUES (
                    %(id)s, %(kind)s, %(name)s, %(handle)s, %(display_group)s,
                    %(color)s, %(model)s, %(role)s, %(version)s, %(summary)s,
                    %(status)s, %(system_prompt)s, %(details)s
                )
                ON CONFLICT (id) DO NOTHING
                """,
                {**record, "details": Jsonb(details)},
            )
            inserted = int(cur.rowcount) > 0
        return inserted

    def ensure_graph(self, agent_id: str, graph: dict[str, Any] | None = None) -> None:
        status_payload = graph or DEFAULT_AGENT_GRAPH_STATUS
        status = status_payload.get("status", "Draft")
        summary = status_payload.get("summary")
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_graph (agent_id, status, summary)
                VALUES (%s, %s, %s)
                ON CONFLICT (agent_id) DO NOTHING
                """,
                (agent_id, status, summary),
            )
        # Mirror ON CONFLICT DO NOTHING above: only seed the routine document
        # itself if this agent doesn't already have one.
        if (
            self._document_store.get_document(
                PROGENITOR_STATE_PREFIX, routine_graph_key(agent_id), None
            )
            is not None
        ):
            return
        document = default_routine_graph(agent_id)
        if graph is not None:
            document["entry"] = graph.get("entry", document["entry"])
            document["nodes"] = graph.get("nodes", document["nodes"])
            document["edges"] = graph.get("edges", document["edges"])
        self._document_store.save_document(
            PROGENITOR_STATE_PREFIX, routine_graph_key(agent_id), document
        )

    def get_routine_document(self, agent_id: str) -> dict[str, Any] | None:
        """Read a routine graph document directly, without requiring an
        ``agent`` row to already exist -- ``get_agent``'s ``graph`` field goes
        through ``_graph_payload``, which needs the row first.
        """
        document = self._document_store.get_document(
            PROGENITOR_STATE_PREFIX, routine_graph_key(agent_id), None
        )
        payload = document.payload if document is not None else None
        return payload if isinstance(payload, dict) else None

    def list_routine_document_ids(self) -> list[str]:
        """Every agent id with a persisted routine graph document."""
        return [
            key.removeprefix(ROUTINE_GRAPH_KEY_PREFIX)
            for key, _ in self._document_store.list_documents(
                PROGENITOR_STATE_PREFIX, ROUTINE_GRAPH_KEY_PREFIX
            )
        ]

    def ensure_routine_agent(self, agent_id: str, graph: dict[str, Any]) -> bool:
        """Create the ``agent``/``agent_graph`` rows for a routine graph
        document written directly to the shared document store -- chiefly the
        runtime agent's ``/routines/<id>.json`` filesystem tool
        (``RoutineDirectoryBackend.awrite``), which persists the graph without
        ever going through the HTTP graph-update route and the row creation
        it implicitly relies on (that route 404s unless the agent already
        exists -- see ``update_agent_graph``). Idempotent: both inserts are
        ``ON CONFLICT DO NOTHING``, so this is safe to call on every routine
        document write, not just the first. Returns True when this created a
        previously-unknown routine (the roster changed).
        """
        created = self.insert_agent_if_absent(
            {
                "id": agent_id,
                "kind": "routine",
                "name": str(graph.get("name") or agent_id),
                "handle": None,
                "display_group": "Routines",
                "color": _color_for(agent_id),
                "model": None,
                "role": None,
                "version": str(graph.get("version") or "1.0.0"),
                "summary": str(graph.get("description") or ""),
                "status": "Draft",
                "system_prompt": None,
            }
        )
        self.ensure_graph(agent_id, graph)
        return created

    def delete_routine_agent(self, agent_id: str) -> bool:
        """Delete a routine's ``agent``/``agent_graph`` rows and its graph
        document. Callers must already have checked ``kind == "routine"``
        (see ``delete_agent_profile``); the ``kind`` guard here is a second,
        cheap line of defense so this can never delete an object/A2A/ACP
        profile row. ``agent_graph`` cascades via its FK. Returns True when a
        row was actually deleted.
        """
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM agent WHERE id = %s AND kind = 'routine'", (agent_id,))
            deleted = int(cur.rowcount) > 0
        if deleted:
            self._document_store.delete_document(
                PROGENITOR_STATE_PREFIX, routine_graph_key(agent_id)
            )
        return deleted

    def delete_routine_catalog_entry(self, agent_id: str) -> bool:
        """Remove catalog metadata only; graph metadata cascades through its FK.

        The authoritative routine document, history, and checkpoints are
        managed independently by the runtime deletion workflow.
        """
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM agent WHERE id = %s AND kind = 'routine'", (agent_id,))
            return int(cur.rowcount) > 0

    def update_graph(
        self,
        agent_id: str,
        graph: dict[str, Any],
        *,
        status: str | None = None,
        summary: str | None = None,
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT name, summary FROM agent WHERE id = %s", (agent_id,))
            agent_row = cast(dict[str, Any] | None, cur.fetchone())
            if agent_row is None:
                return None
            cur.execute(
                """
                INSERT INTO agent_graph (agent_id, status, summary, updated_at)
                VALUES (%s, COALESCE(%s, 'Draft'), %s, now())
                ON CONFLICT (agent_id) DO UPDATE SET
                    status = COALESCE(%s, agent_graph.status),
                    summary = COALESCE(EXCLUDED.summary, agent_graph.summary),
                    updated_at = now()
                RETURNING status, summary
                """,
                (agent_id, status, summary, status),
            )
            graph_row = cast(dict[str, Any] | None, cur.fetchone())
        document = default_routine_graph(
            agent_id, name=agent_row.get("name"), description=agent_row.get("summary") or ""
        )
        document["entry"] = graph.get("entry")
        document["nodes"] = graph.get("nodes", [])
        document["edges"] = graph.get("edges", [])
        for key in ("policy_id", "input_schema", "output_schema", "$defs"):
            if graph.get(key) is not None:
                document[key] = graph[key]
        self._document_store.save_document(
            PROGENITOR_STATE_PREFIX, routine_graph_key(agent_id), document
        )
        return self._graph_payload(agent_id, graph_row)

    def _graph_payload(
        self, agent_id: str, graph_row: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        document = self._document_store.get_document(
            PROGENITOR_STATE_PREFIX, routine_graph_key(agent_id), None
        )
        graph = document.payload if document is not None else None
        if not isinstance(graph, dict):
            return None
        status_payload = graph_row or DEFAULT_AGENT_GRAPH_STATUS
        return {
            "status": status_payload.get("status") or "Draft",
            "summary": status_payload.get("summary"),
            "entry": graph.get("entry"),
            "nodes": graph.get("nodes", []),
            "edges": graph.get("edges", []),
            "policy_id": graph.get("policy_id", "default"),
            "input_schema": graph.get("input_schema"),
            "output_schema": graph.get("output_schema"),
            "$defs": graph.get("$defs"),
        }


def _skill_profile(
    path: str, *, name: str, source: str, location: str | None, content: str
) -> dict[str, Any]:
    """Serialize one resolved ``SKILL.md`` for the Agents page skill-preview endpoint."""
    try:
        front_matter = parse_front_matter(content, source=f"skill:{path}")
    except ValueError:
        front_matter = None
    return {
        "path": path,
        "name": name,
        "source": source,
        "editable": True,
        "location": location,
        "raw_content": content,
        "front_matter": front_matter,
    }


def _front_matter_str(front_matter: Mapping[str, Any], key: str) -> str | None:
    value = front_matter.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _front_matter_color(front_matter: Mapping[str, Any]) -> str | None:
    value = _front_matter_str(front_matter, "color")
    return value if value and value.startswith("#") else None


def _object_agent_profile(
    manifest: Any, *, source: str, raw_content: str | None = None
) -> dict[str, Any]:
    """Serialize a manifest source into the existing agent-profile wire shape."""
    card: dict[str, Any] = {
        "source": source,
        "permissions": [
            {
                "operations": permission.operations,
                "paths": permission.paths,
                "mode": permission.mode,
            }
            for permission in manifest.permissions
        ],
        "interrupt_on": manifest.interrupt_on,
        "mounts": [{"path": mount.path, "access": mount.access} for mount in manifest.mounts],
    }
    front_matter: Mapping[str, Any] = {}
    if raw_content is not None:
        card["raw_content"] = raw_content
        try:
            front_matter = parse_front_matter(raw_content, source=f"object-{_slug(manifest.name)}")
            card["front_matter"] = front_matter
        except ValueError:
            logger.warning("Failed to re-parse front matter for object agent %r", manifest.name)
    return {
        "id": f"object-{_slug(manifest.name)}",
        "kind": "object",
        "name": manifest.name,
        # ``handle``/``group``/``color`` are UI-only presentation fields with no
        # dedicated manifest schema field (unlike name/description/tools/skills,
        # which are validated ``ObjectAgentManifest`` attributes). They read
        # straight from arbitrary front-matter keys when authored, so editing
        # the SYSTEM.md YAML can change them like any other field; absent keys
        # keep the prior deterministic defaults.
        "handle": _front_matter_str(front_matter, "handle") or manifest.name,
        "group": _front_matter_str(front_matter, "group") or "Agents",
        "color": _front_matter_color(front_matter) or _color_for(manifest.name),
        "model": manifest.model,
        "role": manifest.description or None,
        "version": manifest.version,
        "summary": manifest.description,
        "prompt": manifest.system_prompt,
        "skills": [{"name": skill} for skill in manifest.skills],
        "tools": [{"name": tool} for tool in manifest.tools],
        "tasks": [],
        "metrics": dict(_EMPTY_METRICS),
        "card": card,
    }


def _row_to_agent(row: dict[str, Any]) -> dict[str, Any]:
    details = row.get("details")
    if not isinstance(details, dict):
        details = _empty_details()
    updated_at = row.get("updated_at")
    return {
        "id": row["id"],
        "kind": row.get("kind"),
        "name": row.get("name"),
        "handle": row.get("handle"),
        "group": row.get("display_group"),
        "color": row.get("color"),
        "model": row.get("model"),
        "role": row.get("role"),
        "version": row.get("version"),
        "summary": row.get("summary"),
        "status": row.get("status"),
        "prompt": row.get("system_prompt") or "",
        "skills": details.get("skills", []),
        "tools": details.get("tools", []),
        "tasks": details.get("tasks", []),
        "metrics": details.get("metrics", dict(_EMPTY_METRICS)),
        "transport": details.get("transport"),
        "url": details.get("url"),
        "card": details.get("card") or {},
        "updated": updated_at.isoformat() if updated_at else None,
    }


def _acp_task_view(record: dict[str, Any]) -> dict[str, Any]:
    """Shape a Progenitor ``ACPTaskRecord`` dict for the Agents-page detail pane."""
    return {
        "id": str(record.get("task_id") or ""),
        "status": str(record.get("status") or "working"),
        "source": str(record.get("source") or ""),
        "title": str(record.get("request_text") or record.get("summary") or "").strip(),
        "summary": str(record.get("summary") or record.get("output") or "").strip()[:400],
        "created": record.get("created_at"),
        "updated": record.get("updated_at"),
        "completed": record.get("completed_at"),
    }


def _acp_tasks_by_agent() -> dict[str, list[dict[str, Any]]]:
    """Read Progenitor's ACP run records and group recent ones by agent name."""
    try:
        from .state_reader import get_state_reader

        records = get_state_reader().get_acp_tasks()
    except Exception as exc:  # pragma: no cover - registry/state dependent
        logger.warning("Could not read ACP run history: %s", exc)
        return {}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        agent = str(record.get("agent_name") or "")
        if agent:
            grouped.setdefault(agent, []).append(record)

    result: dict[str, list[dict[str, Any]]] = {}
    for agent, recs in grouped.items():
        recs.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
        result[agent] = [_acp_task_view(r) for r in recs[:_A2A_TASK_LIMIT]]
    return result


def acp_profile_from_info(
    info: Any, tasks_by_agent: dict[str, list[dict[str, Any]]] | None = None
) -> dict[str, Any]:
    """Map a Progenitor registry ``AgentInfo`` (transport=="acp") to an AgentProfile.

    Read-through: ACP agents are local stdio CLIs registered in the runtime
    registry. Connection facts come from the entry's ``acp`` config; ``status`` is
    the registry's PATH-availability check; runs come from the ACP task records.
    ACP agents are read-only here (no editable graph).
    """
    acp_cfg = getattr(info, "acp", None)
    acp_cfg = acp_cfg if isinstance(acp_cfg, dict) else {}
    name = str(getattr(info, "name", "") or "acp-agent")
    agent_id = f"acp-{_slug(name)}"
    description = str(getattr(info, "description", "") or "")
    command = str(acp_cfg.get("command") or "copilot")
    model = str(acp_cfg.get("model") or "")
    profile = acp_cfg.get("profile")
    yolo = profile == "copilot"
    port = acp_cfg.get("port")
    is_tcp = port is not None
    transport = "tcp" if is_tcp else "stdio"
    if is_tcp:
        endpoint = f"{acp_cfg.get('host') or '127.0.0.1'}:{port}"
        command_display = f"{endpoint} (ACP/TCP)"
    else:
        command_display = f"{command} --acp" + (" --yolo" if yolo else "")

    # ACP-specific metadata lives in the free-form ``card`` (mirrors how the A2A
    # detail pane reads ``card``), so the AgentProfile model stays unchanged.
    card = {
        "protocol": "Agent Client Protocol",
        "transport": transport,
        "command": command_display,
        "model": model,
        "permissionMode": "auto-approve (--yolo)" if yolo else "prompt",
        "timeoutSeconds": acp_cfg.get("timeout_seconds"),
        "capabilities": {
            "streaming": True,
            "terminal": True,
            "fileAccess": True,
            "autoApprove": yolo,
        },
    }

    if tasks_by_agent is None:
        tasks_by_agent = _acp_tasks_by_agent()
    tasks = tasks_by_agent.get(name, [])
    metrics = dict(_EMPTY_METRICS)
    metrics["tasks"] = str(len(tasks))

    return {
        "id": agent_id,
        "kind": "acp",
        "name": name.replace("_", " ").title() if "_" in name else name,
        "handle": f"@{_slug(name).replace('-', '_')}",
        "group": "ACP agents",
        "color": _color_for(agent_id),
        "model": model or command,
        "role": _first_sentence(description) or None,
        "version": str(getattr(info, "version", "") or "v0.1.0"),
        "summary": description,
        "status": str(getattr(info, "status", "") or "unknown"),
        "prompt": "",
        "skills": [],
        "tools": [
            {"name": "terminal", "type": "acp"},
            {"name": "read_text_file", "type": "acp"},
            {"name": "write_text_file", "type": "acp"},
        ],
        "tasks": tasks,
        "transport": transport,
        "url": None,
        "card": card,
        "metrics": metrics,
        "updated": getattr(info, "updated_at", None) or None,
        "graph": None,
    }


def acp_profiles_from_registry() -> list[dict[str, Any]]:
    """Read Progenitor's registry document and map its ACP agents read-through."""
    try:
        from .state_reader import get_state_reader

        agents, _ = get_state_reader().get_registry()
    except Exception as exc:  # pragma: no cover - registry/state dependent
        logger.warning("Could not read ACP registry: %s", exc)
        return []
    tasks_by_agent = _acp_tasks_by_agent()
    return [
        acp_profile_from_info(info, tasks_by_agent)
        for info in agents
        if _is_acp_registry_entry(info)
    ]


def _skills_from_card(card: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract a normalized skills list from an A2A agent card."""
    if not isinstance(card, dict):
        return []
    raw_skills = card.get("skills")
    if not isinstance(raw_skills, list):
        return []
    skills: list[dict[str, Any]] = []
    for raw in raw_skills:
        if not isinstance(raw, dict):
            continue
        skill_id = str(raw.get("id") or raw.get("name") or "").strip()
        if not skill_id:
            continue
        skills.append(
            {
                "id": skill_id,
                "name": str(raw.get("name") or skill_id),
                "description": str(raw.get("description") or ""),
                "tags": [str(tag) for tag in (raw.get("tags") or [])],
                "examples": [str(example) for example in (raw.get("examples") or [])],
            }
        )
    return skills


def _card_first(card: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty string value among ``keys`` (camel/snake)."""
    for key in keys:
        value = card.get(key)
        if value:
            return str(value)
    return None


def _card_list(card: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = card.get(key)
        if isinstance(value, list) and value:
            return [str(item) for item in value]
    return []


def _a2a_card_details(card: dict[str, Any] | None) -> dict[str, Any]:
    """Extract A2A agent-card metadata for the card-driven detail pane.

    Keys are normalised to the camelCase the a2a-sdk serialises, tolerating
    snake_case in case a card is produced by another implementation.
    """
    if not isinstance(card, dict):
        return {}

    details: dict[str, Any] = {}

    protocol = _card_first(card, "protocolVersion", "protocol_version")
    if protocol:
        details["protocolVersion"] = protocol

    transport = _card_first(card, "preferredTransport", "preferred_transport")
    if transport:
        details["preferredTransport"] = transport

    documentation = _card_first(card, "documentationUrl", "documentation_url")
    if documentation:
        details["documentationUrl"] = documentation

    input_modes = _card_list(card, "defaultInputModes", "default_input_modes")
    if input_modes:
        details["inputModes"] = input_modes
    output_modes = _card_list(card, "defaultOutputModes", "default_output_modes")
    if output_modes:
        details["outputModes"] = output_modes

    caps_raw = card.get("capabilities")
    if isinstance(caps_raw, dict):
        details["capabilities"] = {
            "streaming": bool(caps_raw.get("streaming")),
            "pushNotifications": bool(
                caps_raw.get("pushNotifications") or caps_raw.get("push_notifications")
            ),
            "stateTransitionHistory": bool(
                caps_raw.get("stateTransitionHistory") or caps_raw.get("state_transition_history")
            ),
        }

    provider_raw = card.get("provider")
    if isinstance(provider_raw, dict):
        organization = str(provider_raw.get("organization") or "")
        provider_url = str(provider_raw.get("url") or "")
        if organization or provider_url:
            details["provider"] = {"organization": organization, "url": provider_url}

    return details


# Most recent A2A task runs to surface per agent in the detail pane.
_A2A_TASK_LIMIT = 25


def _task_view(record: dict[str, Any]) -> dict[str, Any]:
    """Shape a Progenitor ``A2ATaskRecord`` dict for the Agents-page detail pane."""
    return {
        "id": str(record.get("task_id") or ""),
        "status": str(record.get("status") or "unknown"),
        "source": str(record.get("source") or ""),
        "title": str(record.get("request_text") or record.get("summary") or "").strip(),
        "summary": str(
            record.get("summary") or record.get("latest_message") or record.get("last_error") or ""
        ).strip(),
        "context_id": record.get("context_id"),
        "created": record.get("created_at"),
        "updated": record.get("updated_at"),
        "completed": record.get("completed_at"),
    }


def _a2a_tasks_by_agent() -> dict[str, list[dict[str, Any]]]:
    """Read Progenitor's A2A task records and group recent ones by agent name."""
    try:
        from .state_reader import get_state_reader

        records = get_state_reader().get_a2a_tasks()
    except Exception as exc:  # pragma: no cover - registry/state dependent
        logger.warning("Could not read A2A task history: %s", exc)
        return {}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        agent = str(record.get("agent_name") or "")
        if agent:
            grouped.setdefault(agent, []).append(record)

    result: dict[str, list[dict[str, Any]]] = {}
    for agent, recs in grouped.items():
        recs.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
        result[agent] = [_task_view(r) for r in recs[:_A2A_TASK_LIMIT]]
    return result


def a2a_profile_from_info(
    info: Any, tasks_by_agent: dict[str, list[dict[str, Any]]] | None = None
) -> dict[str, Any]:
    """Map a Progenitor registry ``AgentInfo`` to an ``AgentProfile``-shaped dict.

    Read-through: everything comes from the live agent card Progenitor already
    fetched (``info.agent_card``), Progenitor's health-derived ``status`` (shown
    verbatim), and the A2A task records Progenitor tracks (``tasks_by_agent``).
    A2A agents are read-only here, so no editable graph is attached.
    """
    card = getattr(info, "agent_card", None)
    card = card if isinstance(card, dict) else {}
    name = str(card.get("name") or getattr(info, "name", "") or "agent")
    agent_id = f"a2a-{_slug(str(getattr(info, 'name', '') or name))}"
    description = str(card.get("description") or getattr(info, "description", "") or "")
    card_details = _a2a_card_details(card)
    transport = card_details.get("preferredTransport") or getattr(info, "transport", None)
    url = card.get("url") or getattr(info, "url", None)
    version = str(card.get("version") or getattr(info, "version", "") or "v0.1.0")

    if tasks_by_agent is None:
        tasks_by_agent = _a2a_tasks_by_agent()
    tasks = tasks_by_agent.get(str(getattr(info, "name", "") or ""), [])
    thread_ids = {task["context_id"] for task in tasks if task.get("context_id")}
    metrics = dict(_EMPTY_METRICS)
    metrics["tasks"] = str(len(tasks))
    metrics["checkpoints"] = str(len(thread_ids))

    return {
        "id": agent_id,
        "kind": "a2a",
        "name": name,
        "handle": f"@{_slug(name).replace('-', '_')}",
        "group": "A2A agents",
        "color": _color_for(agent_id),
        "model": None,
        "role": _first_sentence(description) or None,
        "version": version,
        "summary": description,
        "status": str(getattr(info, "status", "") or "unknown"),
        "enabled": bool(getattr(info, "enabled", True)),
        "prompt": "",
        "skills": _skills_from_card(card),
        "tasks": tasks,
        "transport": transport,
        "url": url,
        "card": card_details,
        "metrics": metrics,
        "updated": getattr(info, "updated_at", None) or None,
        "graph": None,
    }


def a2a_profiles_from_registry() -> list[dict[str, Any]]:
    """Read Progenitor's registry document and map its A2A agents read-through."""
    try:
        from .state_reader import get_state_reader

        agents, _ = get_state_reader().get_registry()
    except Exception as exc:  # pragma: no cover - registry/state dependent
        logger.warning("Could not read A2A registry: %s", exc)
        return []
    tasks_by_agent = _a2a_tasks_by_agent()
    return [
        a2a_profile_from_info(info, tasks_by_agent)
        for info in agents
        if not _is_acp_registry_entry(info)
    ]


_default_store: AgentStore | None = None


def configure_agent_store(
    admin_url: str | None,
    backend_url: str | None,
    backend_db_name: str | None,
    document_store: BackendDocumentStore,
) -> AgentStore | None:
    """Open the backend agent store, creating its database if needed."""
    global _default_store
    if _default_store is not None:
        _default_store.close()
        _default_store = None

    if not backend_url:
        logger.warning("Backend database URL is not set; agent store is unavailable")
        return None

    if admin_url and backend_db_name:
        ensure_database_exists(admin_url, backend_db_name)

    store = AgentStore(backend_url, document_store)
    store.open()
    _default_store = store
    logger.info("Backend agent store connected to Postgres database %r", backend_db_name)
    return store


def set_agent_store(store: AgentStore | None) -> None:
    """Install a store explicitly, primarily for tests."""
    global _default_store
    if _default_store is not None and _default_store is not store:
        _default_store.close()
    _default_store = store


def get_agent_store() -> AgentStore | None:
    return _default_store
