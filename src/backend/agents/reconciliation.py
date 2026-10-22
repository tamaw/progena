"""Agents reconciliation."""

from __future__ import annotations

import asyncio
import logging

import src.backend.agents.routes as agents_routes
import src.backend.agents.store as agents_store
import src.backend.events.stream as events_stream
import src.backend.monitoring.status as monitoring_status
import src.backend.storage.documents as storage_documents
from src.progenitor.routines.store import ROUTINE_GRAPH_KEY_PREFIX

logger = logging.getLogger(__name__)

_active_routine_agent_tasks: set[asyncio.Task] = set()


def _queue_routine_agent_reconciliation(_snapshot, changed_key: str | None = None) -> None:
    """Always-on watcher that reconciles the ``agent``/``agent_graph`` rows
    for routine graph documents written outside the HTTP graph-update route --
    chiefly the runtime agent's ``/routines/<id>.json`` filesystem tool
    (``RoutineDirectoryBackend.awrite``). That route requires the agent row to
    already exist (see ``update_agent_graph``), so a routine the runtime
    creates from scratch would otherwise never appear on the Routines page and
    never get a canonical event -- this closes that gap so a runtime-authored
    write reaches the frontend the exact same way a UI-driven graph edit does
    (``agent.graph.updated`` / ``agents.snapshot`` on ``/api/ui/stream`` ->
    Redux, see ``src/frontend/src/features/agents/state/agents.js``).

    Registered separately from ``_queue_state_watch_status``, which returns
    immediately with no UI client connected (fine for UI-push optimization,
    wrong here: the row must exist by the time someone opens the page, not
    only while they're already looking at it). ROUTINE_GRAPH_KEY_PREFIX is in
    ``state_reader._emit_if_changed``'s bypass list so no write here is
    silently dropped by the coarse snapshot diff.
    """
    if changed_key is None or changed_key.startswith("policy:"):

        async def push_policies():
            from src.backend.settings.policies import policy_catalog

            items = await asyncio.to_thread(
                policy_catalog, storage_documents.get_default_document_store()
            )
            events_stream.broadcast_ui_event("policies.snapshot", {"items": items})

        policy_task = asyncio.create_task(push_policies())
        _active_routine_agent_tasks.add(policy_task)
        policy_task.add_done_callback(_active_routine_agent_tasks.discard)
    task = asyncio.create_task(_reconcile_routine_agent(changed_key))
    _active_routine_agent_tasks.add(task)
    task.add_done_callback(_active_routine_agent_tasks.discard)


async def _reconcile_routine_agent(changed_key: str | None) -> None:
    store = agents_store.get_agent_store()
    if store is None:
        return
    if changed_key is None:
        # Include rows whose documents disappeared while disconnected.
        document_ids = await asyncio.to_thread(store.list_routine_document_ids)
        catalog_ids = await asyncio.to_thread(store.list_routine_catalog_ids)
        agent_ids = sorted(set(document_ids) | set(catalog_ids))
    elif changed_key.startswith(ROUTINE_GRAPH_KEY_PREFIX):
        agent_ids = [changed_key.removeprefix(ROUTINE_GRAPH_KEY_PREFIX)]
    else:
        return

    roster_changed = False
    for agent_id in agent_ids:
        if not agent_id:
            continue
        try:
            graph_document = await asyncio.to_thread(store.get_routine_document, agent_id)
            if graph_document is None:
                deleted = await asyncio.to_thread(store.delete_routine_catalog_entry, agent_id)
                if deleted:
                    roster_changed = True
                    events_stream.broadcast_ui_event("agent.deleted", {"agent_id": agent_id})
                continue
            created = await asyncio.to_thread(store.ensure_routine_agent, agent_id, graph_document)
            agent = await asyncio.to_thread(store.get_agent, agent_id)
        except Exception:
            logger.debug("Failed to reconcile routine agent %r", agent_id, exc_info=True)
            continue
        if agent is None or not isinstance(agent.get("graph"), dict):
            continue
        roster_changed = roster_changed or created
        # `agent["graph"]` goes through `_graph_payload`, which re-reads the
        # routine document from the store a second time (independently of
        # `graph_document` above). That second read has raced with the write
        # this same pass just made -- landing before it was visible -- and
        # published an empty `nodes`/`edges` graph. GraphCanvas seeds its
        # nodes/edges once per mount and never re-syncs from later prop
        # updates, so one bad broadcast during mount left the routine
        # looking permanently empty until the user forced a remount by
        # selecting a different agent and back. Source entry/nodes/edges
        # from the document we already fetched and just confirmed fresh;
        # keep status/summary from the SQL row, which only that row tracks.
        graph_payload = dict(agent["graph"])
        graph_payload["entry"] = graph_document.get("entry")
        graph_payload["nodes"] = graph_document.get("nodes", [])
        graph_payload["edges"] = graph_document.get("edges", [])
        events_stream.broadcast_ui_event(
            "agent.graph.updated",
            {
                "agent_id": agent_id,
                "graph": agents_routes.AgentGraphModel(**graph_payload).model_dump(by_alias=True),
            },
        )
        # The `store` trigger that fired this reconcile pass also appended a
        # row to `routine_graph_revisions` (or no-opped on identical content,
        # e.g. restoring a previously captured graph). Push the
        # refreshed list so an already-open Revisions panel picks up a new
        # entry live instead of only on its next manual Refresh.
        try:
            revisions = await asyncio.to_thread(
                storage_documents.get_default_document_store().list_routine_revisions, agent_id
            )
        except Exception:
            logger.debug("Failed to list routine revisions for %r", agent_id, exc_info=True)
        else:
            events_stream.broadcast_ui_event(
                "agent.routine.revisions.loaded",
                {
                    "agent_id": agent_id,
                    "revisions": [
                        {"revision_id": revision.revision_id, "captured_at": revision.captured_at}
                        for revision in revisions
                    ],
                },
            )

    if roster_changed:
        await monitoring_status._broadcast_agent_profiles_safe()
