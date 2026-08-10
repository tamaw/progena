"""Controlled local shell backend for opt-in object-agent manifests.

This backend is intended for development and as a process inside the hardened
runtime container. The shimmed PATH prevents accidental destructive ``git`` and
``gh`` invocations, but is not a security boundary: ``LocalShellBackend`` runs
``shell=True`` and callers can use absolute executable paths. Container
isolation, the non-root user, and minimal mounts are the security boundary.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from deepagents.backends import LocalShellBackend

if TYPE_CHECKING:
    from .object_agents import ObjectAgentShellConfig


@dataclass(frozen=True)
class ShellPaths:
    """Runtime-controlled locations used by shell-enabled object agents."""

    shim_bin: Path
    agent_home: Path
    timeout_seconds: int


def agent_home_slug(name: str) -> str:
    """Produce a stable, filesystem-safe directory name for one agent."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "agent"


def build_shell_env(
    *,
    shim_bin: Path,
    agent_home: Path,
    config: ObjectAgentShellConfig,
    parent_env: Mapping[str, str],
) -> dict[str, str]:
    """Build the minimal child environment for an object-agent shell."""
    environment = {
        "PATH": f"{shim_bin}:{config.system_path}",
        "HOME": str(agent_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "GH_NO_UPDATE_NOTIFIER": "1",
        "GH_PAGER": "cat",
        "PROGENITOR_SHIM_SYSTEM_PATH": config.system_path,
    }
    if timezone := parent_env.get("TZ"):
        environment["TZ"] = timezone
    for name in config.env_passthrough:
        if value := parent_env.get(name):
            environment[name] = value
    return environment


class ProgenitorShellBackend(LocalShellBackend):
    """Local shell backend with a scrubbed environment and shimmed ``PATH``.

    The shimmed PATH is a guardrail, not a security boundary. ``execute`` uses
    ``subprocess.run(..., shell=True)``; absolute executable paths, pipes, and
    command substitutions bypass it. The production container is the boundary.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        timeout: int,
        config: ObjectAgentShellConfig,
        shim_bin: Path,
        agent_home: Path,
        parent_env: Mapping[str, str] | None = None,
    ) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        agent_home.mkdir(parents=True, exist_ok=True)
        git_config = agent_home / ".gitconfig"
        if not git_config.exists():
            git_config.write_text("[safe]\n\tdirectory = *\n", encoding="utf-8")

        super().__init__(
            root_dir=str(workspace),
            virtual_mode=False,
            timeout=timeout,
            max_output_bytes=config.max_output_bytes,
            env=build_shell_env(
                shim_bin=shim_bin,
                agent_home=agent_home,
                config=config,
                parent_env=parent_env or {},
            ),
            inherit_env=False,
        )
