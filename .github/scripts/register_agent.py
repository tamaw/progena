#!/usr/bin/env python3
"""Register a deployed agent in the progenitor agents.json registry.

Performs a health check for the running Docker container and writes a
minimal agent entry in the progenitor registry file.

Environment variables:
  AGENT_NAME       Name of the deployed agent (required).
  PORT             Port the agent listens on (required).
  REGISTRY_FILE    Path to agents.json on the host (default: /var/lib/progenitor/data/config/agents.json).
  CONTAINER_NAME   Docker container name (default: progenitor-agent-{AGENT_NAME}).
  DOCKER_HOSTNAME  Docker hostname for endpoint/health check (optional; auto-detected when unset).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_DEFAULT_REGISTRY = "/var/lib/progenitor/data/config/agents.json"


def _resolve_container_hostname(container_name: str) -> str:
    """Resolve the Docker hostname configured for a container."""
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.Config.Hostname}}", container_name],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not inspect Docker hostname for {container_name}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    hostname = result.stdout.strip()
    if not hostname:
        raise RuntimeError(f"Container {container_name} did not return a Docker hostname")
    return hostname


def _check_agent_health(container_name: str, docker_hostname: str, port: int) -> None:
    """Run /health check from inside the container using its Docker hostname."""
    script = (
        "import httpx; "
        f"r = httpx.get('http://{docker_hostname}:{port}/health', timeout=10); "
        "r.raise_for_status()"
    )
    result = subprocess.run(
        ["docker", "exec", container_name, "python", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Health check failed for {container_name} at http://{docker_hostname}:{port}/health: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def _normalize_entry(raw_entry: dict) -> dict | None:
    """Normalize a registry entry to config-style fields only."""
    normalized: dict = {}
    url = raw_entry.get("url")
    if isinstance(url, str) and url.strip():
        normalized["url"] = url.strip()
    headers = raw_entry.get("headers")
    if isinstance(headers, dict):
        cleaned_headers = {
            str(k): str(v)
            for k, v in headers.items()
            if str(k).strip()
        }
        if cleaned_headers:
            normalized["headers"] = cleaned_headers
    return normalized or None


def _load_registry(registry_path: Path) -> dict:
    """Load agents.json and normalize to a name-keyed map."""
    if registry_path.exists():
        try:
            with registry_path.open(encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                normalized: dict[str, dict] = {}

                raw_agents = data.get("agents")
                if isinstance(raw_agents, list):
                    for item in raw_agents:
                        if not isinstance(item, dict):
                            continue
                        name = str(item.get("name") or "").strip()
                        if not name:
                            continue
                        entry = _normalize_entry(item)
                        if entry:
                            normalized[name] = entry

                for name, cfg in data.items():
                    if name == "agents" or not isinstance(cfg, dict):
                        continue
                    entry = _normalize_entry(cfg)
                    if entry:
                        normalized[str(name)] = entry

                return normalized
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARNING: Failed to read registry {registry_path}: {exc}", file=sys.stderr)
    return {}


def _save_registry(registry_path: Path, data: dict) -> None:
    """Write agents.json atomically."""
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = registry_path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    tmp_path.replace(registry_path)


def _build_entry(
    agent_name: str,
    port: int,
    docker_hostname: str,
    existing: dict | None,
) -> dict:
    """Build a config-style registry entry for this agent."""
    entry: dict = {"url": f"http://{docker_hostname}:{port}"}
    if existing and isinstance(existing.get("headers"), dict):
        headers = {str(k): str(v) for k, v in existing["headers"].items()}
        if headers:
            entry["headers"] = headers
    return entry


def main() -> int:
    agent_name = os.environ.get("AGENT_NAME", "").strip()
    if not agent_name:
        print("ERROR: AGENT_NAME is required", file=sys.stderr)
        return 1

    port_str = os.environ.get("PORT", "").strip()
    if not port_str or not port_str.isdigit():
        print("ERROR: PORT must be a numeric value", file=sys.stderr)
        return 1
    port = int(port_str)

    registry_path = Path(os.environ.get("REGISTRY_FILE", _DEFAULT_REGISTRY))
    container_name = os.environ.get("CONTAINER_NAME", f"progenitor-agent-{agent_name}")
    docker_hostname = os.environ.get("DOCKER_HOSTNAME", "").strip()
    if not docker_hostname:
        try:
            docker_hostname = _resolve_container_hostname(container_name)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    print(
        f"Registering agent '{agent_name}' (port={port}, docker_hostname={docker_hostname}) "
        f"in {registry_path}"
    )

    try:
        _check_agent_health(container_name, docker_hostname, port)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Health check passed")

    # Load current registry
    registry = _load_registry(registry_path)
    existing = registry.get(agent_name) if isinstance(registry.get(agent_name), dict) else None

    # Build updated entry
    entry = _build_entry(agent_name, port, docker_hostname, existing)

    registry[agent_name] = entry

    # Save registry
    _save_registry(registry_path, registry)
    print(f"Registered agent '{agent_name}' → {entry['url']} (total agents: {len(registry)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
