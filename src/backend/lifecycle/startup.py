"""Lifecycle startup."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

import src.backend.agents.store as agents_store
import src.backend.alerts.relay as alerts_relay
import src.backend.alerts.routes as alerts_routes
import src.backend.attachments.store as attachments_store
import src.backend.channels.manager as channels_manager
import src.backend.chat.responses as chat_responses
import src.backend.events.contracts as events_contracts
import src.backend.events.documents as events_documents
import src.backend.events.routes as events_routes
import src.backend.events.stream as events_stream
import src.backend.integrations.runtime as integrations_runtime
import src.backend.lifecycle.shutdown as lifecycle_shutdown
import src.backend.lifecycle.state as lifecycle_state
import src.backend.lifecycle.watchers as lifecycle_watchers
import src.backend.schedules.service as schedules_service
import src.backend.storage.documents as storage_documents
from src.progena.settings.providers import (
    configure_provider_repository,
    provider_database_url,
    shutdown_provider_repository,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager with graceful shutdown (OPS-002).

    Enhanced with:
    - Session manager lifecycle
    - Attachment manager cleanup
    - Graceful shutdown with callbacks
    """
    sse_event_forwarding_task: asyncio.Task | None = None

    # Startup
    logger.info("Starting backend with enhanced features...")
    document_store = storage_documents.configure_default_document_store(
        lifecycle_state.config.database_url,
        runtime_url=lifecycle_state.config.progena_runtime_url,
    )
    events_documents.seed_document_types(document_store)
    attachment_blob_store = attachments_store.configure_attachment_blob_store(
        lifecycle_state.config.database_url
    )

    # Provider credentials and connection metadata are backend-owned settings,
    # deliberately separate from the runtime's LangGraph document store.
    provider_repository = None
    try:
        if lifecycle_state.config.database_url:
            provider_repository = configure_provider_repository(
                provider_database_url(lifecycle_state.config.database_url)
            )
    except Exception:  # noqa: BLE001 - Settings routes report provider DB availability
        logger.exception("Failed to configure provider repository")

    async def close_provider_repository() -> None:
        if provider_repository is not None:
            shutdown_provider_repository()

    async def close_document_store() -> None:
        document_store.close()

    async def close_attachment_blob_store() -> None:
        attachment_blob_store.close()

    # Backend-owned agent data lives in a separate database on the same server.
    try:
        agent_store = agents_store.configure_agent_store(
            lifecycle_state.config.database_url,
            lifecycle_state.config.backend_database_url,
            lifecycle_state.config.backend_database_name,
            document_store,
        )
    except Exception:  # noqa: BLE001 - backend must still start if agent DB is unavailable
        logger.exception("Failed to configure backend agent store")
        agent_store = None

    async def close_agent_store() -> None:
        if agent_store is not None:
            agent_store.close()

    # Initialize session manager
    await lifecycle_state.session_manager.start()
    logger.info("Session manager started")

    # Install graceful shutdown signal handlers
    lifecycle_state.graceful_shutdown.install_signal_handlers()

    # Register drain callbacks - these run at the start of draining to help
    # close long-running connections that would otherwise block draining
    lifecycle_state.graceful_shutdown.register_drain_callback(
        "close_sse_connections",
        events_routes.close_all_sse_connections,
        priority=10,  # Run early to unblock request draining
        timeout=5.0,
    )

    # Register shutdown callbacks - these run after draining completes
    lifecycle_state.graceful_shutdown.register_callback(
        "close_document_store",
        close_document_store,
        priority=45,
        timeout=5.0,
    )
    lifecycle_state.graceful_shutdown.register_callback(
        "close_provider_repository",
        close_provider_repository,
        priority=45,
        timeout=5.0,
    )
    lifecycle_state.graceful_shutdown.register_callback(
        "close_attachment_blob_store",
        close_attachment_blob_store,
        priority=45,
        timeout=5.0,
    )
    lifecycle_state.graceful_shutdown.register_callback(
        "close_agent_store",
        close_agent_store,
        priority=45,
        timeout=5.0,
    )
    lifecycle_state.graceful_shutdown.register_callback(
        "stop_session_manager",
        lifecycle_state.session_manager.stop,
        priority=50,
        timeout=5.0,
    )

    # Initialize the Progena runtime HTTP client
    logger.info(
        "Using Progena runtime client (base_url=%s)",
        lifecycle_state.config.progena_runtime_url,
    )
    # New alert documents are relayed to the user by Progena.
    alerts_routes.set_alert_relay(
        lambda alert: alerts_relay._relay_alert(lifecycle_state.client, alert)
    )

    # Runtime events that imply the schedule document changed (a task fired and
    # was removed/restamped, or a schedule was created/edited from a chat).
    schedule_change_events = {"scheduled_task_triggered", "schedule_updated"}

    # Create a combined event callback that handles both SSE and messaging channels
    def combined_event_callback(event_type: str, data: dict) -> None:
        """Route runtime events to SSE and, while no browser is open, to channels.

        - Always broadcasts to SSE for any connected web clients
        - With no web client connected, channels that notify when away receive
          agent messages, agent questions and scheduled responses
        """

        # Always broadcast to SSE. Queue snapshots are runtime events rather
        # than chat content, so their conversation identity is top-level.
        metadata = data.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        thread_id = (
            str(
                metadata.get("thread_id")
                or (
                    data.get("conversation_id")
                    if event_type == events_contracts.UiEvent.CHAT_QUEUE_UPDATED
                    else data.get("thread_id", "")
                )
            ).strip()
            or None
        )
        request_id = data.get("request_id")
        chat_responses.handle_runtime_queue_lifecycle(event_type, data)
        events_stream.broadcast_ui_event(
            event_type,
            data,
            scope="chat",
            session_id=thread_id,
            request_id=request_id if isinstance(request_id, str) and request_id else None,
        )
        # Reflect runtime-driven schedule changes on the canonical UI stream so
        # the Schedules page updates live (e.g. a one-off firing and removing
        # itself, or a schedule created from a chat).
        if event_type in schedule_change_events:
            try:
                schedules_service.broadcast_schedule_snapshot()
            except Exception:  # pragma: no cover - best-effort live update
                logger.debug("Failed to broadcast schedule snapshot", exc_info=True)

        # Fire and forget: channel delivery never blocks the SSE broadcast.
        channels_manager.get_channel_manager().notify_away(event_type, data.get("content") or "")

    lifecycle_state.client = integrations_runtime.get_sse_client(
        lifecycle_state.config, event_callback=combined_event_callback
    )

    # Rehydrate relays only after the runtime client is connected. Runtime
    # broadcasts are live-only, while its durable queue retains claimed work.
    async def start_runtime_and_recover_queue(
        client: integrations_runtime.SSEProgenaClient,
    ) -> None:
        await integrations_runtime.start_sse_client_with_retry(client)
        await chat_responses.recover_runtime_queued_executions()

    # Register client shutdown callback
    if lifecycle_state.client:
        sse_event_forwarding_task = asyncio.create_task(
            start_runtime_and_recover_queue(lifecycle_state.client)
        )

        async def stop_sse_event_forwarding_task() -> None:
            """Stop the background HTTP+SSE event-forwarding connector."""
            nonlocal sse_event_forwarding_task
            if sse_event_forwarding_task and not sse_event_forwarding_task.done():
                sse_event_forwarding_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sse_event_forwarding_task
            sse_event_forwarding_task = None

        lifecycle_state.graceful_shutdown.register_callback(
            "stop_sse_event_forwarding_task",
            stop_sse_event_forwarding_task,
            priority=25,
            timeout=5.0,
        )

        async def shutdown_progena_client() -> None:
            """Wrapper to ensure callback returns None."""
            await lifecycle_shutdown.stop_client_safely(
                lifecycle_state.client, timeout=10.0, name="Progena"
            )

        lifecycle_state.graceful_shutdown.register_callback(
            "stop_progena_client",
            shutdown_progena_client,
            priority=30,
            timeout=15.0,
        )

    # Messaging channels run from their channel:<id> documents (seeded once
    # from legacy TELEGRAM_* variables) and need the runtime client above.
    channel_manager = channels_manager.get_channel_manager()
    await channel_manager.start()
    lifecycle_state.graceful_shutdown.register_callback(
        "stop_channels",
        channel_manager.stop,
        priority=70,
        timeout=10.0,
    )

    # Don't auto-start Progena - let it start on first request
    # This allows the server to start even if Progena has issues
    logger.info("Backend ready on %s:%d", lifecycle_state.config.host, lifecycle_state.config.port)
    logger.info(
        "Rate limiting: %d/min, %d/hour (enabled=%s)",
        lifecycle_state.config.rate_limit_per_minute,
        lifecycle_state.config.rate_limit_per_hour,
        lifecycle_state.config.rate_limit_enabled,
    )
    logger.info("Enhanced features: streaming, metadata, attachments, sessions")
    await lifecycle_watchers.start_state_status_watcher(reconnect_delay=2.0)
    lifecycle_state.graceful_shutdown.register_callback(
        "stop_state_status_watcher",
        lifecycle_watchers.stop_state_status_watcher,
        priority=40,
        timeout=5.0,
    )

    # Knowledge search indexer (Postgres only; idle if another process holds its lock).
    from src.backend.knowledge.indexer import get_knowledge_indexer

    indexer = get_knowledge_indexer()
    await indexer.start()
    if indexer.running:
        lifecycle_state.graceful_shutdown.register_callback(
            "stop_knowledge_indexer",
            indexer.stop,
            priority=35,
            timeout=10.0,
        )

    yield

    # Graceful Shutdown (OPS-002) - now using enhanced manager
    logger.info("Initiating graceful shutdown...")

    # Use the enhanced graceful shutdown manager
    await lifecycle_state.graceful_shutdown.initiate_shutdown()

    lifecycle_state.client = None
    logger.info("Backend shutdown complete")
