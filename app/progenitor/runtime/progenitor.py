"""Progenitor runtime lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from ..config import ProgenitorConfig, get_config
from ..llm.client import LLMClient
from ..mcp import MCPRuntime
from ..registry import AgentRegistryRuntime
from ..state.langgraph_store import LangGraphRuntimeStore
from ..state.manager import StateManager
from ..state.store_listener import RuntimeStoreListener
from .heartbeat import ProgenitorLoop
from .http import ProgenitorHTTPServer
from .mcp_sync import MCPSyncScheduler

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


class Progenitor:
    """Main Progenitor agent runtime."""

    def __init__(
        self,
        config: ProgenitorConfig | None = None,
    ):
        self.config = config or get_config(PROJECT_ROOT)
        self.running = False
        self._shutdown_started = False

        self.logs_dir = self.config.storage.log_dir
        if not self.config.database.enabled:
            raise ValueError(
                "PROGENITOR_DATABASE_URL is required; file-backed persistence is removed"
            )
        self.log_store: LangGraphRuntimeStore | None = None
        self.state_manager: StateManager | None = None
        self.llm_client = LLMClient(
            config=self.config.llm,
            mcp_config=self.config.mcp,
        )
        self.registry_runtime = AgentRegistryRuntime()
        self.mcp_runtime = MCPRuntime(self.config.mcp)
        self.llm_client.set_mcp_runtime(self.mcp_runtime)
        self.tool_registry = self.registry_runtime.agent_registry
        self.graph: Any = None
        self.loop_runner: Any = None
        self._http_server: ProgenitorHTTPServer | None = None
        self._background_tasks: list[asyncio.Task] = []
        self._persistence_stack = contextlib.AsyncExitStack()
        self._store_listener: RuntimeStoreListener | None = None
        self._mcp_scheduler: MCPSyncScheduler | None = None

    def _on_store_change(self, payload: str) -> None:
        """Handle a ``store`` NOTIFY by re-syncing the live MCP runtime.

        Runs in the listener task; routes by key onto the coalescing scheduler:
        ``mcp`` (server config: add/remove/enable/disable/url) needs a full
        re-discovery; ``mcp_tools`` (a per-tool enable toggle) only needs the
        disabled set re-applied. This is the single path for both UI-driven and
        agent-driven config changes — there is no separate write-hook.
        """
        if self._mcp_scheduler is None:
            return
        try:
            data = json.loads(payload) if payload else {}
            key = data.get("key") if isinstance(data, dict) else None
        except (ValueError, TypeError):
            key = None
        if key == "mcp":
            self._mcp_scheduler.request(full=True)
        elif key == "mcp_tools":
            self._mcp_scheduler.request(full=False)

    def _on_store_resync(self) -> None:
        """On (re)connect, recover any MCP config change missed while disconnected."""
        if self._mcp_scheduler is not None:
            self._mcp_scheduler.request(full=True)

    async def _do_mcp_full_resync(self) -> None:
        from ..nodes.mcp import sync_mcp_runtime_context

        if self.state_manager is None:
            return
        summary = await sync_mcp_runtime_context(
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
            mcp_config=self.config.mcp,
            state_manager=self.state_manager,
            reload_config=True,
        )
        logger.info("Re-synced MCP runtime from config change: %s", summary)

    async def _do_mcp_reapply(self) -> None:
        from ..nodes.mcp import reapply_mcp_disabled_tools

        count, _changed = await reapply_mcp_disabled_tools(self.llm_client, self.state_manager)
        logger.info("Re-applied MCP tool flags from store change (%d disabled)", count)

    async def initialize(self) -> None:
        """Initialize all components."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        from ..database import open_langgraph_persistence

        persistence = await self._persistence_stack.enter_async_context(
            open_langgraph_persistence(
                self.config.database.url,
                setup=self.config.database.setup,
                pool_min_size=self.config.database.pool_min_size,
                pool_max_size=self.config.database.pool_max_size,
            )
        )
        checkpointer = persistence.checkpointer
        store = persistence.store
        if store is None:
            raise RuntimeError("LangGraph Postgres store is required for runtime persistence")
        runtime_store = LangGraphRuntimeStore(
            store,
            debug_mode=os.getenv("PROGENITOR_STORE_LOG_DEBUG", "").lower() in ("1", "true", "yes"),
        )
        self.log_store = runtime_store
        self.state_manager = StateManager(runtime_store=runtime_store)
        await self.state_manager.initialize()
        mcp_payload = await self.state_manager.get_mcp_config()
        self.config.mcp = type(self.config.mcp).from_payload(mcp_payload)
        self.llm_client.mcp_config = self.config.mcp
        self.mcp_runtime.config = self.config.mcp
        # Back the registry with the database (registry document); agents.json is
        # only used to migrate any pre-existing entries on first load.
        self.registry_runtime.bind_state_manager(self.state_manager)
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

        assert self.state_manager is not None
        self.graph = await create_deep_agent_for_runtime(
            config=self.config,
            checkpointer=checkpointer,
            store=store,
            tool_registry=self.tool_registry,
            llm_client=self.llm_client,
            state_manager=self.state_manager,
            project_root=str(PROJECT_ROOT),
            log_sink=self.log_store,
            # The loop is created below, after the graph; resolve it lazily so
            # tools always see the live conversation state once it's running.
            live_state_provider=lambda: (
                self.loop_runner.state if self.loop_runner is not None else None
            ),
        )

        self.loop_runner = ProgenitorLoop(
            graph=self.graph,
            state_manager=self.state_manager,
            config=self.config,
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
            project_root=PROJECT_ROOT,
            log_sink=self.log_store,
        )
        # React to store writes (UI server/tool toggles, agent edits to
        # /config/mcp.json) by re-syncing the live MCP runtime immediately, rather
        # than waiting for the next heartbeat MCP refresh. The scheduler serializes
        # and coalesces this work so bursts can't race on the shared runtime.
        self._mcp_scheduler = MCPSyncScheduler(
            full_resync=self._do_mcp_full_resync,
            reapply=self._do_mcp_reapply,
        )
        self._store_listener = RuntimeStoreListener(
            self.config.database.url,
            on_change=self._on_store_change,
            on_resync=self._on_store_resync,
        )
        await self._store_listener.start()

    async def run(self) -> None:
        """Run the perpetual agent loop until shutdown."""
        self.running = True

        logger.info("Progenitor starting...")

        await self.initialize()
        logger.info("Progenitor initialized. Entering perpetual loop.")

        assert self.loop_runner is not None, "loop_runner must be initialized before run"
        loop_task = asyncio.create_task(self.loop_runner.run_forever())
        self._background_tasks = [loop_task]

        assert self.state_manager is not None, (
            "state_manager must be initialized before HTTP server"
        )
        self._http_server = ProgenitorHTTPServer(
            state_manager=self.state_manager,
            loop_runner=self.loop_runner,
            tool_registry=self.tool_registry,
            host=self.config.runtime.http_host,
            port=self.config.runtime.http_port,
            public_base_url=self.config.runtime.http_public_base_url,
        )
        await self._http_server.start()

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

        if self._store_listener is not None:
            await self._store_listener.stop()
            self._store_listener = None

        if self._mcp_scheduler is not None:
            await self._mcp_scheduler.aclose()
            self._mcp_scheduler = None

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

        if self.state_manager is not None:
            await self.state_manager.commit_state()
            await self.state_manager.close()
            self.state_manager = None
        await self._persistence_stack.aclose()
        self.log_store = None
        await self.llm_client.close()

        logger.info("State persisted. Goodbye.")
