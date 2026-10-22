"""Progenitor runtime lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from src.progenitor.documents.kinds import AGENT_DOCUMENT_KEY_PREFIX, ROUTINE_GRAPH_KEY_PREFIX
from src.progenitor.llm.client import LLMClient
from src.progenitor.mcp.runtime import MCPRuntime
from src.progenitor.persistence.listener import RuntimeStoreListener
from src.progenitor.persistence.store import LangGraphRuntimeStore
from src.progenitor.registry.runtime import AgentRegistryRuntime
from src.progenitor.runtime.http.server import ProgenitorHTTPServer
from src.progenitor.settings.providers import (
    configure_provider_repository,
    get_provider_repository,
    provider_database_url,
    shutdown_provider_repository,
)

from ..config import ProgenitorConfig, get_config
from ..state.manager import StateManager
from .agent_sync import AgentSyncScheduler
from .heartbeat import ProgenitorLoop
from .mcp_sync import MCPSyncScheduler
from .plugin_sync import PluginSyncScheduler

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
        self._plugin_scheduler: PluginSyncScheduler | None = None
        self._agent_scheduler: AgentSyncScheduler | None = None
        self._provider_repository_open = False

    def _on_store_change(self, payload: str) -> None:
        """Refresh the chat-facing live registries a ``store`` NOTIFY touches.

        The runtime only listens for what chat calls into: ``mcp`` (server
        config) needs a full re-discovery, ``mcp_tools`` (a per-tool toggle)
        only re-applies the disabled set, ``plugins`` runs the plugin
        reconciler, and ``agent:`` and routine documents refresh the sub-agent
        registries (so new agents and routines are runnable without a restart).
        Each routes onto a coalescing scheduler. Documents the backend also
        writes (``repo:<id>`` documents, repo scan, background tasks) are read through from the
        store by ``StateManager`` instead, so they need no notification here.
        """
        try:
            data = json.loads(payload) if payload else {}
            key = data.get("key") if isinstance(data, dict) else None
        except (ValueError, TypeError):
            key = None
        if not isinstance(key, str):
            return
        if key.startswith((AGENT_DOCUMENT_KEY_PREFIX, ROUTINE_GRAPH_KEY_PREFIX)):
            if self._agent_scheduler is not None:
                self._agent_scheduler.request()
        elif key in ("mcp", "mcp_tools"):
            if self._mcp_scheduler is not None:
                self._mcp_scheduler.request(full=key == "mcp")
        elif key == "plugins" and self._plugin_scheduler is not None:
            self._plugin_scheduler.request()

    def _on_store_resync(self) -> None:
        """Recover MCP, plugin, and object-agent changes missed while disconnected."""
        if self._mcp_scheduler is not None:
            self._mcp_scheduler.request(full=True)
        if self._plugin_scheduler is not None:
            self._plugin_scheduler.request()
        if self._agent_scheduler is not None:
            self._agent_scheduler.request()

    async def _do_agent_refresh(self) -> None:
        holder = self.graph
        if getattr(holder, "object_agent_registry", None) is None:
            holder = getattr(self.graph, "agent", None)
        for name in ("object_agent_registry", "routine_subagent_registry"):
            registry = getattr(holder, name, None)
            if registry is not None:
                await registry.refresh()

    async def _do_mcp_full_resync(self) -> None:
        from src.progenitor.orchestration.nodes.mcp import sync_mcp_runtime_context

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
        from src.progenitor.orchestration.nodes.mcp import reapply_mcp_disabled_tools

        count, _changed = await reapply_mcp_disabled_tools(self.llm_client, self.state_manager)
        logger.info("Re-applied MCP tool flags from store change (%d disabled)", count)

    async def _do_plugin_reconcile(self) -> None:
        from ..plugins.install import reconcile_plugins

        if self.state_manager is None:
            return
        summary = await reconcile_plugins(self.state_manager, log_sink=self.log_store)
        # Reconcile may install/remove multiple agent documents. Refresh after
        # completion too, even if their notifications preceded this pass.
        if self._agent_scheduler is not None:
            self._agent_scheduler.request()
        logger.info("Reconciled plugins from config change: %s", summary)

    async def _migrate_legacy_model_profiles(self) -> None:
        """Persist the one-time Azure-profile conversion when a Foundry exists."""
        if self.state_manager is None:
            return
        payload = await self.state_manager.get_model_profiles_config()
        if not any(
            isinstance(profile, dict)
            and profile.get("provider") == "azure_openai"
            and "provider_id" not in profile
            for profile in payload.values()
        ):
            return
        providers = await asyncio.to_thread(get_provider_repository().list_public)
        foundry = next(
            (
                provider
                for provider in providers
                if provider.get("kind") == "foundry" and provider.get("is_default")
            ),
            next((provider for provider in providers if provider.get("kind") == "foundry"), None),
        )
        if foundry is None:
            logger.warning("Legacy Azure model profiles are awaiting a Foundry provider migration")
            return
        from src.progenitor.settings.model_profiles import migrate_legacy_model_profiles_payload

        migrated = migrate_legacy_model_profiles_payload(payload, str(foundry["id"]))
        if migrated != payload:
            await self.state_manager.save_model_profiles_config(migrated)

    async def initialize(self) -> None:
        """Initialize all components."""
        await asyncio.to_thread(
            configure_provider_repository,
            provider_database_url(self.config.database.url),
        )
        self._provider_repository_open = True

        self.logs_dir.mkdir(parents=True, exist_ok=True)

        from src.progenitor.persistence.langgraph import open_langgraph_persistence

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
        self.state_manager = StateManager(
            runtime_store=runtime_store,
            database_url=self.config.database.url,
        )
        await self.state_manager.initialize()
        await self._migrate_legacy_model_profiles()
        mcp_payload = await self.state_manager.get_mcp_config()
        self.config.mcp = type(self.config.mcp).from_payload(mcp_payload)
        self.llm_client.mcp_config = self.config.mcp
        self.mcp_runtime.config = self.config.mcp
        # Back the registry with the database (registry document); /config/agents.json
        # is only a virtual view of it, never read from disk.
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
        self._plugin_scheduler = PluginSyncScheduler(
            reconcile=self._do_plugin_reconcile,
        )
        self._agent_scheduler = AgentSyncScheduler(reconcile=self._do_agent_refresh)
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

        if self._plugin_scheduler is not None:
            await self._plugin_scheduler.aclose()
            self._plugin_scheduler = None

        if self._agent_scheduler is not None:
            await self._agent_scheduler.aclose()
            self._agent_scheduler = None

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
        if self._provider_repository_open:
            await asyncio.to_thread(shutdown_provider_repository)
            self._provider_repository_open = False

        logger.info("State persisted. Goodbye.")
