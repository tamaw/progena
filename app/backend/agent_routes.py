"""Agent registry routes for backend-managed deploy integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .state_reader import get_state_reader

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agents"])
_registry_write_lock = asyncio.Lock()


class AgentRegisterRequest(BaseModel):
    """Payload for registering or updating a deployed agent."""

    name: str
    url: str
    headers: dict[str, str] | None = None


class AgentRegisterResponse(BaseModel):
    """Response returned after a registry upsert."""

    name: str
    url: str
    total: int


def _load_registry_map() -> dict[str, dict[str, Any]]:
    state_reader = get_state_reader()
    loaded = state_reader._read_json_from_path(  # noqa: SLF001 - shared persistence bridge
        state_reader.config_dir / "agents.json"
    )
    if not isinstance(loaded, dict):
        return {}

    registry: dict[str, dict[str, Any]] = {}

    raw_agents = loaded.get("agents")
    if isinstance(raw_agents, list):
        for item in raw_agents:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            url = item.get("url")
            if not name or not isinstance(url, str) or not url.strip():
                continue
            entry: dict[str, Any] = {"url": url.strip()}
            headers = item.get("headers")
            if isinstance(headers, dict):
                clean_headers = {str(k): str(v) for k, v in headers.items() if str(k).strip()}
                if clean_headers:
                    entry["headers"] = clean_headers
            registry[name] = entry

    for name, value in loaded.items():
        if name == "agents" or not isinstance(value, dict):
            continue
        url = value.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        registry_entry: dict[str, Any] = {"url": url.strip()}
        headers = value.get("headers")
        if isinstance(headers, dict):
            clean_headers = {str(k): str(v) for k, v in headers.items() if str(k).strip()}
            if clean_headers:
                registry_entry["headers"] = clean_headers
        registry[str(name)] = registry_entry

    return registry


def _write_registry_map(registry: dict[str, dict[str, Any]]) -> None:
    state_reader = get_state_reader()
    registry_path = state_reader.config_dir / "agents.json"
    if not state_reader._write_json_to_path(  # noqa: SLF001 - shared persistence bridge
        registry_path, registry
    ):
        raise RuntimeError("Backend document store is not configured")
    state_reader.invalidate(registry_path)


@router.post("/agents/register", response_model=AgentRegisterResponse)
async def register_agent(payload: AgentRegisterRequest) -> AgentRegisterResponse:
    """Register or update a deployed agent URL in the live registry."""

    name = payload.name.strip()
    url = payload.url.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Agent name is required")
    if not url:
        raise HTTPException(status_code=400, detail="Agent url is required")

    async with _registry_write_lock:
        registry = _load_registry_map()
        existing = registry.get(name) if isinstance(registry.get(name), dict) else {}
        entry: dict[str, Any] = {"url": url}

        existing_headers = existing.get("headers") if isinstance(existing, dict) else None
        if payload.headers is not None:
            clean_headers = {
                str(key): str(value) for key, value in payload.headers.items() if str(key).strip()
            }
            if clean_headers:
                entry["headers"] = clean_headers
        elif isinstance(existing_headers, dict):
            clean_headers = {
                str(key): str(value) for key, value in existing_headers.items() if str(key).strip()
            }
            if clean_headers:
                entry["headers"] = clean_headers

        registry[name] = entry
        _write_registry_map(registry)

    logger.info("Registered agent %s -> %s", name, url)
    return AgentRegisterResponse(name=name, url=url, total=len(registry))
