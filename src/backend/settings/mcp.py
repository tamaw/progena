"""Settings mcp."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

import src.backend.events.contracts as events_contracts
import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
import src.backend.http.requests as http_requests
import src.backend.monitoring.state as monitoring_state
import src.backend.schedules.models as schedules_models
import src.backend.schedules.service as schedules_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _read_mcp_config(state_reader: monitoring_state.StateReader) -> dict:
    """Read the live MCP config from the shared state document."""
    return state_reader.get_mcp_config()


def _write_mcp_config(state_reader: monitoring_state.StateReader, data: dict[str, Any]) -> None:
    """Persist the MCP config to the shared state document."""
    state_reader.save_mcp_config(data)


def _mcp_widget_items() -> list[schedules_models.WidgetResponse]:
    """The MCP-type widgets, used by the /mcp route and the mcp.snapshot event.

    List payload strips per-tool schemas and log history (``data.tools``,
    ``.logs``) — the list/filter/sort UI never reads them; only the selected
    server's detail view does, fetched on demand via ``GET /mcp/content``.
    """
    state_reader = monitoring_state.get_state_reader()
    items = []
    for widget in state_reader.get_widgets():
        if widget.get("type") != "mcp":
            continue
        response = schedules_service._widget_to_response(widget)
        lean_data = {
            key: value for key, value in response.data.items() if key not in ("tools", "logs")
        }
        items.append(response.model_copy(update={"data": lean_data, "logs": []}))
    return items


def _broadcast_mcp_server_detail(widget_id: str, *, request_id: str | None = None) -> bool:
    """Re-publish one MCP server's full detail (tools + logs) on the UI stream.

    Returns ``False`` (no broadcast) when ``widget_id`` no longer names a live
    MCP widget, so callers can 404 or silently skip as appropriate.
    """
    state_reader = monitoring_state.get_state_reader()
    widget = next(
        (
            w
            for w in state_reader.get_widgets()
            if w.get("type") == "mcp" and w.get("id") == widget_id
        ),
        None,
    )
    if widget is None:
        return False
    response = schedules_service._widget_to_response(widget)
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.MCP_SERVER_DETAIL_UPDATED,
        response.model_dump(mode="json"),
        request_id=request_id,
    )
    return True


@router.get("/mcp", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_mcp_servers(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Acknowledge MCP-list requests and publish the snapshot on the UI stream.

    Mirrors the schedule/agent pattern: a dedicated typed event (``mcp.snapshot``)
    feeds a dedicated store slice, so the Tools page renders from the realtime
    store instead of filtering the generic widget list.
    """
    request_id = http_requests.request_correlation_id(request, x_request_id)
    items = _mcp_widget_items()
    broadcast_mcp_snapshot(items, request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.MCP_SNAPSHOT]
    )


@router.get("/mcp/content", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_mcp_server_detail(
    id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Acknowledge a request for one MCP server's full detail and publish it.

    Mirrors ``GET /workflows/content``/``GET /instructions/content``: the list
    (``mcp.snapshot``) omits per-tool schemas and log history; the selected
    server's full body (tools + logs) is fetched on demand here.
    """
    request_id = http_requests.request_correlation_id(request, x_request_id)
    if not _broadcast_mcp_server_detail(id, request_id=request_id):
        raise HTTPException(status_code=404, detail=f"MCP server '{id}' not found")
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.MCP_SERVER_DETAIL_UPDATED]
    )


def broadcast_mcp_snapshot(
    items: list[schedules_models.WidgetResponse] | None = None, *, request_id: str | None = None
) -> events_contracts.UiEventEnvelope:
    """Broadcast the current MCP server list to /api/ui/stream subscribers.

    Emitted after the Tools page requests the list and whenever an MCP config
    change is detected (backend edit, or runtime/agent write surfaced via the
    Postgres NOTIFY watcher), so the Tools page reflects changes live. Callers
    that already built the list (e.g. the GET handler) can pass it in to avoid
    a second ``get_widgets()`` pass and keep the snapshot consistent with the
    HTTP response.
    """
    source = _mcp_widget_items() if items is None else items
    payload = [item.model_dump(mode="json") for item in source]
    return events_stream.broadcast_ui_event(
        events_contracts.UiEvent.MCP_SNAPSHOT, {"items": payload}, request_id=request_id
    )
