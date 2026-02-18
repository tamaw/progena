"""
Container Spawner for isolated A2A agent and MCP server execution.

Creates per-run workspace/config bundles and launches short-lived containers
using Docker/Podman with strict defaults:
- --rm lifecycle
- bridge networking by default (never host by default)
- explicit env allowlist
- minimal, explicit mounts
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

logger = logging.getLogger(__name__)

RunnerMode = Literal["a2a", "mcp"]
RunnerTransport = Literal["stdio", "http"]


@dataclass
class ContainerMount:
    """Host-to-container mount mapping."""

    source: str
    target: str
    read_only: bool = False


@dataclass
class PortMapping:
    """Host-to-container port mapping."""

    host_port: int
    container_port: int


@dataclass
class RunnerSpawnConfig:
    """Per-run runner contract used to generate /workspace/run.json."""

    name: str
    mode: RunnerMode
    transport: RunnerTransport = "stdio"
    command: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)
    timeout_seconds: float = 30.0
    env_allowlist: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    network: str = "bridge"
    ports: list[PortMapping] = field(default_factory=list)
    mounts: list[ContainerMount] = field(default_factory=list)
    readiness_path: str = "/.well-known/agent.json"
    workspace_subdir: str = ""

    def to_run_json(self) -> dict[str, Any]:
        """Serialize contract payload for the container runner."""
        return {
            "name": self.name,
            "mode": self.mode,
            "transport": self.transport,
            "command": self.command,
            "args": self.args,
            "timeout_seconds": self.timeout_seconds,
            "env_allowlist": self.env_allowlist,
            "env": self.env,
            "http": {
                "ports": [
                    {"host": mapping.host_port, "container": mapping.container_port}
                    for mapping in self.ports
                ],
                "readiness_path": self.readiness_path,
            },
        }


@dataclass
class ProcessHandle:
    """Handle for a running containerized subprocess bridge."""

    run_id: str
    container_name: str
    image: str
    process: asyncio.subprocess.Process
    mode: RunnerMode
    transport: RunnerTransport
    workspace_dir: Path
    run_config_path: Path
    port: int | None = None


def _slug(text: str) -> str:
    value = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    return "-".join(part for part in value.split("-") if part)[:48] or "runner"


class ContainerSpawner:
    """Launches A2A agents and MCP servers inside short-lived containers."""

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.enabled = os.getenv("PROGENITOR_CONTAINER_RUNNERS", "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.engine = os.getenv("PROGENITOR_CONTAINER_ENGINE", "docker")
        self.agent_image = os.getenv(
            "PROGENITOR_AGENT_RUNNER_IMAGE", "progenitor-agent-runner:latest"
        )
        self.mcp_image = os.getenv(
            "PROGENITOR_MCP_RUNNER_IMAGE", "progenitor-mcp-runner:latest"
        )
        self.default_network = os.getenv("PROGENITOR_CONTAINER_NETWORK", "bridge")
        self.run_root = Path(
            os.getenv(
                "PROGENITOR_CONTAINER_RUN_DIR",
                str(self.project_root / "data" / "state" / "container_runs"),
            )
        )
        self.default_env_allowlist = [
            key.strip()
            for key in os.getenv(
                "PROGENITOR_CONTAINER_ENV_ALLOWLIST",
                "LITELLM_BASE_URL,LITELLM_MASTER_KEY,OPENAI_API_KEY,"
                "AZURE_OPENAI_API_KEY,AZURE_OPENAI_ENDPOINT,"
                "AZURE_OPENAI_API_VERSION,ANTHROPIC_API_KEY,GITHUB_TOKEN",
            ).split(",")
            if key.strip()
        ]

    def _build_run_paths(self, base_name: str) -> tuple[str, Path, Path, Path]:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        run_id = f"{timestamp}-{uuid4().hex[:8]}"
        run_dir = self.run_root / f"{_slug(base_name)}-{run_id}"
        workspace_dir = run_dir / "workspace"
        run_config_path = workspace_dir / "run.json"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return run_id, run_dir, workspace_dir, run_config_path

    def _select_image(self, mode: RunnerMode) -> str:
        return self.agent_image if mode == "a2a" else self.mcp_image

    def _build_env_args(
        self,
        explicit_env: dict[str, str],
        allowlist: list[str],
    ) -> list[str]:
        merged_allowlist = list(dict.fromkeys([*self.default_env_allowlist, *allowlist]))
        args: list[str] = []

        for env_key in merged_allowlist:
            if env_key in explicit_env:
                args.extend(["-e", f"{env_key}={explicit_env[env_key]}"])
                continue
            env_value = os.getenv(env_key)
            if env_value is not None:
                args.extend(["-e", f"{env_key}={env_value}"])

        return args

    async def spawn_agent(self, config: RunnerSpawnConfig) -> ProcessHandle:
        """Spawn an A2A agent container."""
        return await self._spawn(config)

    async def spawn_mcp(self, config: RunnerSpawnConfig) -> ProcessHandle:
        """Spawn an MCP server container."""
        return await self._spawn(config)

    async def _spawn(self, config: RunnerSpawnConfig) -> ProcessHandle:
        run_id, _, workspace_dir, run_config_path = self._build_run_paths(config.name)

        run_payload = config.to_run_json()
        run_payload["workspace"] = "/workspace"
        run_payload["cwd"] = (
            f"/workspace/{config.workspace_subdir.strip('/')}"
            if config.workspace_subdir
            else "/workspace"
        )

        with open(run_config_path, "w", encoding="utf-8") as handle:
            json.dump(run_payload, handle, indent=2)

        image = self._select_image(config.mode)
        container_name = f"progenitor-{config.mode}-{_slug(config.name)}-{run_id[-8:]}"
        network = config.network or self.default_network

        command = [
            self.engine,
            "run",
            "--rm",
            "-i",
            "--name",
            container_name,
            "--network",
            network,
            "-v",
            f"{workspace_dir}:/workspace:rw",
        ]

        for mount in config.mounts:
            mode = "ro" if mount.read_only else "rw"
            command.extend(["-v", f"{mount.source}:{mount.target}:{mode}"])

        for mapping in config.ports:
            command.extend(["-p", f"{mapping.host_port}:{mapping.container_port}"])

        command.extend(self._build_env_args(config.env, config.env_allowlist))
        command.extend(
            [
                image,
                "--mode",
                config.mode,
                "--config",
                "/workspace/run.json",
            ]
        )

        logger.info(
            "Spawning containerized %s '%s' with image '%s'",
            config.mode,
            config.name,
            image,
        )

        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.project_root,
        )

        selected_port = config.ports[0].host_port if config.ports else None
        return ProcessHandle(
            run_id=run_id,
            container_name=container_name,
            image=image,
            process=process,
            mode=config.mode,
            transport=config.transport,
            workspace_dir=workspace_dir,
            run_config_path=run_config_path,
            port=selected_port,
        )

    async def stop(self, handle: ProcessHandle, timeout_seconds: float = 5.0) -> None:
        """Stop a running container process and force-remove if needed."""
        process = handle.process
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
                return
            except TimeoutError:
                logger.warning(
                    "Container '%s' did not stop in time; forcing removal",
                    handle.container_name,
                )

        force_cmd = [self.engine, "rm", "-f", handle.container_name]
        forced = await asyncio.create_subprocess_exec(
            *force_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=self.project_root,
        )
        await forced.wait()
