"""Helpers for loading and resolving MCP server configuration."""

from __future__ import annotations

import logging
import os
from string import Template
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..config import MCPServerConfig


def resolve_required_env_template(value: str) -> tuple[str | None, list[str]]:
    """Resolve $VAR/${VAR} placeholders, returning missing variables when unresolved."""
    template = Template(value)
    missing: list[str] = []

    for match in template.pattern.finditer(value):
        var_name = match.group("named") or match.group("braced")
        if var_name is not None and os.getenv(var_name) is None:
            missing.append(var_name)

    if missing:
        return None, missing

    return template.safe_substitute(os.environ), []


def normalize_mcp_config_payload(raw_data: dict[str, object] | None) -> dict[str, object]:
    """Normalize a raw MCP config payload into the persisted document shape."""
    data = dict(raw_data) if isinstance(raw_data, dict) else {}
    raw_servers = data.get("mcpServers")
    if not isinstance(raw_servers, dict):
        data["mcpServers"] = {}
    return data


def load_mcp_servers(raw_data: dict[str, object] | None) -> list[MCPServerConfig]:
    """Load external MCP server definitions from the persisted MCP state document."""
    from ..config import MCPServerConfig

    normalized = normalize_mcp_config_payload(raw_data)
    raw_servers = normalized.get("mcpServers")
    if raw_servers is None:
        return []
    if not isinstance(raw_servers, dict):
        return []

    servers: list[MCPServerConfig] = []
    for server_name, raw_server in raw_servers.items():
        if not isinstance(server_name, str) or not isinstance(raw_server, dict):
            continue

        transport = str(raw_server.get("transport") or "http").strip().lower()
        if transport != "http":
            logger.warning(
                "Skipping MCP server '%s': unsupported transport '%s' (remote HTTP only)",
                server_name,
                transport,
            )
            continue

        raw_url = str(raw_server.get("url") or raw_server.get("endpoint") or "").strip()
        if not raw_url:
            logger.warning("Skipping MCP server '%s': missing url", server_name)
            continue

        resolved_url, missing_url_vars = resolve_required_env_template(raw_url)
        if resolved_url is None:
            logger.warning(
                "Skipping MCP server '%s': unresolved env vars in url: %s",
                server_name,
                ", ".join(sorted(set(missing_url_vars))),
            )
            continue

        headers: dict[str, str] = {}
        raw_headers = raw_server.get("headers")
        if isinstance(raw_headers, dict):
            for header_name, header_value in raw_headers.items():
                if not isinstance(header_name, str):
                    continue
                resolved_header, missing_header_vars = resolve_required_env_template(
                    str(header_value)
                )
                if resolved_header is None:
                    logger.info(
                        "Skipping header '%s' for MCP server '%s': missing env vars %s",
                        header_name,
                        server_name,
                        ", ".join(sorted(set(missing_header_vars))),
                    )
                    continue
                headers[header_name] = resolved_header

        servers.append(
            MCPServerConfig(
                name=server_name,
                url=resolved_url,
                enabled=bool(raw_server.get("enabled", True)),
                transport=transport,
                headers=headers,
                description=str(raw_server.get("description") or "").strip(),
            )
        )

    return servers
