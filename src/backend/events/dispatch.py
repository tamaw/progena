"""Events dispatch."""

from __future__ import annotations

import logging

import src.backend.alerts.routes as alerts_routes
import src.backend.chat.routes as chat_routes
import src.backend.events.documents as events_documents
import src.backend.modules.events as modules_events
import src.backend.schedules.service as schedules_service
import src.backend.storage.documents as storage_documents
import src.backend.tasks.service as tasks_service
import src.backend.workflows.runtime as workflows_runtime

logger = logging.getLogger(__name__)

_document_event_dispatcher: events_documents.DocumentEventDispatcher | None = None


def get_document_event_dispatcher() -> events_documents.DocumentEventDispatcher:
    """Return the backend's single document event log consumer."""

    global _document_event_dispatcher
    if _document_event_dispatcher is None:
        dispatcher = events_documents.DocumentEventDispatcher(
            store_getter=storage_documents.get_default_document_store
        )
        dispatcher.add_handler(workflows_runtime._publish_routine_run_lifecycle)
        dispatcher.add_handler(workflows_runtime._advance_workflows_on_routine_event)
        dispatcher.add_handler(alerts_routes._publish_alert_document)
        dispatcher.add_handler(workflows_runtime._apply_workflow_approval_request)
        dispatcher.add_handler(workflows_runtime._dispatch_document_event_workflows)
        dispatcher.add_handler(workflows_runtime.get_acp_task_supervisor().handle_document_event)
        dispatcher.add_handler(schedules_service._apply_document_event_widgets)
        dispatcher.add_handler(tasks_service._record_kanban_card_activity)
        dispatcher.add_handler(tasks_service._sync_kanban_card_order)
        dispatcher.add_handler(chat_routes._publish_chat_session_document_event)
        dispatcher.add_handler(modules_events.module_events.document_event)
        _document_event_dispatcher = dispatcher
    return _document_event_dispatcher
