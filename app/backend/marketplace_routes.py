"""Marketplace sources API routes — the agent-editable ``marketplaces`` document."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.progenitor.marketplaces import validate_marketplaces_payload
from app.progenitor.plugins.sources import PluginFetchError, fetch_marketplace

from .chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from .marketplace_service import (
    get_cached_catalog,
    load_marketplaces,
    save_marketplaces,
    set_cached_catalog,
)
from .request_utils import request_correlation_id as _request_correlation_id
from .ui_routes import broadcast_marketplaces_snapshot, broadcast_plugin_catalog_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui/marketplaces", tags=["marketplaces"])


def _catalog_to_dict(catalog: Any) -> dict[str, Any]:
    """Serialize a MarketplaceCatalog to a plain dict."""
    return {
        "name": getattr(catalog, "name", ""),
        "owner": getattr(catalog, "owner", {}),
        "plugins": getattr(catalog, "plugins", []),
        "description": getattr(catalog, "description", None),
        "version": getattr(catalog, "version", None),
        "metadata": getattr(catalog, "metadata", {}),
        "repository": getattr(catalog, "repository", None),
        "commit_sha": getattr(catalog, "commit_sha", None),
    }


@router.get("", status_code=202, response_model=UiRequestAcknowledgement)
async def get_marketplaces(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Acknowledge a read and publish the marketplaces snapshot on the UI stream."""
    request_id = _request_correlation_id(request, x_request_id)
    snapshot = await asyncio.to_thread(load_marketplaces)
    broadcast_marketplaces_snapshot(snapshot, request_id=request_id)
    return acknowledgement_response(request_id, ["marketplaces.snapshot"])


@router.put("", status_code=202, response_model=UiRequestAcknowledgement)
async def replace_marketplaces(
    payload: dict[str, Any],
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Validate and persist the full marketplaces document."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        normalized = validate_marketplaces_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved = await asyncio.to_thread(save_marketplaces, normalized["marketplaces"])
    broadcast_marketplaces_snapshot(saved, request_id=request_id)
    return acknowledgement_response(request_id, ["marketplaces.snapshot"])


@router.get("/{name}/plugins", status_code=202, response_model=UiRequestAcknowledgement)
async def browse_marketplace_plugins(
    name: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Fetch the named marketplace's catalog and broadcast it."""
    request_id = _request_correlation_id(request, x_request_id)

    # Resolve the registered source URL.
    snapshot = await asyncio.to_thread(load_marketplaces)
    entry = snapshot.get("marketplaces", {}).get(name)
    source_url = str(entry.get("source") or "").strip() if isinstance(entry, dict) else None

    if not source_url:
        raise HTTPException(status_code=404, detail=f"marketplace '{name}' not found")

    catalog = get_cached_catalog(name)
    if catalog is None:
        try:
            catalog = await fetch_marketplace(source_url)
        except PluginFetchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        set_cached_catalog(name, catalog)

    broadcast_plugin_catalog_snapshot(name, _catalog_to_dict(catalog), request_id=request_id)
    return acknowledgement_response(request_id, ["plugin.catalog.snapshot"])
