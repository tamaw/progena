#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path


def main() -> int:
    agent_name = os.environ.get("AGENT_NAME")
    if not agent_name:
        raise RuntimeError("AGENT_NAME is required")

    dockerfile = Path("app") / "agents" / agent_name / "Dockerfile"
    if not dockerfile.exists():
        raise RuntimeError(f"Dockerfile not found: {dockerfile}")

    port = None
    for raw_line in dockerfile.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.upper().startswith("EXPOSE "):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        token = parts[1].split("/")[0]
        if token.isdigit():
            port = token
            break

    if not port:
        raise RuntimeError(f"Could not determine EXPOSE port from {dockerfile}")

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as f:
            f.write(f"port={port}\n")
    else:
        print(f"port={port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
