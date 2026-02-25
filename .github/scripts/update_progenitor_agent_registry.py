#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    agent_name = os.environ.get("AGENT_NAME")
    if not agent_name:
        raise RuntimeError("AGENT_NAME is required")

    agent_port = int(os.environ.get("AGENT_PORT", "8080"))
    endpoint = f"http://progenitor-agent-{agent_name}:{agent_port}"
    card_url = f"{endpoint}/.well-known/agent.json"

    state_dir_candidates: list[Path] = []
    state_dir_env = os.environ.get("PROGENITOR_STATE_DIR")
    if state_dir_env:
        state_dir_candidates.append(Path(state_dir_env))
    state_dir_candidates.extend([Path("/data/state"), Path("/app/data/state")])

    registry_path: Path | None = None
    for state_dir in state_dir_candidates:
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            registry_path = state_dir / "registry.json"
            break
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue

    if registry_path is None:
        raise RuntimeError(
            "Could not resolve writable state dir for registry.json "
            f"(tried: {', '.join(str(p) for p in state_dir_candidates)})"
        )

    if registry_path.exists():
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    else:
        data = {"agents": [], "tools": []}

    agents = list(data.get("agents") or [])
    now = datetime.now(UTC).isoformat()
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
