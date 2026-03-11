#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SKIP_NAMES = {"__pycache__", ".git", "base", ".venv", "node_modules"}


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def list_components(base: str) -> list[str]:
    path = Path(base)
    if not path.exists():
        return []
    return sorted(
        p.name
        for p in path.iterdir()
        if p.is_dir() and p.name not in SKIP_NAMES and not p.name.startswith(".")
    )


def get_changed_files(event_name: str) -> tuple[list[str], bool]:
    if event_name == "workflow_dispatch":
        has_origin_main = run_git(
            "show-ref", "--verify", "--quiet", "refs/remotes/origin/main", check=False
        ).returncode == 0
        if not has_origin_main:
            run_git("fetch", "origin", "main")
        diff = run_git("diff", "--name-only", "origin/main...HEAD", check=False)
        if diff.returncode != 0:
            return [], True
        return [line for line in diff.stdout.splitlines() if line.strip()], False

    has_parent = run_git("rev-parse", "--verify", "HEAD^", check=False).returncode == 0
    if not has_parent:
        return [], False
    diff = run_git("diff", "--name-only", "HEAD^", "HEAD")
    return [line for line in diff.stdout.splitlines() if line.strip()], False


def write_output(name: str, value: list[str]) -> None:
    encoded = json.dumps(value)
    output_file = os.getenv("GITHUB_OUTPUT")
    if output_file:
        with Path(output_file).open("a", encoding="utf-8") as fh:
            fh.write(f"{name}={encoded}\n")
    print(f"{name}: {encoded}")


def main() -> int:
    event_name = os.getenv("EVENT_NAME", "")
    mode = os.getenv("MODE", "changed") or "changed"

    deploy_all = event_name == "workflow_dispatch" and mode == "all"
    changed_files: list[str] = []
    if not deploy_all:
        changed_files, deploy_all = get_changed_files(event_name)
        if any(path.startswith("docker/runner/") for path in changed_files):
            deploy_all = True

    if deploy_all:
        agents = list_components("app/agents")
        mcps = list_components("app/mcp")
    else:
        agents_set = set()
        mcps_set = set()
        for path in changed_files:
            parts = path.split("/")
            if len(parts) >= 3 and parts[0] == "app" and parts[1] == "agents" and Path("app", "agents", parts[2]).is_dir():
                agents_set.add(parts[2])
            if len(parts) >= 3 and parts[0] == "app" and parts[1] == "mcp" and Path("app", "mcp", parts[2]).is_dir():
                mcps_set.add(parts[2])
        agents = sorted(agents_set)
        mcps = sorted(mcps_set)

    write_output("agents", agents)
    write_output("mcps", mcps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
