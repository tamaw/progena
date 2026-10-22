"""Installed plugins API routes — the agent-owned ``plugins`` document."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

import src.backend.events.contracts as events_contracts
import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
import src.backend.storage.documents as storage_documents
from src.backend.http.requests import request_correlation_id as _request_correlation_id
from src.backend.plugins.marketplaces import (
    get_cached_catalog,
    load_marketplaces,
    set_cached_catalog,
)
from src.backend.storage.documents import PROGENA_STATE_PREFIX, get_default_document_store
from src.progena.config import default_plugins_config_payload
from src.progena.documents.content import content_document_key
from src.progena.plugins.schema import validate_plugins_payload
from src.progena.plugins.sources import (
    PluginFetchError,
    fetch_marketplace,
    fetch_plugin_skill_content,
    list_plugin_skill_names,
    resolve_relative_plugin_source,
)

# Imported at module level so tests can monkeypatch the fetch callable.

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui/plugins", tags=["plugins"])


def _load_plugins_document() -> dict[str, Any]:
    """Read the installed-plugins document from the shared store."""
    default = default_plugins_config_payload()
    document = get_default_document_store().get_document(PROGENA_STATE_PREFIX, "plugins", default)
    payload = document.payload if document is not None else default
    return payload if isinstance(payload, dict) else default


def _save_plugins_document(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist the installed-plugins document after validating it."""
    validated = validate_plugins_payload(payload)
    get_default_document_store().save_document(PROGENA_STATE_PREFIX, "plugins", validated)
    return validated


def _marketplace_source_url(marketplace_name: str) -> str | None:
    """Look up a registered marketplace's own source string by name."""
    snapshot = load_marketplaces()
    entry = snapshot.get("marketplaces", {}).get(marketplace_name)
    if not isinstance(entry, dict):
        return None
    return str(entry.get("source") or "").strip() or None


async def _resolve_catalog(marketplace_name: str) -> Any:
    """Return a cached or freshly-fetched marketplace catalog."""
    cached = get_cached_catalog(marketplace_name)
    if cached is not None:
        return cached

    source_url = _marketplace_source_url(marketplace_name)
    if not source_url:
        from src.progena.plugins.sources import PluginFetchError

        raise PluginFetchError(f"marketplace '{marketplace_name}' not found")

    catalog = await fetch_marketplace(source_url)
    set_cached_catalog(marketplace_name, catalog)
    return catalog


@router.get("", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_plugins(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
    """Acknowledge a read and publish the plugins snapshot on the UI stream."""
    request_id = _request_correlation_id(request, x_request_id)
    snapshot = await asyncio.to_thread(_load_plugins_document)
    broadcast_plugins_snapshot(snapshot, request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["plugins.snapshot"])


@router.post("", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def install_plugin(
    body: dict[str, Any],
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
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

    resolved_source = plugin_entry.get("source")
    marketplace_source_url = _marketplace_source_url(marketplace_name)
    marketplace_commit_sha = getattr(catalog, "commit_sha", None)
    if isinstance(resolved_source, str) and not resolved_source.startswith(("http://", "https://")):
        if not marketplace_source_url:
            raise HTTPException(
                status_code=400, detail=f"marketplace '{marketplace_name}' not found"
            )
        plugin_root = str(getattr(catalog, "metadata", {}).get("pluginRoot", "") or "")
        try:
            resolved_source = resolve_relative_plugin_source(
                marketplace_source_url,
                resolved_source,
                plugin_root,
                marketplace_commit_sha,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    new_entry: dict[str, Any] = {
        "source": resolved_source,
        "version": plugin_entry.get("version"),
        "marketplace_source": marketplace_source_url,
        "marketplace_plugin_source": plugin_entry.get("source"),
        "marketplace_commit_sha": marketplace_commit_sha,
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
    return http_contracts.acknowledgement_response(request_id, ["plugins.snapshot"])


@router.get("/{marketplace}/{plugin}/preview")
async def preview_plugin_skills(
    marketplace: str,
    plugin: str,
) -> dict[str, Any]:
    """Return skill names discoverable from an uninstalled marketplace plugin."""
    source = _marketplace_source_url(marketplace)
    if not source:
        raise HTTPException(status_code=404, detail=f"marketplace '{marketplace}' not found")
    try:
        catalog = await _resolve_catalog(marketplace)
        if not any(
            isinstance(entry, dict) and entry.get("name") == plugin for entry in catalog.plugins
        ):
            raise HTTPException(status_code=404, detail=f"plugin '{plugin}' not found")
        skills = await list_plugin_skill_names(catalog, plugin)
    except PluginFetchError as exc:
        status_code = 429 if "rate limit exceeded" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {"marketplace": marketplace, "plugin": plugin, "skills": skills}


@router.delete(
    "/{marketplace}/{plugin}",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def uninstall_plugin(
    marketplace: str,
    plugin: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
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
    return http_contracts.acknowledgement_response(request_id, ["plugins.snapshot"])


@router.get(
    "/{marketplace}/{plugin}/skills/{skill_name}",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def get_plugin_skill_content(
    marketplace: str,
    plugin: str,
    skill_name: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
    """Publish one installed plugin skill's ``SKILL.md`` body for preview.

    Reads the same ``skill:<marketplace>__<plugin>/<skill>/SKILL.md`` content
    document the agent's ``/skills/<segment>/`` mount serves (see
    ``plugins/install.py``), so the preview always matches what the agent
    actually reads.
    """
    request_id = _request_correlation_id(request, x_request_id)
    segment = f"{marketplace}__{plugin}"
    key = content_document_key("skill", f"{segment}/{skill_name}/SKILL.md")
    document = get_default_document_store().get_document(PROGENA_STATE_PREFIX, key, None)
    if (
        document is None
        or not isinstance(document.payload, dict)
        or not isinstance(document.payload.get("content"), str)
    ):
        try:
            catalog = await _resolve_catalog(marketplace)
            content = await fetch_plugin_skill_content(catalog, plugin, skill_name)
        except PluginFetchError as exc:
            status_code = 429 if "rate limit exceeded" in str(exc).lower() else 404
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        payload = {
            "marketplace": marketplace,
            "plugin": plugin,
            "skill": skill_name,
            "content": content,
        }
        events_stream.broadcast_ui_event(
            "plugin.skill.content.updated", payload, request_id=request_id
        )
        return http_contracts.acknowledgement_response(request_id, ["plugin.skill.content.updated"])

    payload = {
        "marketplace": marketplace,
        "plugin": plugin,
        "skill": skill_name,
        "content": document.payload["content"],
    }
    events_stream.broadcast_ui_event("plugin.skill.content.updated", payload, request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["plugin.skill.content.updated"])


def broadcast_plugins_snapshot(
    plugins: dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
) -> events_contracts.UiEventEnvelope:
    """Broadcast the current installed-plugins document to /api/ui/stream subscribers.

    Emitted after backend-side plugin mutations and whenever the ``plugins`` state
    document changes (surfaced via the Postgres NOTIFY watcher), so the Plugins
    page reflects changes live without refetching.
    """
    if plugins is None:
        default = default_plugins_config_payload()
        document = storage_documents.get_default_document_store().get_document(
            storage_documents.PROGENA_STATE_PREFIX, "plugins", default
        )
        raw = document.payload if document is not None else default
        plugins = raw if isinstance(raw, dict) else default
    return events_stream.broadcast_ui_event("plugins.snapshot", plugins, request_id=request_id)
