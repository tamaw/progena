"""Monitoring logs."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Header, HTTPException, Query, Request

import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
import src.backend.http.requests as http_requests
import src.backend.monitoring.state as monitoring_state
from src.backend.events import contracts as events_contracts

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/logs", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_logs(
    request: Request,
    level: str | None = None,
    search: str | None = None,
    source: str | None = None,
    folder: str | None = None,
    file_path: str | None = Query(default=None, alias="file"),
    start_date: str | None = Query(default=None, alias="start"),
    end_date: str | None = Query(default=None, alias="end"),
    after: str | None = None,
    limit: int = 500,
    format: str = "text",
    search_mode: str = "plain",
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """
    Get agent logs with optional filtering.

    Args:
        level: Filter by log level (debug, info, warn, error)
        search: Search string to filter logs
        limit: Maximum number of lines to return (default 500)
        format: Response format - "text" for raw logs, "json" for structured

    Returns recent logs from the agent.
    """
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()
    if search_mode not in {"plain", "regex"}:
        raise HTTPException(status_code=400, detail="search_mode must be 'plain' or 'regex'")

    source_groups = state_reader.get_log_sources()
    default_end = datetime.now(UTC).date()
    default_start = default_end - timedelta(days=6)
    start_date = start_date or default_start.isoformat()
    end_date = end_date or default_end.isoformat()
    if not any(value is not None for value in (level, search, source, folder, file_path)):
        candidate_path = None
        for log_path in state_reader._iter_log_files():
            candidate_result = state_reader.query_logs(
                folder=state_reader._log_group(log_path),
                file_path=state_reader._relative_log_path(log_path),
                start_date=start_date,
                end_date=end_date,
                limit=1,
            )
            if candidate_result["entries"]:
                candidate_path = log_path
                break

        if candidate_path is None:
            candidate_path = next(iter(state_reader._iter_log_files()), None)

        if candidate_path is not None:
            folder = state_reader._log_group(candidate_path)
            file_path = state_reader._relative_log_path(candidate_path)
        else:
            for group in source_groups:
                options = group.get("options", [])
                if options:
                    folder = str(group.get("id") or "")
                    file_path = str(options[0].get("id") or "")
                    break

    try:
        result = state_reader.query_logs(
            level=level,
            search=search,
            source=source,
            folder=folder,
            file_path=file_path,
            start_date=start_date,
            end_date=end_date,
            after=after,
            limit=limit,
            search_mode=search_mode,
        )
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {exc}") from exc
    page_payload = {
        **result,
        "filters": {
            "level": level,
            "search": search,
            "source": source,
            "folder": folder,
            "file": file_path,
            "start": start_date,
            "end": end_date,
            "after": after,
            "limit": limit,
            "format": format,
            "search_mode": search_mode,
        },
        "active_selection": {
            "source": source or "",
            "group": folder or "",
            "file": file_path or "",
        },
        "date_range": {"start": start_date, "end": end_date},
        "cursor": {
            "after": after,
            "newest_cursor": result["newest_cursor"],
            "cursor_found": result["cursor_found"],
        },
        "source_groups": source_groups,
        "folders": state_reader.get_log_folders(),
    }
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.LOGS_PAGE_LOADED, page_payload, request_id=request_id
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.LOGS_PAGE_LOADED]
    )


@router.get(
    "/logs/sources", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def get_log_sources(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Return available log groups/files without loading log entries."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()
    source_groups = state_reader.get_log_sources()
    folders = state_reader.get_log_folders()
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.LOGS_SOURCES_LOADED,
        {"source_groups": source_groups, "folders": folders},
        request_id=request_id,
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.LOGS_SOURCES_LOADED]
    )


@router.get("/logs/export", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def export_logs(
    request: Request,
    level: str | None = None,
    search: str | None = None,
    source: str | None = None,
    folder: str | None = None,
    file_path: str | None = Query(default=None, alias="file"),
    start_date: str | None = Query(default=None, alias="start"),
    end_date: str | None = Query(default=None, alias="end"),
    search_mode: str = "plain",
    format: str = "text",
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Export logs using the current filters.

    The export data is broadcast as chunked events (``logs.export.chunk``) on the
    UI event stream. The frontend reconstructs the download from these chunks.
    """
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()
    if search_mode not in {"plain", "regex"}:
        raise HTTPException(status_code=400, detail="search_mode must be 'plain' or 'regex'")

    default_end = datetime.now(UTC).date()
    default_start = default_end - timedelta(days=6)
    start_date = start_date or default_start.isoformat()
    end_date = end_date or default_end.isoformat()

    try:
        result = state_reader.query_logs(
            level=level,
            search=search,
            source=source,
            folder=folder,
            file_path=file_path,
            start_date=start_date,
            end_date=end_date,
            limit=0,
            search_mode=search_mode,
        )
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {exc}") from exc

    entries = result["entries"]
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.LOGS_EXPORT_STARTED,
        {"format": format, "total": len(entries)},
        request_id=request_id,
    )

    # Broadcast entries as chunked events (batches of 100)
    chunk_size = 100
    for i in range(0, len(entries), chunk_size):
        chunk = entries[i : i + chunk_size]
        events_stream.broadcast_ui_event(
            events_contracts.UiEvent.LOGS_EXPORT_CHUNK,
            {"format": format, "index": i // chunk_size, "entries": chunk, "total": len(entries)},
            request_id=request_id,
        )

    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.LOGS_EXPORT_COMPLETED,
        {"format": format, "total": len(entries)},
        request_id=request_id,
    )

    event_types: list[str] = [events_contracts.UiEvent.LOGS_EXPORT_STARTED]
    if entries:
        event_types.append(events_contracts.UiEvent.LOGS_EXPORT_CHUNK)
    event_types.append(events_contracts.UiEvent.LOGS_EXPORT_COMPLETED)
    return http_contracts.acknowledgement_response(request_id, event_types)
