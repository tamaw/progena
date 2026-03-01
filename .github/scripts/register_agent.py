#!/usr/bin/env python3
"""Register a deployed agent in the progenitor agents.json registry.

Reads the agent card from the running Docker container and writes or
replaces the agent's entry in the progenitor registry file.

Environment variables:
  AGENT_NAME      Name of the deployed agent (required).
  PORT            Port the agent listens on (required).
  REGISTRY_FILE   Path to agents.json on the host (default: /var/lib/progenitor/data/config/agents.json).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


_DEFAULT_REGISTRY = "/var/lib/progenitor/data/config/agents.json"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _fetch_agent_card(container_name: str, port: int) -> dict | None:
    """Fetch /.well-known/agent.json from the running container."""
    script = (
        "import httpx, sys; "
        f"r = httpx.get('http://localhost:{port}/.well-known/agent.json', timeout=10); "
        "r.raise_for_status(); "
        "print(r.text)"
    )
    result = subprocess.run(
        ["docker", "exec", container_name, "python", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(
            f"WARNING: Could not fetch agent card from {container_name}: "
            f"{result.stderr.strip() or result.stdout.strip()}",
            file=sys.stderr,
        )
        return None
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        print(f"WARNING: Failed to parse agent card JSON: {exc}", file=sys.stderr)
        return None


def _load_registry(registry_path: Path) -> dict:
    """Load agents.json, returning an empty registry on failure."""
    if registry_path.exists():
        try:
            with registry_path.open(encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARNING: Failed to read registry {registry_path}: {exc}", file=sys.stderr)
    return {"agents": []}


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
    card: dict | None,
    existing: dict | None,
) -> dict:
    """Build the registry entry for this agent."""
    now = _utc_now_iso()
    container_hostname = f"progenitor-agent-{agent_name}"
    endpoint = f"http://{container_hostname}:{port}"
    agent_card_url = f"{endpoint}/.well-known/agent.json"

    # Extract metadata from agent card when available
    name = agent_name
    description = f"A2A agent: {agent_name}"
    version = "0.1.0"
    capabilities: list[str] = []

    if card:
        name = card.get("name", agent_name)
        description = card.get("description", description)
        version = card.get("version", version)
        # A2A agent card skills → capabilities
        skills = card.get("skills") or []
        for skill in skills:
            if isinstance(skill, dict):
                skill_id = skill.get("id") or skill.get("name") or ""
                if skill_id:
                    capabilities.append(skill_id)

    # Determine entrypoint: prefer agent.py, keep existing if set
    entrypoint = f"app/agents/{agent_name}/agent.py"
    if existing:
        entrypoint = existing.get("entrypoint", entrypoint)

    entry: dict = {
        "name": agent_name,
        "kind": "a2a_agent",
        "version": version,
        "description": description,
        "capabilities": capabilities,
        "entrypoint": entrypoint,
        "transport": "http",
        "port": port,
        "endpoint": endpoint,
        "agent_card_url": agent_card_url,
        "status": "running",
        "status_message": "Deployed and health-checked by CI pipeline",
        "pid": None,
        "startup_time": now,
        "last_heartbeat": now,
        "last_error": None,
        "enabled": True,
        "created_at": existing.get("created_at", now) if existing else now,
        "updated_at": now,
    }
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
    container_name = f"progenitor-agent-{agent_name}"

    print(f"Registering agent '{agent_name}' (port={port}) in {registry_path}")

    # Fetch agent card from running container
    card = _fetch_agent_card(container_name, port)
    if card:
        print(f"Fetched agent card: name={card.get('name')!r} version={card.get('version')!r}")
    else:
        print("Proceeding without agent card (using defaults)")

    # Load current registry
    registry = _load_registry(registry_path)
    agents: list[dict] = registry.get("agents", [])
    if not isinstance(agents, list):
        agents = []

    # Find existing entry
    existing = next((a for a in agents if isinstance(a, dict) and a.get("name") == agent_name), None)

    # Build updated entry
    entry = _build_entry(agent_name, port, card, existing)

    # Replace or append
    agents = [a for a in agents if not (isinstance(a, dict) and a.get("name") == agent_name)]
    agents.append(entry)
    registry["agents"] = agents

    # Save registry
    _save_registry(registry_path, registry)
    print(f"Registered agent '{agent_name}' → {entry['endpoint']} (total agents: {len(agents)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
