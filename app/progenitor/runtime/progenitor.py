"""Progenitor runtime lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from ..config import ProgenitorConfig, get_config
from ..ipc.server import IPCServer
from ..llm.client import LLMClient
from ..mcp import MCPRuntime
from ..nodes.health import write_health_status
from ..registry import AgentRegistryRuntime
from ..state.manager import StateManager
from .heartbeat import ProgenitorLoop
from .http import ProgenitorHTTPServer

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


class Progenitor:
    """Main Progenitor agent runtime."""

    def __init__(
        self,
        config: ProgenitorConfig | None = None,
    ):
        self.config = config or get_config(PROJECT_ROOT, CONFIG_DIR)
        self.running = False
        self._shutdown_started = False

        self.state_dir = self.config.storage.state_dir
        self.config_dir = self.state_dir.parent / "config"
        self.logs_dir = self.config.storage.log_dir
        self.state_manager = StateManager(
            self.state_dir,
            self.logs_dir,
            lock_timeout=self.config.storage.state_lock_timeout_seconds,
        )
        self.llm_client = LLMClient(
            config=self.config.llm,
            mcp_config=self.config.mcp,
        )
        self.registry_runtime = AgentRegistryRuntime(self.config_dir, PROJECT_ROOT)
        self.mcp_runtime = MCPRuntime(self.config.mcp)
        self.llm_client.set_mcp_runtime(self.mcp_runtime)
        self.tool_registry = self.registry_runtime.agent_registry
        self.graph: Any = None
        self.loop_runner: Any = None
        self._http_server: ProgenitorHTTPServer | None = None
        self._ipc_server: IPCServer | None = None
        self._background_tasks: list[asyncio.Task] = []

    async def initialize(self) -> None:
        """Initialize all components."""
        for dir_path in [self.state_dir, self.config_dir, self.logs_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)

        await self.state_manager.initialize()
        await self.registry_runtime.load()

        async def deferred_push_event(event_type: str, content: str, metadata=None):
            """Event pusher that delegates to the loop runner when available."""
            if self.loop_runner:
                await self.loop_runner.queues.push_event(event_type, content, metadata)
            else:
                logger.warning("Event pusher called before loop_runner available: %s", event_type)

        self.llm_client.set_event_pusher(deferred_push_event)

        logger.info("Creating DeepAgent graph (core-only)")
        from ..agent.adapters import create_deep_agent_for_runtime

        self.graph = create_deep_agent_for_runtime(
            config=self.config,
            tool_registry=self.tool_registry,
            llm_client=self.llm_client,
            state_manager=self.state_manager,
            project_root=str(PROJECT_ROOT),
            log_dir=self.logs_dir,
        )

        self.loop_runner = ProgenitorLoop(
            graph=self.graph,
            state_manager=self.state_manager,
            config=self.config,
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
            project_root=PROJECT_ROOT,
        )

    async def run(self) -> None:
        """Run the perpetual agent loop until shutdown."""
        self.running = True

        logger.info("Progenitor starting...")

        await self.initialize()
        logger.info("Progenitor initialized. Entering perpetual loop.")

        loop_task = asyncio.create_task(self.loop_runner.run_forever())
        self._background_tasks = [loop_task]

        self._http_server = ProgenitorHTTPServer(
            state_manager=self.state_manager,
            loop_runner=self.loop_runner,
            tool_registry=self.tool_registry,
            host=self.config.runtime.http_host,
            port=self.config.runtime.http_port,
            public_base_url=self.config.runtime.http_public_base_url,
        )
        await self._http_server.start()

        assert self.loop_runner is not None, "loop_runner must be initialized before IPC server"
        logger.info(
            "Starting IPC server on %s:%d",
            self.config.runtime.ipc_host,
            self.config.runtime.ipc_port,
        )
        self._ipc_server = IPCServer(
            loop_runner=self.loop_runner,
            host=self.config.runtime.ipc_host,
            port=self.config.runtime.ipc_port,
            max_clients=self.config.runtime.ipc_max_clients,
        )
        await self._ipc_server.start()
        ipc_task = asyncio.create_task(self._ipc_server.serve_forever())
        self._background_tasks.append(ipc_task)

        try:
            done, pending = await asyncio.wait(
                self._background_tasks,
                return_when=asyncio.FIRST_EXCEPTION,
            )

            for task in pending:
                task.cancel()

            for task in done:
                if exc := task.exception():
                    raise exc

        except asyncio.CancelledError:
            logger.info("Progenitor loop cancelled.")
            raise
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        if self._shutdown_started:
            return
        self._shutdown_started = True
        logger.info("Progenitor shutting down...")
        self.running = False

        if self._ipc_server is not None:
            await self._ipc_server.stop()
            self._ipc_server = None

        if self._http_server is not None:
            await self._http_server.stop()
            self._http_server = None

        if self.loop_runner:
            self.loop_runner.stop()

        for task in self._background_tasks:
            if task.cancelled():
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._background_tasks.clear()

        await self.state_manager.commit_state()
        await self.llm_client.close()
        await write_health_status(self.state_dir, "offline")

        logger.info("State persisted. Goodbye.")
