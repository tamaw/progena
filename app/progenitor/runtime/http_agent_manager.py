"""
HTTP Agent Manager - Manages lifecycle of HTTP-based A2A agents.

Starts agents as HTTP servers on dynamically allocated ports,
monitors stdout/stderr for status updates, and maintains registry state.

Agents are expected to:
1. Start an HTTP server on the port specified via --port argument
2. Expose /.well-known/agent.json for the agent card
3. Output status lines to stdout: STATUS:<status>:<message>
4. Accept requests at POST /
"""

import asyncio
import logging
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp

from ..state.models import AgentStatus
from .container_spawner import ContainerSpawner, PortMapping, RunnerSpawnConfig
from .port_utils import find_available_port, is_port_available
from .time_utils import utcnow, utcnow_isoformat

if TYPE_CHECKING:
    from ..tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# Status line pattern: STATUS:<status>:<message>
# e.g., STATUS:running:Server started on port 8100
STATUS_LINE_PATTERN = re.compile(r'^STATUS:(\w+):(.*)$')


@dataclass
class ManagedAgent:
    """A managed HTTP agent process."""
    name: str
    entrypoint: str
    port: int
    pid: int
    process: asyncio.subprocess.Process
    started_at: datetime
    status: AgentStatus = AgentStatus.STARTING
    status_message: str | None = None
    last_error: str | None = None

    # Tasks for monitoring stdout/stderr
    stdout_task: asyncio.Task | None = None
    stderr_task: asyncio.Task | None = None

    @property
    def endpoint(self) -> str:
        return f"http://localhost:{self.port}"

    @property
    def agent_card_url(self) -> str:
        return f"{self.endpoint}/.well-known/agent.json"


