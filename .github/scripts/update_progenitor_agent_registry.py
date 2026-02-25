#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    agent_name = os.environ.get("AGENT_NAME")
    if not agent_name:
        raise RuntimeError("AGENT_NAME is required")

    agent_port = int(os.environ.get("AGENT_PORT", "8080"))
    endpoint = f"http://progenitor-agent-{agent_name}:{agent_port}"
    card_url = f"{endpoint}/.well-known/agent.json"

    registry_path = Path("/app/data/state/registry.json")
    registry_path.parent.mkdir(parents=True, exist_ok=True)

    if registry_path.exists():
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    else:
        data = {"agents": [], "tools": []}

    agents = list(data.get("agents") or [])
    now = datetime.now(timezone.utc).isoformat()
    existing = next((agent for agent in agents if agent.get("name") == agent_name), {})

    entry = {
        **existing,
        "name": agent_name,
        "kind": "a2a_agent",
        "enabled": existing.get("enabled", True),
        "created_at": existing.get("created_at", now),
        "transport": "http",
        "port": agent_port,
        "endpoint": endpoint,
        "agent_card_url": card_url,
        "status": "running",
        "updated_at": now,
    }

    data["agents"] = [agent for agent in agents if agent.get("name") != agent_name] + [entry]
    registry_path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")
    print(f"Updated registry entry for {agent_name} -> {endpoint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
