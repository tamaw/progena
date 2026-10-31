"""Schedules routes."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Header, HTTPException, Request

import src.backend.chat.threads as chat_threads
import src.backend.http.contracts as http_contracts
import src.backend.http.requests as http_requests
import src.backend.lifecycle.state as lifecycle_state
import src.backend.monitoring.state as monitoring_state
import src.backend.schedules.models as schedules_models
import src.backend.schedules.service as schedules_service
import src.backend.settings.mcp as settings_mcp
import src.backend.settings.preferences_store as preferences_store
import src.backend.storage.documents as storage_documents
import src.backend.workflows.runtime as workflows_runtime
from src.backend.events import contracts as events_contracts
from src.progena.schedules.engine import Scheduler
from src.progena.schedules.service import generate_schedule_id
from src.progena.utils import utcnow

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/widgets", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_widgets(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Return the unified widget list and publish it on the UI stream.

    Mirrors the schedule/agent/mcp pattern: the GET acknowledges and broadcasts
    ``widgets.updated`` so pages render from the realtime store rather than
    holding the HTTP response themselves.
    """
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()
    widgets = [
        schedules_service._widget_to_response(widget) for widget in state_reader.get_widgets()
    ]
    schedules_service.broadcast_widgets_snapshot(widgets, request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.WIDGETS_UPDATED]
    )


