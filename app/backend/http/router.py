"""Compose UI feature routers without owning domain state."""

from __future__ import annotations

from fastapi import APIRouter

import app.backend.agents.routes as agents_routes
import app.backend.agents.store as agents_store
import app.backend.alerts.routes as alerts_routes
import app.backend.chat.routes as chat_routes
import app.backend.events.routes as events_routes
import app.backend.events.stream as events_stream
import app.backend.inbox.routes as inbox_routes
import app.backend.insights.routes as insights_routes
import app.backend.integrations.runtime as integrations_runtime
import app.backend.monitoring.logs as monitoring_logs
import app.backend.monitoring.state as monitoring_state
import app.backend.schedules.routes as schedules_routes
import app.backend.settings.mcp as settings_mcp
import app.backend.settings.model_profiles as settings_model_profiles
import app.backend.settings.policies as settings_policies
import app.backend.settings.providers as settings_providers
import app.backend.storage.documents as storage_documents
import app.backend.storage.routes as storage_routes
import app.backend.tasks.routes as tasks_routes
import app.backend.workflows.routes as workflows_routes
from app.progenitor.settings.providers import get_provider_repository

router = APIRouter(prefix="/api/ui", tags=["ui"])

router.include_router(workflows_routes.router)

router.include_router(schedules_routes.router)

router.include_router(alerts_routes.router)

router.include_router(tasks_routes.router)

router.include_router(settings_mcp.router)

router.include_router(storage_routes.router)

router.include_router(monitoring_logs.router)

router.include_router(events_routes.router)

router.include_router(
    chat_routes.create_chat_router(
        get_document_store=storage_documents.get_default_document_store,
        broadcast_ui_event=events_stream.broadcast_ui_event,
        get_current_state=lambda thread_id: (
            monitoring_state.get_state_reader().get_progenitor_state(thread_id)
        ),
        get_thread_messages=lambda thread_id: (
            monitoring_state.get_state_reader().get_thread_messages(thread_id)
        ),
        get_active_request_id=chat_routes._get_active_request_id_for_thread,
    )
)
router.include_router(
    agents_routes.create_agent_profile_router(
        get_agent_store=agents_store.get_agent_store,
        get_document_store=storage_documents.get_default_document_store,
        broadcast_ui_event=events_stream.broadcast_ui_event,
        is_runtime_live=integrations_runtime._progenitor_runtime_live,
    )
)
router.include_router(
    inbox_routes.create_inbox_router(
        get_document_store=storage_documents.get_default_document_store,
        broadcast_ui_event=events_stream.broadcast_ui_event,
    )
)
router.include_router(
    settings_policies.create_policy_router(
        storage_documents.get_default_document_store, events_stream.broadcast_ui_event
    )
)
router.include_router(
    settings_model_profiles.create_model_profile_router(
        get_store=storage_documents.get_default_document_store,
        broadcast=events_stream.broadcast_ui_event,
        get_provider_repository=get_provider_repository,
    )
)
router.include_router(
    settings_providers.create_provider_router(
        get_repository=get_provider_repository,
        get_store=storage_documents.get_default_document_store,
        broadcast=events_stream.broadcast_ui_event,
    )
)
router.include_router(
    insights_routes.create_insights_router(
        storage_documents.get_default_document_store, events_stream.broadcast_ui_event
    )
)
