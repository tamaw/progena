"""Lifecycle watchers."""

from __future__ import annotations

import logging

import src.backend.agents.reconciliation as agents_reconciliation
import src.backend.channels.manager as channels_manager
import src.backend.events.dispatch as events_dispatch
import src.backend.monitoring.state as monitoring_state
import src.backend.monitoring.status as monitoring_status
import src.backend.runs.events as runs_events
import src.backend.storage.documents as storage_documents
import src.backend.workflows.events as workflows_events
import src.backend.workflows.runtime as workflows_runtime

logger = logging.getLogger(__name__)

_state_watch_callback = None


_workflow_invoke_watch_callback = None


_knowledge_index_watch_callback = None


def _queue_knowledge_index_resync(_snapshot, changed_key: str | None = None) -> None:
    """Route state changes to the knowledge indexer."""
    from src.backend.knowledge.indexer import get_knowledge_indexer

    get_knowledge_indexer().on_store_change(changed_key)


_routine_agent_watch_callback = None


_channel_watch_callback = None


async def start_state_status_watcher(reconnect_delay: float = 2.0) -> None:
    global _state_watch_callback
    global _workflow_invoke_watch_callback, _routine_agent_watch_callback
    global _knowledge_index_watch_callback, _channel_watch_callback
    state_reader = monitoring_state.get_state_reader()
    if _state_watch_callback is None:
        _state_watch_callback = monitoring_status._queue_state_watch_status
        state_reader.add_watcher(_state_watch_callback)
    if _workflow_invoke_watch_callback is None:
        _workflow_invoke_watch_callback = workflows_runtime._queue_workflow_invoke_reconciliation
        state_reader.add_watcher(_workflow_invoke_watch_callback)
    if _routine_agent_watch_callback is None:
        _routine_agent_watch_callback = agents_reconciliation._queue_routine_agent_reconciliation
        state_reader.add_watcher(_routine_agent_watch_callback)
    if _knowledge_index_watch_callback is None:
        _knowledge_index_watch_callback = _queue_knowledge_index_resync
        state_reader.add_watcher(_knowledge_index_watch_callback)
    if _channel_watch_callback is None:
        _channel_watch_callback = channels_manager.get_channel_manager().on_store_change
        state_reader.add_watcher(_channel_watch_callback)
    await workflows_events.routine_events.start()
    # The runtime creates LangGraph's checkpoint tables.  Install the backend
    # trigger only after that schema exists, before listening for its NOTIFYs.
    storage_documents.ensure_runs_checkpoint_trigger(state_reader.document_store)
    await runs_events.runs_events.start(state_reader.document_store)
    await workflows_runtime.get_acp_task_supervisor().start()
    await events_dispatch.get_document_event_dispatcher().start(reconnect_delay)
    await workflows_runtime.get_workflow_executor().recover_startup_runs()
    await workflows_runtime.get_workflow_cron_clock().start()
    await state_reader.start_watching(reconnect_delay)


async def stop_state_status_watcher() -> None:
    global _state_watch_callback
    global _workflow_invoke_watch_callback, _routine_agent_watch_callback
    global _knowledge_index_watch_callback, _channel_watch_callback
    state_reader = monitoring_state.get_state_reader()
    if _state_watch_callback is not None:
        state_reader.remove_watcher(_state_watch_callback)
        _state_watch_callback = None
    if _workflow_invoke_watch_callback is not None:
        state_reader.remove_watcher(_workflow_invoke_watch_callback)
        _workflow_invoke_watch_callback = None
    if _routine_agent_watch_callback is not None:
        state_reader.remove_watcher(_routine_agent_watch_callback)
        _routine_agent_watch_callback = None
    if _knowledge_index_watch_callback is not None:
        state_reader.remove_watcher(_knowledge_index_watch_callback)
        _knowledge_index_watch_callback = None
    if _channel_watch_callback is not None:
        state_reader.remove_watcher(_channel_watch_callback)
        _channel_watch_callback = None
    await workflows_events.routine_events.stop()
    await runs_events.runs_events.stop()
    await workflows_runtime.get_workflow_cron_clock().stop()
    await workflows_runtime.get_acp_task_supervisor().stop()
    await events_dispatch.get_document_event_dispatcher().stop()
    await state_reader.stop_watching()
