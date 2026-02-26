#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
from pathlib import Path


def _iter_instructions(text: str) -> list[str]:
    instructions: list[str] = []
    current = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            line = line[:-1].rstrip()
            current = f"{current} {line}".strip() if current else line
            continue
        combined = f"{current} {line}".strip() if current else line
        instructions.append(combined)
        current = ""
    if current:
        instructions.append(current)
    return instructions


def _parse_volume_arg(arg: str) -> list[str]:
    arg = arg.strip()
    if not arg:
        return []
    if arg.startswith("["):
        try:
            values = json.loads(arg)
        except json.JSONDecodeError:
            return []
        return [v for v in values if isinstance(v, str)]
    return [token for token in shlex.split(arg) if token]


def main() -> int:
    agent_name = os.environ.get("AGENT_NAME")
    if not agent_name:
        raise RuntimeError("AGENT_NAME is required")

    dockerfile = Path("app") / "agents" / agent_name / "Dockerfile"
    if not dockerfile.exists():
        raise RuntimeError(f"Dockerfile not found: {dockerfile}")

    instructions = _iter_instructions(dockerfile.read_text(encoding="utf-8"))
    port = None
    volume_paths: list[str] = []
    for line in instructions:
        upper = line.upper()
        if upper.startswith("EXPOSE ") and not port:
            parts = line.split()
            if len(parts) >= 2:
                token = parts[1].split("/")[0]
                if token.isdigit():
                    port = token
            continue

        if upper.startswith("VOLUME "):
            volume_paths.extend(_parse_volume_arg(line[7:]))

    if not port:
        raise RuntimeError(f"Could not determine EXPOSE port from {dockerfile}")

    # Keep mount order stable while removing duplicates.
    unique_volumes: list[str] = []
    seen: set[str] = set()
    for path in volume_paths:
        if path in seen:
            continue
        seen.add(path)
        unique_volumes.append(path)

    mounts_csv = ",".join(unique_volumes)

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as f:
            f.write(f"port={port}\n")
            f.write(f"mounts_csv={mounts_csv}\n")
    else:
        print(f"port={port}")
        print(f"mounts_csv={mounts_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
