"""Compose UI feature routers without owning domain state."""

from __future__ import annotations

from fastapi import APIRouter

import src.backend.agents.routes as agents_routes
import src.backend.agents.store as agents_store
import src.backend.alerts.routes as alerts_routes
import src.backend.channels.manager as channels_manager
import src.backend.channels.routes as channels_routes
import src.backend.chat.routes as chat_routes
import src.backend.events.routes as events_routes
import src.backend.events.stream as events_stream
import src.backend.inbox.routes as inbox_routes
import src.backend.insights.routes as insights_routes
import src.backend.integrations.runtime as integrations_runtime
import src.backend.monitoring.logs as monitoring_logs
import src.backend.runs.routes as runs_routes
import src.backend.schedules.routes as schedules_routes
import src.backend.settings.mcp as settings_mcp
import src.backend.settings.model_profiles as settings_model_profiles
import src.backend.settings.policies as settings_policies
import src.backend.settings.providers as settings_providers
import src.backend.storage.documents as storage_documents
import src.backend.storage.routes as storage_routes
import src.backend.tasks.routes as tasks_routes
import src.backend.workflows.routes as workflows_routes
from src.progena.settings.providers import get_provider_repository

router = APIRouter(prefix="/api/ui", tags=["ui"])

router.include_router(workflows_routes.router)

router.include_router(schedules_routes.router)

router.include_router(runs_routes.router)

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
        get_active_response=chat_routes._get_active_response_for_thread,
    )
)
router.include_router(
    agents_routes.create_agent_profile_router(
        get_agent_store=agents_store.get_agent_store,
        get_document_store=storage_documents.get_default_document_store,
        broadcast_ui_event=events_stream.broadcast_ui_event,
        is_runtime_live=integrations_runtime._progena_runtime_live,
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
router.include_router(
    channels_routes.create_channels_router(
        get_manager=channels_manager.get_channel_manager,
        get_store=storage_documents.get_default_document_store,
        broadcast=events_stream.broadcast_ui_event,
    )
)
