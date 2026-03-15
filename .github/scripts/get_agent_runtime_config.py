#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shlex
import subprocess
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


def _parse_env_arg(arg: str) -> list[str]:
    tokens = shlex.split(arg)
    if not tokens:
        return []

    keys: list[str] = []
    if any("=" in token for token in tokens):
        for token in tokens:
            if "=" not in token:
                continue
            key = token.split("=", 1)[0].strip()
            if key:
                keys.append(key)
        return keys

    for idx in range(0, len(tokens), 2):
        key = tokens[idx].strip()
        if key:
            keys.append(key)
    return keys


def _load_progenitor_mounts() -> dict[str, str]:
    mounts_json = os.environ.get("PROGENITOR_MOUNTS_JSON")
    if mounts_json:
        parsed = json.loads(mounts_json)
        if not isinstance(parsed, dict):
            raise RuntimeError("PROGENITOR_MOUNTS_JSON must be a JSON object")
        return {str(k): str(v) for k, v in parsed.items()}

    container_name = os.environ.get("PROGENITOR_CONTAINER", "progenitor")
    proc = subprocess.run(
        ["docker", "inspect", container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to inspect container '{container_name}': {proc.stderr.strip() or proc.stdout.strip()}"
        )

    payload = json.loads(proc.stdout)
    if not payload:
        raise RuntimeError(f"Container '{container_name}' not found")

    mounts = payload[0].get("Mounts") or []
    mapping: dict[str, str] = {}
    for mount in mounts:
        if not isinstance(mount, dict):
            continue
        dest = mount.get("Destination")
        source = mount.get("Source")
        if isinstance(dest, str) and isinstance(source, str):
            mapping[dest] = source
    return mapping


def _map_target_to_host(target: str, mounts: dict[str, str]) -> str | None:
    mapping_roots = [
        ("/app/data", "/app/data"),
        ("/app/logs", "/app/logs"),
        ("/app/worktrees", "/app/worktrees"),
    ]
    for target_root, progenitor_root in mapping_roots:
        if target == target_root or target.startswith(f"{target_root}/"):
            source_root = mounts.get(progenitor_root)
            if not source_root:
                return None
            suffix = target[len(target_root) :]
            return f"{source_root}{suffix}"
    return None


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
    env_keys: list[str] = []
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
            continue

        if upper.startswith("ENV "):
            env_keys.extend(_parse_env_arg(line[4:]))

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

    unique_env_keys: list[str] = []
    seen_env_keys: set[str] = set()
    for key in env_keys:
        if key in seen_env_keys:
            continue
        seen_env_keys.add(key)
        unique_env_keys.append(key)
    runtime_env_keys = [key for key in unique_env_keys if key in os.environ]

    progenitor_mounts = _load_progenitor_mounts()
    volume_args: list[str] = []
    chown_paths: list[str] = []
    seen_paths: set[str] = set()

    for target in unique_volumes:
        host_path = _map_target_to_host(target, progenitor_mounts)
        if not host_path:
            continue
        volume_args.append(f"-v {host_path}:{target}")
        if host_path not in seen_paths:
            seen_paths.add(host_path)
            chown_paths.append(host_path)

    volume_args_str = " ".join(volume_args)
    chown_paths_str = " ".join(chown_paths)
    env_args_str = " ".join(f"-e {key}" for key in runtime_env_keys)
    tmpfs_args_str = "--tmpfs /app/tmp:rw,noexec,nosuid,size=512m"

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as f:
            f.write(f"port={port}\n")
            f.write(f"volume_args={volume_args_str}\n")
            f.write(f"chown_paths={chown_paths_str}\n")
            f.write(f"env_args={env_args_str}\n")
            f.write(f"tmpfs_args={tmpfs_args_str}\n")
    else:
        print(f"port={port}")
        print(f"volume_args={volume_args_str}")
        print(f"chown_paths={chown_paths_str}")
        print(f"env_args={env_args_str}")
        print(f"tmpfs_args={tmpfs_args_str}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
