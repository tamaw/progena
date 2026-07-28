"""Installed plugins API routes — the agent-owned ``plugins`` document."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.progenitor.agent.state_backend import validate_plugins_document
from app.progenitor.config import default_plugins_config_payload

# Imported at module level so tests can monkeypatch the fetch callable.
from app.progenitor.plugins.sources import fetch_marketplace

from .chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from .document_store import PROGENITOR_STATE_PREFIX, get_default_document_store
from .marketplace_service import get_cached_catalog, load_marketplaces, set_cached_catalog
from .request_utils import request_correlation_id as _request_correlation_id
from .ui_routes import broadcast_plugins_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui/plugins", tags=["plugins"])


def _load_plugins_document() -> dict[str, Any]:
    """Read the installed-plugins document from the shared store."""
    default = default_plugins_config_payload()
    document = get_default_document_store().get_document(
        PROGENITOR_STATE_PREFIX, "plugins", default
    )
    payload = document.payload if document is not None else default
    return payload if isinstance(payload, dict) else default


def _save_plugins_document(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist the installed-plugins document after validating it."""
    validate_plugins_document(json.dumps(payload))
    get_default_document_store().save_document(PROGENITOR_STATE_PREFIX, "plugins", payload)
    return payload


async def _resolve_catalog(marketplace_name: str) -> Any:
    """Return a cached or freshly-fetched marketplace catalog."""
    cached = get_cached_catalog(marketplace_name)
    if cached is not None:
        return cached

    snapshot = load_marketplaces()
    source_url: str | None = None
    for src in snapshot.get("sources", []):
        if isinstance(src, dict) and src.get("name") == marketplace_name:
            source_url = str(src.get("source") or "").strip()
            break

    if not source_url:
        from app.progenitor.plugins.sources import PluginFetchError

        raise PluginFetchError(f"marketplace '{marketplace_name}' not found")

    catalog = await fetch_marketplace(source_url)
    set_cached_catalog(marketplace_name, catalog)
    return catalog


@router.get("", status_code=202, response_model=UiRequestAcknowledgement)
async def get_plugins(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Acknowledge a read and publish the plugins snapshot on the UI stream."""
    request_id = _request_correlation_id(request, x_request_id)
    snapshot = await asyncio.to_thread(_load_plugins_document)
    broadcast_plugins_snapshot(snapshot, request_id=request_id)
    return acknowledgement_response(request_id, ["plugins.snapshot"])


@router.post("", status_code=202, response_model=UiRequestAcknowledgement)
async def install_plugin(
    body: dict[str, Any],
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Resolve a plugin from its marketplace catalog and add it to the
    installed-plugins document with ``status: pending``. The reconciler
    will materialize the skill documents asynchronously.
    """
    request_id = _request_correlation_id(request, x_request_id)
    marketplace_name = str(body.get("marketplace") or "").strip()
    plugin_name = str(body.get("plugin") or "").strip()

    if not marketplace_name or not plugin_name:
        raise HTTPException(status_code=400, detail="missing 'marketplace' or 'plugin'")

    catalog = None
    try:
        catalog = await _resolve_catalog(marketplace_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    plugin_entry: dict[str, Any] | None = None
    for p in getattr(catalog, "plugins", []):
        if isinstance(p, dict) and p.get("name") == plugin_name:
            plugin_entry = p
            break

    if plugin_entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"plugin '{plugin_name}' not found in marketplace '{marketplace_name}'",
        )

    new_entry: dict[str, Any] = {
        "source": plugin_entry.get("source"),
        "version": plugin_entry.get("version"),
        "status": "pending",
    }
    new_entry = {k: v for k, v in new_entry.items() if v is not None}

    current = _load_plugins_document()
    plugins = dict(current.get("plugins", {}))
    plugins[f"{marketplace_name}/{plugin_name}"] = new_entry
    merged = {**current, "plugins": plugins}

    try:
        saved = await asyncio.to_thread(_save_plugins_document, merged)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    broadcast_plugins_snapshot(saved, request_id=request_id)
    return acknowledgement_response(request_id, ["plugins.snapshot"])


@router.delete("/{marketplace}/{plugin}", status_code=202, response_model=UiRequestAcknowledgement)
async def uninstall_plugin(
    marketplace: str,
    plugin: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Remove a plugin from the installed-plugins document."""
    request_id = _request_correlation_id(request, x_request_id)
    current = _load_plugins_document()
    plugins = dict(current.get("plugins", {}))
    key = f"{marketplace}/{plugin}"
    if key not in plugins:
        raise HTTPException(status_code=404, detail=f"plugin '{key}' is not installed")

    del plugins[key]
    merged = {**current, "plugins": plugins}

    try:
        saved = await asyncio.to_thread(_save_plugins_document, merged)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    broadcast_plugins_snapshot(saved, request_id=request_id)
    return acknowledgement_response(request_id, ["plugins.snapshot"])
