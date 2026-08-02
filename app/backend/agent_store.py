"""Backend-owned agent storage.

Agent catalog metadata (name, role, status, etc.) is the backend's own data, so
it lives in a separate database on the *same* Postgres server as the
Progenitor runtime (only the database name differs). The store is seeded from
code-defined DeepAgent subagents (``app/progenitor/agent/subagents.py``) and
the shared Postgres ``registry`` document; live runtime status is merged in at
read time by the route layer.

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
import re
from typing import Any, cast

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.progenitor.routines.graph import VALID_ROUTINE_NODE_KINDS
from app.progenitor.routines.store import (
    ROUTINE_GRAPH_KEY_PREFIX,
    default_routine_graph,
    routine_graph_key,
)

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


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "agent"


def _title(value: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_\s]+", value.strip()) if part)


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
        "repositories": [],
        "knowledge": [],
        "memories": [],
        "sessions": [],
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
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'agent_graph' AND column_name = 'graph'
                """
            )
            if cur.fetchone() is not None:
                self._migrate_legacy_graph_column(cur)

    def _migrate_legacy_graph_column(self, cur: Any) -> None:
        """One-time migration: move ``agent_graph.graph`` JSONB rows into the
        shared document store, then drop the column. ``_ensure_tables`` only
        calls this when the column still exists, so re-running is a no-op.
        """
        cur.execute(
            """
            SELECT ag.agent_id, ag.graph, a.name, a.summary
            FROM agent_graph ag JOIN agent a ON a.id = ag.agent_id
            """
        )
        rows = cast(list[dict[str, Any]], cur.fetchall())
        for row in rows:
            graph = row.get("graph")
            nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
            kinds = {n.get("kind") for n in nodes if isinstance(n, dict)}
            document = default_routine_graph(
                row["agent_id"], name=row.get("name"), description=row.get("summary") or ""
            )
            if isinstance(graph, dict) and not kinds - VALID_ROUTINE_NODE_KINDS:
                document["entry"] = graph.get("entry") or document["entry"]
                document["nodes"] = graph.get("nodes") or document["nodes"]
                document["edges"] = graph.get("edges", [])
            self._document_store.save_document(
                PROGENITOR_STATE_PREFIX, routine_graph_key(row["agent_id"]), document
            )
        cur.execute("ALTER TABLE agent_graph DROP COLUMN graph")
        if rows:
            logger.info(
                "Migrated %d legacy agent_graph.graph row(s) into the document store", len(rows)
            )

    def delete_agents_by_kind(self, kind: str) -> int:
        """Delete all agent rows of a given kind. Used to purge legacy A2A rows
        now that A2A agents are read through from Progenitor's registry."""
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM agent WHERE kind = %s", (kind,))
            return int(cur.rowcount)

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
        return [_row_to_agent(row) for row in rows]

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

    def reset_legacy_graphs(self, valid_kinds: set[str]) -> int:
        """Reset routine documents that use node kinds outside ``valid_kinds`` to
        the current default. Dev convenience so the legacy decorative graphs
        don't get stuck (``ensure_graph`` only seeds when a document is absent).
        Returns the count.
        """
        reset = 0
        for key, document in self._document_store.list_documents(
            PROGENITOR_STATE_PREFIX, ROUTINE_GRAPH_KEY_PREFIX
        ):
            agent_id = key[len(ROUTINE_GRAPH_KEY_PREFIX) :]
            graph = document.payload if isinstance(document.payload, dict) else {}
            nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
            kinds = {n.get("kind") for n in nodes if isinstance(n, dict)}
            if not kinds - valid_kinds:
                continue
            replacement = default_routine_graph(
                agent_id,
                name=graph.get("name") if isinstance(graph, dict) else None,
                description=graph.get("description", "") if isinstance(graph, dict) else "",
            )
            self._document_store.save_document(PROGENITOR_STATE_PREFIX, key, replacement)
            reset += 1
        return reset

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
                    status = COALESCE(EXCLUDED.status, agent_graph.status),
                    summary = COALESCE(EXCLUDED.summary, agent_graph.summary),
                    updated_at = now()
                RETURNING status, summary
                """,
                (agent_id, status, summary),
            )
            graph_row = cast(dict[str, Any] | None, cur.fetchone())
        document = default_routine_graph(
            agent_id, name=agent_row.get("name"), description=agent_row.get("summary") or ""
        )
        document["entry"] = graph.get("entry")
        document["nodes"] = graph.get("nodes", [])
        document["edges"] = graph.get("edges", [])
        self._document_store.save_document(
            PROGENITOR_STATE_PREFIX, routine_graph_key(agent_id), document
        )
        return self._graph_payload(agent_id, graph_row)

    def _graph_payload(self, agent_id: str, graph_row: dict[str, Any] | None) -> dict[str, Any]:
        default = default_routine_graph(agent_id)
        document = self._document_store.get_document(
            PROGENITOR_STATE_PREFIX, routine_graph_key(agent_id), default
        )
        graph = document.payload if document is not None else default
        if not isinstance(graph, dict):
            graph = default
        status_payload = graph_row or DEFAULT_AGENT_GRAPH_STATUS
        return {
            "status": status_payload.get("status") or "Draft",
            "summary": status_payload.get("summary"),
            "entry": graph.get("entry"),
            "nodes": graph.get("nodes", []),
            "edges": graph.get("edges", []),
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
        "repositories": details.get("repositories", []),
        "knowledge": details.get("knowledge", []),
        "memories": details.get("memories", []),
        "sessions": details.get("sessions", []),
        "tasks": details.get("tasks", []),
        "metrics": details.get("metrics", dict(_EMPTY_METRICS)),
        "transport": details.get("transport"),
        "url": details.get("url"),
        "card": details.get("card") or {},
        "updated": updated_at.isoformat() if updated_at else None,
    }


def _agent_record_from_subagent(sub: dict[str, Any]) -> dict[str, Any]:
    raw_name = str(sub.get("name") or "subagent")
    agent_id = f"subagent-{_slug(raw_name)}"
    description = str(sub.get("description") or "")
    return {
        "id": agent_id,
        "kind": "subagent",
        "name": _title(raw_name),
        "handle": f"@{_slug(raw_name).replace('-', '_')}",
        "display_group": "Subagents",
        "color": _color_for(agent_id),
        "model": "inherit",
        "role": _first_sentence(description) or "DeepAgent subagent",
        "version": "v0.1.0",
        "summary": description,
        "status": "running",
        "system_prompt": str(sub.get("system_prompt") or ""),
        "details": _empty_details(),
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
        "role": _first_sentence(description) or "ACP agent",
        "version": str(getattr(info, "version", "") or "v0.1.0"),
        "summary": description,
        "status": str(getattr(info, "status", "") or "unknown"),
        "prompt": "",
        "skills": [],
        "tools": [
            {"name": "terminal", "type": "acp", "calls": 0, "enabled": True},
            {"name": "read_text_file", "type": "acp", "calls": 0, "enabled": True},
            {"name": "write_text_file", "type": "acp", "calls": 0, "enabled": True},
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
        "model": str(getattr(info, "kind", "") or "a2a"),
        "role": _first_sentence(description) or "A2A agent",
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


def seed_default_agents(store: AgentStore) -> int:
    """Seed backend-owned subagents from code. Idempotent.

    Only DeepAgent subagents are backend-owned (editable). A2A agents are not
    stored here — they are read through from Progenitor's registry at request time.
    Existing rows are never overwritten (ON CONFLICT DO NOTHING), so user edits
    survive restarts. Returns the number of agents newly inserted.
    """
    records: list[dict[str, Any]] = []

    try:
        from ..progenitor.agent.subagents import create_progenitor_sync_subagents

        for sub in create_progenitor_sync_subagents():
            records.append(_agent_record_from_subagent(dict(sub)))
    except Exception as exc:  # pragma: no cover - import/environment dependent
        logger.warning("Could not seed code subagents: %s", exc)

    inserted = 0
    for record in records:
        if store.insert_agent_if_absent(record):
            inserted += 1
        store.ensure_graph(record["id"])

    if inserted:
        logger.info("Seeded %d agent(s) into the backend database", inserted)
    return inserted


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
    # A2A and ACP agents are read through from Progenitor's registry; drop any
    # legacy rows left in the backend DB (A2A from the old sync architecture, ACP
    # from the earlier hardcoded preview seed).
    purged = store.delete_agents_by_kind("a2a") + store.delete_agents_by_kind("acp")
    if purged:
        logger.info("Purged %d legacy A2A/ACP agent row(s) from the backend database", purged)
    seed_default_agents(store)
    # Refresh any graphs still using the legacy decorative node kinds so the dev
    # database reflects the executable workflow schema.
    from .agent_profile_routes import VALID_NODE_KINDS

    reset = store.reset_legacy_graphs(VALID_NODE_KINDS)
    if reset:
        logger.info("Reset %d legacy subagent graph(s) to the default workflow", reset)
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
