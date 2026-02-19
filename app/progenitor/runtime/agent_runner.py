"""
Agent Runner - Runs and monitors built A2A agents.

Allows Progenitor to start, monitor, and interact with agents
immediately after they are built.

Includes logging for observability of sub-agent execution.
"""

import asyncio
import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .time_utils import utcnow_isoformat

logger = logging.getLogger(__name__)


@dataclass
class AgentProcess:
    """Represents a running agent process."""
    name: str
    process: asyncio.subprocess.Process | None
    entrypoint: str
    started_at: str
    pid: int
    status: str = "running"  # running, stopped, failed, stdio
    last_health_check: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pid": self.pid,
            "entrypoint": self.entrypoint,
            "started_at": self.started_at,
            "status": self.status,
            "last_health_check": self.last_health_check,
            "error": self.error,
        }


@dataclass
class AgentInteractionResult:
    """Result of interacting with a running agent."""
    success: bool
    response: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: float | None = None


class AgentRunner:
    """
    Runs and monitors A2A agents.

    Provides capabilities to:
    - Start agents as background processes
    - Monitor agent health
    - Invoke agents directly
    - Stop agents gracefully
    """

    def __init__(
        self,
        project_root: Path,
        log_callback: Callable | None = None,
    ):
        """
        Initialize the agent runner.

        Args:
            project_root: Root path of the Progenitor project.
            log_callback: Optional callback for logging events.
        """
        self.project_root = Path(project_root)
        self.log_callback = log_callback
        self._running_agents: dict[str, AgentProcess] = {}
        self._lock = asyncio.Lock()

    async def start_agent(
        self,
        agent_name: str,
        entrypoint: str,
        env: dict[str, str] | None = None,
        background: bool = True,
    ) -> AgentProcess:
        """
        Start an agent process.

        Args:
            agent_name: Name of the agent.
            entrypoint: Path to agent entrypoint (e.g., app/agents/hello/agent.py).
            env: Additional environment variables.
            background: Run as background process (for HTTP agents).

        Returns:
            AgentProcess representing the running agent.
        """
        async with self._lock:
            # Stop existing if running
            if agent_name in self._running_agents:
                await self._stop_agent_internal(agent_name)

            # Resolve entrypoint path
            if not Path(entrypoint).is_absolute():
                entrypoint_path = self.project_root / entrypoint
            else:
                entrypoint_path = Path(entrypoint)

            if not entrypoint_path.exists():
                raise FileNotFoundError(f"Agent entrypoint not found: {entrypoint_path}")

            # Prepare environment
            agent_env = os.environ.copy()
            agent_env["PYTHONPATH"] = str(self.project_root)
            if env:
                agent_env.update(env)

            logger.info("Starting agent '%s' from %s", agent_name, entrypoint_path)

            # Start the process
            if background:
                # For HTTP/long-running agents
                process = await asyncio.create_subprocess_exec(
                    sys.executable, str(entrypoint_path),
                    cwd=self.project_root,
                    env=agent_env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                # For stdio agents, we don't keep them running
                # They are invoked per-request
                process = None

            agent_process = AgentProcess(
                name=agent_name,
                process=process,
                entrypoint=str(entrypoint_path),
                started_at=utcnow_isoformat(),
                pid=process.pid if process else 0,
                status="running" if process else "stdio",
            )

            if process:
                self._running_agents[agent_name] = agent_process

            if self.log_callback:
                await self.log_callback({
                    "event": "agent_started",
                    "agent_name": agent_name,
                    "entrypoint": str(entrypoint_path),
                    "pid": agent_process.pid,
                    "timestamp": utcnow_isoformat(),
                })

            logger.info("Agent '%s' started (PID: %s)", agent_name, agent_process.pid)
            return agent_process

    async def invoke_agent(
        self,
        agent_name: str,
        entrypoint: str,
        request: str | dict[str, Any],
        context: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> AgentInteractionResult:
        """
        Invoke an agent with a request (stdio transport).

        Args:
            agent_name: Name of the agent.
            entrypoint: Path to agent entrypoint.
            request: Request message or data.
            context: Additional context to pass.
            timeout: Timeout in seconds.

        Returns:
            AgentInteractionResult with response or error.
        """
        start_time = datetime.now()

        # Resolve entrypoint path
        if not Path(entrypoint).is_absolute():
            entrypoint_path = self.project_root / entrypoint
        else:
            entrypoint_path = Path(entrypoint)

        if not entrypoint_path.exists():
            return AgentInteractionResult(
                success=False,
                error=f"Agent entrypoint not found: {entrypoint_path}",
            )

        # Prepare input payload
        if isinstance(request, str):
            payload = {
                "request": request,
                "context": context or {},
            }
        else:
            payload = request
            if context:
                payload["context"] = context

        # Prepare environment
        agent_env = os.environ.copy()
        agent_env["PYTHONPATH"] = str(self.project_root)

        try:
            logger.info("Invoking agent '%s'", agent_name)

            process = await asyncio.create_subprocess_exec(
                sys.executable, str(entrypoint_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_root,
                env=agent_env,
            )

            input_bytes = json.dumps(payload).encode() + b"\n"
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=input_bytes),
                timeout=timeout,
            )

            duration_ms = (datetime.now() - start_time).total_seconds() * 1000

            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else f"Exit code {process.returncode}"
                logger.warning("Agent '%s' invocation failed: %s", agent_name, error_msg)
                return AgentInteractionResult(
                    success=False,
                    error=error_msg,
                    duration_ms=duration_ms,
                )

            # Parse response
            try:
                response = json.loads(stdout.decode())
            except json.JSONDecodeError:
                response = {"raw_output": stdout.decode()}

            logger.info("Agent '%s' responded in %.1fms", agent_name, duration_ms)

            if self.log_callback:
                await self.log_callback({
                    "event": "agent_invoked",
                    "agent_name": agent_name,
                    "success": True,
                    "duration_ms": duration_ms,
                    "timestamp": utcnow_isoformat(),
                })

            return AgentInteractionResult(
                success=True,
                response=response,
                duration_ms=duration_ms,
            )

        except TimeoutError:
            duration_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.error(f"Agent '{agent_name}' invocation timed out after {timeout}s")
            return AgentInteractionResult(
                success=False,
                error=f"Timeout after {timeout}s",
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.exception(f"Error invoking agent '{agent_name}'")
            return AgentInteractionResult(
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )

    async def test_agent(
        self,
        agent_name: str,
        entrypoint: str,
        test_request: str = "Hello, are you working?",
    ) -> AgentInteractionResult:
        """
        Quick test to verify an agent is working.

        Args:
            agent_name: Name of the agent.
            entrypoint: Path to agent entrypoint.
            test_request: Simple test request to send.

        Returns:
            AgentInteractionResult from the test.
        """
        logger.info(f"Testing agent '{agent_name}'...")
        result = await self.invoke_agent(
            agent_name=agent_name,
            entrypoint=entrypoint,
            request=test_request,
            timeout=30.0,
        )

        if result.success:
            logger.info(f"Agent '{agent_name}' test PASSED")
        else:
            logger.warning(f"Agent '{agent_name}' test FAILED: {result.error}")

        return result

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
        if agent_name not in self._running_agents:
            return False

        agent_process = self._running_agents[agent_name]

        if agent_process.process and agent_process.process.returncode is None:
            logger.info(f"Stopping agent '{agent_name}' (PID: {agent_process.pid})")

            try:
                agent_process.process.terminate()
                await asyncio.wait_for(
                    agent_process.process.wait(),
                    timeout=5.0,
                )
            except TimeoutError:
                logger.warning(f"Agent '{agent_name}' did not terminate gracefully, killing")
                agent_process.process.kill()

        agent_process.status = "stopped"
        del self._running_agents[agent_name]

        if self.log_callback:
            await self.log_callback({
                "event": "agent_stopped",
                "agent_name": agent_name,
                "timestamp": utcnow_isoformat(),
            })

        return True

    async def stop_all(self) -> None:
        """Stop all running agents."""
        async with self._lock:
            for agent_name in list(self._running_agents.keys()):
                await self._stop_agent_internal(agent_name)

    def get_running_agents(self) -> dict[str, dict[str, Any]]:
        """Get info about all running agents."""
        return {
            name: agent.to_dict()
            for name, agent in self._running_agents.items()
        }

    def is_running(self, agent_name: str) -> bool:
        """Check if an agent is currently running."""
        if agent_name not in self._running_agents:
            return False

        agent = self._running_agents[agent_name]
        if agent.process and agent.process.returncode is not None:
            # Process has exited
            agent.status = "stopped"
            return False

        return True

    async def health_check(self, agent_name: str) -> bool:
        """
        Check health of a running agent.

        For stdio agents, this verifies the entrypoint exists.
        For HTTP agents, this could ping a health endpoint.
        """
        if agent_name in self._running_agents:
            agent = self._running_agents[agent_name]

            # Check if process is still alive
            if agent.process and agent.process.returncode is not None:
                agent.status = "stopped"
                agent.error = f"Process exited with code {agent.process.returncode}"
                return False

            agent.last_health_check = utcnow_isoformat()
            return True

        return False

    async def health_check_all(self) -> dict[str, bool]:
        """Check health of all registered agents."""
        results = {}
        for agent_name in list(self._running_agents.keys()):
            results[agent_name] = await self.health_check(agent_name)
        return results
