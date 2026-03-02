#!/usr/bin/env python3
"""Register a deployed agent in progenitor's live agents.json.

Runs on the GitHub runner and writes directly into the running progenitor
container's mounted registry path.

Environment variables:
  AGENT_NAME            Name of the deployed agent (required).
  PORT                  Port the agent listens on (required).
  DOCKER_HOSTNAME       Agent container Docker hostname (required).
  PROGENITOR_CONTAINER  Progenitor container name (default: progenitor).
  REGISTRY_PATH         Registry file path inside progenitor container
                        (default: /app/data/config/agents.json).
"""
from __future__ import annotations

import os
import subprocess
import sys

_DEFAULT_PROGENITOR_CONTAINER = "progenitor"
_DEFAULT_REGISTRY_PATH = "/app/data/config/agents.json"

_UPDATE_REGISTRY_SCRIPT = """
import json
import os
from pathlib import Path

agent_name = os.environ["AGENT_NAME"].strip()
port = int(os.environ["PORT"].strip())
docker_hostname = os.environ["DOCKER_HOSTNAME"].strip()
registry_path = Path(os.environ["REGISTRY_PATH"].strip())

registry = {}
if registry_path.exists():
    try:
        loaded = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        loaded = {}
    if isinstance(loaded, dict):
        raw_agents = loaded.get("agents")
        if isinstance(raw_agents, list):
            for item in raw_agents:
                if not isinstance(item, dict):
                    continue
                legacy_name = str(item.get("name") or "").strip()
                legacy_url = item.get("url")
                if not legacy_name or not isinstance(legacy_url, str) or not legacy_url.strip():
                    continue
                entry = {"url": legacy_url.strip()}
                headers = item.get("headers")
                if isinstance(headers, dict):
                    clean_headers = {str(k): str(v) for k, v in headers.items() if str(k).strip()}
                    if clean_headers:
                        entry["headers"] = clean_headers
                registry[legacy_name] = entry
        for key, value in loaded.items():
            if key == "agents" or not isinstance(value, dict):
                continue
            url = value.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            entry = {"url": url.strip()}
            headers = value.get("headers")
            if isinstance(headers, dict):
                clean_headers = {str(k): str(v) for k, v in headers.items() if str(k).strip()}
                if clean_headers:
                    entry["headers"] = clean_headers
            registry[str(key)] = entry

existing = registry.get(agent_name) if isinstance(registry.get(agent_name), dict) else None
entry = {"url": f"http://{docker_hostname}:{port}"}
if existing and isinstance(existing.get("headers"), dict):
    clean_headers = {str(k): str(v) for k, v in existing["headers"].items() if str(k).strip()}
    if clean_headers:
        entry["headers"] = clean_headers
registry[agent_name] = entry

registry_path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = registry_path.with_suffix(".tmp")
tmp_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
tmp_path.replace(registry_path)
print(f"Registered {agent_name} -> {entry['url']} (total agents: {len(registry)})")
""".strip()


def _update_registry_in_container(
    *,
    progenitor_container: str,
    registry_path: str,
    agent_name: str,
    port: int,
    docker_hostname: str,
) -> None:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-e",
            f"AGENT_NAME={agent_name}",
            "-e",
            f"PORT={port}",
            "-e",
            f"DOCKER_HOSTNAME={docker_hostname}",
            "-e",
            f"REGISTRY_PATH={registry_path}",
            progenitor_container,
            "python",
            "-c",
            _UPDATE_REGISTRY_SCRIPT,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to update registry in container '{progenitor_container}': "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    if result.stdout.strip():
        print(result.stdout.strip())


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

    docker_hostname = os.environ.get("DOCKER_HOSTNAME", "").strip()
    if not docker_hostname:
        print("ERROR: DOCKER_HOSTNAME is required", file=sys.stderr)
        return 1
    progenitor_container = os.environ.get(
        "PROGENITOR_CONTAINER", _DEFAULT_PROGENITOR_CONTAINER
    ).strip() or _DEFAULT_PROGENITOR_CONTAINER
    registry_path = os.environ.get("REGISTRY_PATH", _DEFAULT_REGISTRY_PATH).strip() or _DEFAULT_REGISTRY_PATH

    print(f"Registering '{agent_name}' in container '{progenitor_container}' at {registry_path}")
    try:
        _update_registry_in_container(
            progenitor_container=progenitor_container,
            registry_path=registry_path,
            agent_name=agent_name,
            port=port,
            docker_hostname=docker_hostname,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