@router.post(
    "/widgets/{widget_id}/refresh",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def refresh_widget(
    widget_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Refresh agent widget metadata from its live protocol surface.

    MCP servers are not refreshed here: their tools/capabilities are discovered
    by the Progena runtime when it initialises its MCP context (and on
    ``mcp_refresh``), which is the single source of truth the UI reads from.
    """
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()
    widget = schedules_service._find_widget_payload(state_reader, widget_id)

    if widget.get("type") == "agent":
        refreshed = await schedules_service._inspect_agent_widget(widget, state_reader)
        schedules_service._broadcast_widget_refresh(
            state_reader,
            request_id=request_id,
            refreshed_widget=refreshed,
        )
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.WIDGETS_UPDATED]
        )

    raise HTTPException(
        status_code=400,
        detail="Only agent widgets support live refresh",
    )


@router.patch(
    "/widgets/{widget_id}", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def patch_widget(
    widget_id: str,
    body: schedules_models.WidgetPatchRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Patch a backend-owned widget where a matching state file exists."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()
    if widget_id.startswith("schedule:"):
        schedule_id = widget_id.split(":", 1)[1]
        schedule_data = schedules_service._read_schedule_config(state_reader)

        def _apply_schedule_patch(entry: dict) -> None:
            current_schedule_type = body.schedule_type or entry.get("schedule_type") or "cron"
            current_expression = (
                body.expression or entry.get("schedule") or entry.get("cron_expression") or ""
            )
            if body.enabled is not None:
                entry["enabled"] = body.enabled
                # Mirror into ``paused`` so the runtime scheduler honours the change.
                entry["paused"] = not body.enabled
            if body.name is not None:
                entry["name"] = body.name
                entry["user_request"] = body.name
            if body.instruction is not None:
                entry["message"] = body.instruction
                entry["instruction"] = body.instruction
            if body.expression is not None or body.schedule_type is not None:
                normalized_type, cron_expression, scheduled_at = (
                    schedules_service._resolve_schedule_expression(
                        current_schedule_type,
                        current_expression,
                    )
                )
                entry["schedule_type"] = normalized_type
                entry["schedule"] = scheduled_at or current_expression
                if cron_expression is None:
                    entry.pop("cron_expression", None)
                else:
                    entry["cron_expression"] = cron_expression
                if scheduled_at is None:
                    entry.pop("scheduled_at", None)
                else:
                    entry["scheduled_at"] = scheduled_at
            if body.timezone is not None:
                entry["timezone"] = body.timezone
            if "conversation_id" in body.model_fields_set:
                entry["conversation_id"] = schedules_service.validate_destination(
                    body.conversation_id
                )
            if body.enabled and entry.get("conversation_id"):
                # Resuming must not wake an archived or deleted chat.
                schedules_service.validate_destination(entry["conversation_id"])
            entry["updated_at"] = utcnow().isoformat()
            next_run = Scheduler().next_run_for_entry(
                entry, preferences_store.load_preferences().get("timezone", "UTC")
            )
            entry["next_run"] = schedules_service._next_run_iso(next_run) if next_run else None
            entry["next_run_at"] = schedules_service._next_run_iso(next_run) if next_run else None

        def _save_schedule() -> None:
            state_reader.document_store.save_document(
                storage_documents.PROGENA_STATE_PREFIX, "schedule", schedule_data
            )

        updated = schedules_service._patch_document_entry(
            entries=schedule_data,
            entry_id=schedule_id,
            apply=_apply_schedule_patch,
            save=_save_schedule,
        )

        schedules_service._widget_or_fallback(
            widget_id,
            state_reader,
            lambda: {
                "id": widget_id,
                "type": "schedule",
                "name": updated.get("name", schedule_id),
                "title": updated.get("name", schedule_id),
                "status": "enabled" if updated.get("enabled", True) else "disabled",
                "description": updated.get("message"),
                "summary": updated.get("message"),
                "updated_at": updated.get("created_at"),
            },
        )

        schedules_service._broadcast_widget_refresh(state_reader, request_id=request_id)
        schedules_service.broadcast_schedule_snapshot(request_id=request_id)
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.WIDGETS_UPDATED]
        )

    if widget_id.startswith("event:"):
        event_id = widget_id.split(":", 1)[1]
        event_widgets = schedules_service._read_event_widget_records(state_reader)

        def _apply_event_patch(entry: dict) -> None:
            if body.enabled is not None:
                entry["enabled"] = body.enabled
            if body.name is not None:
                name = body.name.strip()
                if not name:
                    raise HTTPException(status_code=400, detail="Event widget name is required")
                entry["name"] = name
            if body.event_type is not None:
                entry["event_type"] = schedules_service._validate_event_type(body.event_type)
                if not entry.get("summary") or str(entry.get("summary")).startswith("Waiting for "):
                    entry["summary"] = f"Waiting for {entry['event_type']}"
            entry["updated_at"] = utcnow().isoformat()

        def _save_event_widgets() -> None:
            schedules_service._write_event_widget_records(state_reader, event_widgets)

        updated = schedules_service._patch_document_entry(
            entries=event_widgets,
            entry_id=event_id,
            apply=_apply_event_patch,
            save=_save_event_widgets,
        )

        schedules_service._widget_or_fallback(
            widget_id,
            state_reader,
            lambda: {
                "id": widget_id,
                "type": "event",
                "name": updated.get("name", event_id),
                "title": updated.get("name", event_id),
                "status": "enabled" if updated.get("enabled", True) else "disabled",
                "description": updated.get("event_type"),
                "summary": updated.get("summary"),
                "updated_at": updated.get("updated_at"),
                "event_type": updated.get("event_type"),
                "enabled": updated.get("enabled", True),
                "trigger_count": updated.get("trigger_count", 0),
                "last_triggered_at": updated.get("last_triggered_at"),
                "last_run_status": updated.get("last_run_status", "idle"),
                "last_payload": updated.get("last_payload"),
            },
        )

        schedules_service._broadcast_widget_refresh(state_reader, request_id=request_id)
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.WIDGETS_UPDATED]
        )

    if widget_id.startswith("mcp_tool:"):
        tool_name = widget_id.split(":", 1)[1]
        if body.enabled is None:
            raise HTTPException(status_code=400, detail="enabled is required for MCP tool widgets")
        tools_doc = state_reader.get_mcp_tools()
        raw_tools = tools_doc.get("tools") if isinstance(tools_doc, dict) else None
        target = (
            next(
                (t for t in raw_tools if isinstance(t, dict) and t.get("name") == tool_name),
                None,
            )
            if isinstance(raw_tools, list)
            else None
        )
        if target is None:
            raise HTTPException(status_code=404, detail="MCP tool not found")
        target["enabled"] = body.enabled
        # The runtime owns the tool list; we only flip the enabled flag here. The
        # runtime re-reads this document on its next MCP refresh and stops/starts
        # exposing the tool to the agent accordingly.
        state_reader.save_mcp_tools(tools_doc)
        schedules_service._broadcast_widget_refresh(state_reader, request_id=request_id)
        settings_mcp.broadcast_mcp_snapshot(request_id=request_id)
        event_types: list[str] = [
            events_contracts.UiEvent.WIDGETS_UPDATED,
            events_contracts.UiEvent.MCP_SNAPSHOT,
        ]
        server_name_for_tool = str(target.get("server") or "").strip()
        if server_name_for_tool and settings_mcp._broadcast_mcp_server_detail(
            f"mcp:{server_name_for_tool}", request_id=request_id
        ):
            event_types.append(events_contracts.UiEvent.MCP_SERVER_DETAIL_UPDATED)
        return http_contracts.acknowledgement_response(request_id, event_types)

    if not widget_id.startswith("mcp:"):
        raise HTTPException(
            status_code=400,
            detail="Only schedule, event, MCP, and MCP tool widgets are mutable from the backend",
        )

    server_name = widget_id.split(":", 1)[1]
    mcp_data = settings_mcp._read_mcp_config(state_reader)
    raw_servers = mcp_data.get("mcpServers")
    if not isinstance(raw_servers, dict):
        raw_servers = {}
        mcp_data["mcpServers"] = raw_servers

    def _apply_mcp_patch(server: dict) -> None:
        requested_name = (body.name or "").strip()
        if requested_name and requested_name != server_name:
            raise HTTPException(status_code=400, detail="Renaming MCP servers is not supported")
        if body.enabled is not None:
            server["enabled"] = body.enabled
        if body.description is not None:
            server["description"] = body.description
        if body.url is not None:
            url = body.url.strip()
            if not url:
                raise HTTPException(status_code=400, detail="MCP server URL is required")
            server["url"] = url

    server = schedules_service._patch_document_entry(
        entries=raw_servers,
        entry_id=server_name,
        apply=_apply_mcp_patch,
        save=lambda: settings_mcp._write_mcp_config(state_reader, mcp_data),
    )

    schedules_service._widget_or_fallback(
        widget_id,
        state_reader,
        lambda: {
            "id": widget_id,
            "type": "mcp",
            "name": server_name,
            "title": server_name,
            "description": str(server.get("description") or ""),
            "status": "enabled" if bool(server.get("enabled", True)) else "disabled",
            "summary": str(server.get("description") or "") or f"MCP {server_name}",
            "enabled": bool(server.get("enabled", True)),
            "url": server.get("url"),
            "transport": server.get("transport", "http"),
            "updated_at": None,
        },
    )

    schedules_service._broadcast_widget_refresh(state_reader, request_id=request_id)
    settings_mcp.broadcast_mcp_snapshot(request_id=request_id)
    event_types = [events_contracts.UiEvent.WIDGETS_UPDATED, events_contracts.UiEvent.MCP_SNAPSHOT]
    if settings_mcp._broadcast_mcp_server_detail(widget_id, request_id=request_id):
        event_types.append(events_contracts.UiEvent.MCP_SERVER_DETAIL_UPDATED)
    return http_contracts.acknowledgement_response(request_id, event_types)


@router.delete(
    "/widgets/{widget_id}", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def delete_widget(
    widget_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Delete a backend-owned widget where a matching state file exists."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()

    if widget_id.startswith("mcp:"):
        server_name = widget_id.split(":", 1)[1]
        mcp_data = settings_mcp._read_mcp_config(state_reader)
        raw_servers = mcp_data.get("mcpServers")
        if not isinstance(raw_servers, dict):
            raw_servers = {}
        deleted = schedules_service._delete_document_entry(
            entries=raw_servers,
            entry_id=server_name,
            save=lambda: settings_mcp._write_mcp_config(state_reader, mcp_data),
        )
        if not deleted:
            logger.warning("MCP widget %s was already removed before delete request", widget_id)
            schedules_service._broadcast_widget_refresh(state_reader, request_id=request_id)
            settings_mcp.broadcast_mcp_snapshot(request_id=request_id)
            return http_contracts.acknowledgement_response(
                request_id,
                [events_contracts.UiEvent.WIDGETS_UPDATED, events_contracts.UiEvent.MCP_SNAPSHOT],
            )
        schedules_service._broadcast_widget_refresh(state_reader, request_id=request_id)
        settings_mcp.broadcast_mcp_snapshot(request_id=request_id)
        return http_contracts.acknowledgement_response(
            request_id,
            [events_contracts.UiEvent.WIDGETS_UPDATED, events_contracts.UiEvent.MCP_SNAPSHOT],
        )

    if not widget_id.startswith("schedule:"):
        raise HTTPException(
            status_code=400,
            detail="Only schedule and MCP widgets can be deleted from the backend",
        )

    schedule_id = widget_id.split(":", 1)[1]
    deleted = schedules_service._delete_schedule_entry(state_reader, schedule_id)
    if not deleted:
        logger.warning("Schedule widget %s was already removed before delete request", widget_id)
        schedules_service._broadcast_widget_refresh(state_reader, request_id=request_id)
        schedules_service.broadcast_schedule_snapshot(request_id=request_id)
        return http_contracts.acknowledgement_response(
            request_id,
            [events_contracts.UiEvent.WIDGETS_UPDATED, events_contracts.UiEvent.SCHEDULE_SNAPSHOT],
        )

    schedules_service._broadcast_widget_refresh(state_reader, request_id=request_id)
    schedules_service.broadcast_schedule_snapshot(request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id,
        [events_contracts.UiEvent.WIDGETS_UPDATED, events_contracts.UiEvent.SCHEDULE_SNAPSHOT],
    )


@router.get("/schedules", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_schedules(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Alias for schedule data using the requested plural path."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    schedules_service.broadcast_schedule_snapshot(request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.SCHEDULE_SNAPSHOT]
    )


@router.post("/schedules", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def create_schedule(
    body: schedules_models.ScheduleCreateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Create a new schedule entry in backend state."""
    name = body.name.strip()
    instruction = body.instruction.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Schedule name is required")
    if not instruction:
        raise HTTPException(status_code=400, detail="Schedule task is required")
    request_id = http_requests.request_correlation_id(request, x_request_id)

    state_reader = monitoring_state.get_state_reader()
    schedule_data = schedules_service._read_schedule_config(state_reader)
    conversation_id = schedules_service.validate_destination(body.conversation_id)

    created_at = utcnow().isoformat()
    schedule_id = generate_schedule_id(is_one_off=body.schedule_type.strip().lower() == "once")
    entry = schedules_service._build_schedule_entry(
        schedule_id=schedule_id,
        name=name,
        instruction=instruction,
        schedule_type=body.schedule_type,
        expression=body.expression,
        timezone_name=body.timezone,
        preference_timezone=preferences_store.load_preferences().get("timezone", "UTC"),
        enabled=body.enabled,
        created_at=created_at,
        conversation_id=conversation_id,
    )
    schedule_data[schedule_id] = entry

    state_reader.document_store.save_document(
        storage_documents.PROGENA_STATE_PREFIX, "schedule", schedule_data
    )

    refreshed = next(
        (
            widget
            for widget in state_reader.get_widgets()
            if widget["id"] == f"schedule:{schedule_id}"
        ),
        None,
    )
    if refreshed is None:
        refreshed = {
            "id": f"schedule:{schedule_id}",
            "type": "schedule",
            "name": name,
            "title": name,
            "status": "enabled" if body.enabled else "disabled",
            "description": name,
            "summary": instruction,
            "updated_at": created_at,
            "data": entry,
        }

    schedules_service._broadcast_widget_refresh(state_reader, request_id=request_id)
    schedules_service.broadcast_schedule_snapshot(request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.SCHEDULE_SNAPSHOT]
    )


@router.post(
    "/schedules/{schedule_id}/trigger",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def retrigger_schedule(
    schedule_id: str,
    body: schedules_models.ScheduleTriggerRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Reschedule a task from a natural-language description.

    Rather than editing cron fields by hand, the user describes the timing in
    plain language ("friday at 15:00"). We hand that to the Progena agent on a
    dedicated (non-chat) thread carrying just the schedule id and the free text -
    the loaded ``managing-schedules`` skill owns how a natural-language reschedule request
    gets interpreted and applied to ``/config/schedule.json``, not a hardcoded
    tool-call prescription here. We then recompute ``next_run`` (in case the
    agent didn't) and rebroadcast a snapshot.
    """

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Trigger description is required")
    if not lifecycle_state.client:
        raise HTTPException(status_code=503, detail="Progena client not initialized")
    request_id = http_requests.request_correlation_id(request, x_request_id)

    state_reader = monitoring_state.get_state_reader()
    schedule_data = schedules_service._read_schedule_config(state_reader)
    entry = schedule_data.get(schedule_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Schedule not found")

    instruction = (
        f'Reschedule the task with id "{schedule_id}" under /config/schedule.json to run: '
        f'"{text}". Do not change the task\'s message and do not create a new task. Reply '
        "with one short sentence confirming the new timing."
    )

    thread_id = workflows_runtime._resolve_action_thread_id(None)
    metadata = workflows_runtime._build_action_metadata(
        thread_id=thread_id,
        invocation_type="system",
        invocation_source="schedule.trigger",
        widget_id=f"schedule:{schedule_id}",
        root_widget_id=f"schedule:{schedule_id}",
        action="schedule.trigger",
    )
    chat_threads.upsert_thread(
        thread_id,
        invocation_type="system",
        invocation_source="schedule.trigger",
        root_widget_id=f"schedule:{schedule_id}",
        widget_id=f"schedule:{schedule_id}",
    )
    try:
        await lifecycle_state.client.send_message(instruction, metadata=metadata)
    except Exception as exc:
        chat_threads.fail_thread(thread_id, str(exc), widget_id=f"schedule:{schedule_id}")
        raise HTTPException(status_code=502, detail=f"Agent reschedule failed: {exc}") from exc
    chat_threads.complete_thread(thread_id, "schedule updated", widget_id=f"schedule:{schedule_id}")

    # The agent edits cron_expression/scheduled_at directly but may not recompute
    # next_run or the display fields; reconcile them here so the UI stays coherent.
    schedule_data = schedules_service._read_schedule_config(state_reader)
    updated = schedule_data.get(schedule_id)
    if updated is not None:
        updated["updated_at"] = utcnow().isoformat()
        if updated.get("scheduled_at"):
            updated["schedule_type"] = "once"
            updated["schedule"] = updated["scheduled_at"]
            updated.pop("cron_expression", None)
        elif updated.get("cron_expression"):
            updated["schedule_type"] = "cron"
            updated["schedule"] = updated["cron_expression"]
        next_run = Scheduler().next_run_for_entry(
            updated, preferences_store.load_preferences().get("timezone", "UTC")
        )
        updated["next_run"] = schedules_service._next_run_iso(next_run) if next_run else None
        updated["next_run_at"] = schedules_service._next_run_iso(next_run) if next_run else None
        state_reader.document_store.save_document(
            storage_documents.PROGENA_STATE_PREFIX, "schedule", schedule_data
        )

    schedules_service._broadcast_widget_refresh(state_reader, request_id=request_id)
    schedules_service.broadcast_schedule_snapshot(request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.SCHEDULE_SNAPSHOT]
    )


@router.post("/events", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def create_event_widget(
    body: schedules_models.EventWidgetCreateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Create a new persisted event widget trigger."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Event widget name is required")
    request_id = http_requests.request_correlation_id(request, x_request_id)

    state_reader = monitoring_state.get_state_reader()
    event_widgets = schedules_service._read_event_widget_records(state_reader)
    created_at = utcnow().isoformat()
    event_widget_id = f"evt_{uuid.uuid4().hex[:12]}"
    entry = schedules_service._build_event_widget_entry(
        event_widget_id=event_widget_id,
        name=name,
        event_type=body.event_type,
        enabled=body.enabled,
        created_at=created_at,
    )
    event_widgets[event_widget_id] = entry
    schedules_service._write_event_widget_records(state_reader, event_widgets)

    refreshed = next(
        (
            widget
            for widget in state_reader.get_widgets()
            if widget["id"] == f"event:{event_widget_id}"
        ),
        None,
    )
    if refreshed is None:
        refreshed = {
            "id": f"event:{event_widget_id}",
            "type": "event",
            "name": name,
            "title": name,
            "status": "enabled" if body.enabled else "disabled",
            "description": entry["event_type"],
            "summary": entry["summary"],
            "updated_at": created_at,
            "data": entry,
        }

    schedules_service._broadcast_widget_refresh(state_reader, request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.WIDGETS_UPDATED]
    )