class HTTPAgentManager:
    """
    Manages HTTP-based A2A agents.

    Responsibilities:
    - Start agents as HTTP servers on allocated ports
    - Monitor stdout/stderr for status updates
    - Update registry.json with runtime state
    - Health check running agents
    - Graceful shutdown
    """

    def __init__(
        self,
        project_root: Path,
        registry: "ToolRegistry",
        log_callback: Callable | None = None,
    ):
        """
        Initialize the HTTP agent manager.

        Args:
            project_root: Root path of the Progenitor project.
            registry: Tool registry for status updates.
            log_callback: Optional callback for logging events.
        """
        self.project_root = Path(project_root)
        self.registry = registry
        self.log_callback = log_callback
        self._agents: dict[str, ManagedAgent] = {}
        self._lock = asyncio.Lock()
        self._shutdown = False
        self._container_spawner = ContainerSpawner(self.project_root)

    async def start_agent(
        self,
        agent_name: str,
        entrypoint: str,
        env: dict[str, str] | None = None,
        startup_timeout: float = 30.0,
    ) -> ManagedAgent:
        """
        Start an agent as an HTTP server.

        Args:
            agent_name: Name of the agent.
            entrypoint: Path to agent entrypoint.
            env: Additional environment variables.
            startup_timeout: Timeout waiting for agent to become ready.

        Returns:
            ManagedAgent instance.

        Raises:
            FileNotFoundError: If entrypoint doesn't exist.
            RuntimeError: If agent fails to start.
        """
        async with self._lock:
            # Stop existing if running
            if agent_name in self._agents:
                await self._stop_agent_internal(agent_name)

            # Resolve entrypoint path
            if not Path(entrypoint).is_absolute():
                entrypoint_path = self.project_root / entrypoint
            else:
                entrypoint_path = Path(entrypoint)

            if not entrypoint_path.exists():
                raise FileNotFoundError(f"Agent entrypoint not found: {entrypoint_path}")

            # Allocate port (registry.allocate_port now checks OS-level availability)
            port = self.registry.allocate_port()

            # Double-check port is available (in case of race conditions)
            if not is_port_available(port):
                logger.warning(
                    "Allocated port %d is not available, finding alternative...", port
                )
                port = find_available_port(
                    start_port=port + 1,
                    end_port=port + 100,  # Try 100 ports above
                )

            logger.info(
                "Starting agent '%s' from %s on port %d",
                agent_name, entrypoint_path, port
            )

            # Prepare environment
            agent_env = os.environ.copy()
            agent_env["PYTHONPATH"] = str(self.project_root)
            agent_env["AGENT_NAME"] = agent_name
            agent_env["AGENT_PORT"] = str(port)
            if env:
                agent_env.update(env)

            # Start the process with port argument
            if self._container_spawner.enabled and not bool(env and env.get("DISABLE_CONTAINER")):
                raw_env = env or {}
                runtime_env = {
                    key: value
                    for key, value in raw_env.items()
                    if isinstance(value, str)
                }
                raw_allowlist = raw_env.get("CONTAINER_ENV_ALLOWLIST", [])
                if isinstance(raw_allowlist, str):
                    env_allowlist = [part.strip() for part in raw_allowlist.split(",") if part.strip()]
                elif isinstance(raw_allowlist, list):
                    env_allowlist = [str(item) for item in raw_allowlist if str(item).strip()]
                else:
                    env_allowlist = []

                try:
                    entrypoint_rel = entrypoint_path.relative_to(self.project_root).as_posix()
                except ValueError:
                    entrypoint_rel = Path(entrypoint).as_posix().lstrip("/")
                if entrypoint_rel.startswith("app/") and entrypoint_rel.endswith(".py"):
                    module = entrypoint_rel[:-3].replace("/", ".")
                    command = [
                        "python",
                        "-m",
                        module,
                        "--port",
                        str(port),
                        "--name",
                        agent_name,
                    ]
                else:
                    command = [
                        "python",
                        f"/app/{entrypoint_rel}",
                        "--port",
                        str(port),
                        "--name",
                        agent_name,
                    ]

                spawn_config = RunnerSpawnConfig(
                    name=agent_name,
                    mode="a2a",
                    transport="http",
                    command=command,
                    timeout_seconds=startup_timeout,
                    env_allowlist=env_allowlist,
                    env=runtime_env,
                    network=(raw_env.get("CONTAINER_NETWORK", "bridge") if isinstance(raw_env.get("CONTAINER_NETWORK", "bridge"), str) else "bridge"),
                    ports=[PortMapping(host_port=port, container_port=port)],
                    readiness_path="/.well-known/agent.json",
                )
                handle = await self._container_spawner.spawn_agent(spawn_config)
                process = handle.process
            else:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(entrypoint_path),
                    "--port",
                    str(port),
                    "--name",
                    agent_name,
                    cwd=self.project_root,
                    env=agent_env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

            managed_agent = ManagedAgent(
                name=agent_name,
                entrypoint=str(entrypoint_path),
                port=port,
                pid=process.pid,
                process=process,
                started_at=utcnow(),
                status=AgentStatus.STARTING,
            )

            self._agents[agent_name] = managed_agent

            # Update registry with starting status
            await self.registry.mark_agent_starting(
                agent_name,
                pid=process.pid,
                port=port,
            )

            # Start output monitoring tasks
            managed_agent.stdout_task = asyncio.create_task(
                self._monitor_stdout(managed_agent)
            )
            managed_agent.stderr_task = asyncio.create_task(
                self._monitor_stderr(managed_agent)
            )

            # Wait for agent to become ready
            try:
                await asyncio.wait_for(
                    self._wait_for_ready(managed_agent),
                    timeout=startup_timeout,
                )
                logger.info(
                    "Agent '%s' is running on port %d (PID: %d)",
                    agent_name, port, process.pid
                )
            except TimeoutError:
                error_msg = f"Agent failed to start within {startup_timeout}s"
                logger.error("Agent '%s' startup timeout: %s", agent_name, error_msg)
                await self._mark_failed(managed_agent, error_msg)
                raise RuntimeError(error_msg)

            if self.log_callback:
                await self.log_callback({
                    "event": "agent_started",
                    "agent_name": agent_name,
                    "entrypoint": str(entrypoint_path),
                    "port": port,
                    "pid": process.pid,
                    "timestamp": utcnow_isoformat(),
                })

            return managed_agent

    async def _monitor_stdout(self, agent: ManagedAgent) -> None:
        """Monitor agent stdout for status updates."""
        try:
            assert agent.process.stdout is not None, "stdout should be available"
            while not self._shutdown and agent.process.returncode is None:
                line = await agent.process.stdout.readline()
                if not line:
                    break

                line_str = line.decode().strip()
                if not line_str:
                    continue

                logger.debug("[%s stdout] %s", agent.name, line_str)

                # Parse status lines
                match = STATUS_LINE_PATTERN.match(line_str)
                if match:
                    status_str, message = match.groups()
                    await self._handle_status_update(agent, status_str, message)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error monitoring stdout for '%s': %s", agent.name, e)

    async def _monitor_stderr(self, agent: ManagedAgent) -> None:
        """Monitor agent stderr for errors."""
        error_lines = []
        try:
            assert agent.process.stderr is not None, "stderr should be available"
            while not self._shutdown and agent.process.returncode is None:
                line = await agent.process.stderr.readline()
                if not line:
                    break

                line_str = line.decode().strip()
                if not line_str:
                    continue

                logger.warning("[%s stderr] %s", agent.name, line_str)
                error_lines.append(line_str)

                # Keep only last 10 error lines
                if len(error_lines) > 10:
                    error_lines = error_lines[-10:]

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error monitoring stderr for '%s': %s", agent.name, e)

        # If process exited with errors, mark as failed
        if agent.process.returncode and agent.process.returncode != 0:
            error_msg = "\n".join(error_lines[-5:]) if error_lines else f"Exit code {agent.process.returncode}"
            await self._mark_failed(agent, error_msg)

    async def _handle_status_update(
        self,
        agent: ManagedAgent,
        status_str: str,
        message: str,
    ) -> None:
        """Handle a status update from agent stdout."""
        try:
            status = AgentStatus(status_str.lower())
        except ValueError:
            logger.warning(
                "Unknown status '%s' from agent '%s'",
                status_str, agent.name
            )
            return

        agent.status = status
        agent.status_message = message

        await self.registry.update_agent_status(
            agent.name,
            status,
            status_message=message,
            update_heartbeat=(status == AgentStatus.RUNNING),
        )

        logger.info(
            "Agent '%s' status: %s - %s",
            agent.name, status.value, message
        )

    async def _wait_for_ready(self, agent: ManagedAgent) -> None:
        """Wait for agent to report running status or respond to health check."""
        while agent.status == AgentStatus.STARTING:
            # Check if process exited
            if agent.process.returncode is not None:
                raise RuntimeError(f"Agent process exited with code {agent.process.returncode}")

            # Try health check
            if await self._health_check_agent(agent):
                if agent.status == AgentStatus.STARTING:
                    agent.status = AgentStatus.RUNNING
                    await self.registry.mark_agent_running(agent.name)
                return

            await asyncio.sleep(0.5)

    async def _health_check_agent(self, agent: ManagedAgent) -> bool:
        """Check if agent is responding to HTTP requests."""
        try:
            async with aiohttp.ClientSession() as session, session.get(
                agent.agent_card_url,
                timeout=aiohttp.ClientTimeout(total=2),
            ) as response:
                if response.status == 200:
                    return True
        except (TimeoutError, aiohttp.ClientError):
            pass
        return False

    async def _mark_failed(self, agent: ManagedAgent, error: str) -> None:
        """Mark an agent as failed."""
        agent.status = AgentStatus.FAILED
        agent.last_error = error
        await self.registry.mark_agent_failed(agent.name, error)

    async def stop_agent(self, agent_name: str) -> bool:
        """
        Stop a running agent.

        Args:
            agent_name: Name of the agent to stop.

        Returns:
            True if agent was stopped, False if not running.
        """
        async with self._lock:
            return await self._stop_agent_internal(agent_name)

    async def _stop_agent_internal(self, agent_name: str) -> bool:
        """Internal stop without lock."""
        if agent_name not in self._agents:
            return False

        agent = self._agents[agent_name]

        # Cancel monitoring tasks
        if agent.stdout_task:
            agent.stdout_task.cancel()
        if agent.stderr_task:
            agent.stderr_task.cancel()

        # Terminate process
        if agent.process and agent.process.returncode is None:
            logger.info("Stopping agent '%s' (PID: %d)", agent_name, agent.pid)

            try:
                agent.process.terminate()
                await asyncio.wait_for(
                    agent.process.wait(),
                    timeout=5.0,
                )
            except TimeoutError:
                logger.warning(
                    "Agent '%s' did not terminate gracefully, killing",
                    agent_name
                )
                agent.process.kill()

        # Update registry
        await self.registry.mark_agent_stopped(agent_name)

        del self._agents[agent_name]

        if self.log_callback:
            await self.log_callback({
                "event": "agent_stopped",
                "agent_name": agent_name,
                "timestamp": utcnow_isoformat(),
            })

        return True

    async def stop_all(self) -> None:
        """Stop all running agents."""
        self._shutdown = True
        async with self._lock:
            for agent_name in list(self._agents.keys()):
                await self._stop_agent_internal(agent_name)

    async def invoke_agent(
        self,
        agent_name: str,
        request: str | dict[str, Any],
        context: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """
        Invoke an agent with a request via HTTP.

        Args:
            agent_name: Name of the agent.
            request: Request message or data.
            context: Additional context.
            timeout: Request timeout.

        Returns:
            Response from the agent.
        """
        # Get agent endpoint from registry
        endpoint = self.registry.get_agent_endpoint(agent_name)
        if not endpoint:
            return {"success": False, "error": f"Agent '{agent_name}' not found or not running"}

        # Update status to executing
        await self.registry.update_agent_status(
            agent_name,
            AgentStatus.EXECUTING,
            status_message="Processing request",
        )

        # Build payload
        if isinstance(request, str):
            payload = {"request": request, "context": context or {}}
        else:
            payload = request
            if context:
                payload["context"] = context

        try:
            async with aiohttp.ClientSession() as session, session.post(
                endpoint,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                result = await response.json()

                # Update status back to waiting
                await self.registry.update_agent_status(
                    agent_name,
                    AgentStatus.WAITING,
                    status_message="Idle, waiting for requests",
                    update_heartbeat=True,
                )

                return result

        except aiohttp.ClientError as e:
            error = f"HTTP error: {e}"
            await self.registry.update_agent_status(
                agent_name,
                AgentStatus.WAITING,
                status_message=f"Request failed: {error}",
            )
            return {"success": False, "error": error}
        except TimeoutError:
            error = f"Request timeout after {timeout}s"
            await self.registry.update_agent_status(
                agent_name,
                AgentStatus.WAITING,
                status_message=f"Request failed: {error}",
            )
            return {"success": False, "error": error}

    async def health_check_all(self) -> dict[str, bool]:
        """Health check all running agents."""
        results = {}
        for agent_name, agent in self._agents.items():
            healthy = await self._health_check_agent(agent)
            results[agent_name] = healthy

            if healthy:
                await self.registry.update_agent_status(
                    agent_name,
                    agent.status,  # Keep current status
                    update_heartbeat=True,
                )
            else:
                # Check if process is still alive
                if agent.process.returncode is not None:
                    error = f"Process exited with code {agent.process.returncode}"
                    await self._mark_failed(agent, error)

        return results

    def get_agent(self, agent_name: str) -> ManagedAgent | None:
        """Get a managed agent by name."""
        return self._agents.get(agent_name)

    def get_all_agents(self) -> dict[str, ManagedAgent]:
        """Get all managed agents."""
        return dict(self._agents)
