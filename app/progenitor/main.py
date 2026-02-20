"""
Progenitor Service Entrypoint

Perpetual loop agent that orchestrates TARDIS system operations.
Runs as a continuous LangGraph loop inside a Docker container.

Communication:
- IPC: Socket-based communication for reliability and streaming
"""

# CI: comment-only change to validate path filters.

import asyncio
import contextlib
import json
import logging
import signal
from pathlib import Path
from typing import Any

from .config import ProgenitorConfig, get_config, setup_logging
from .graph_background_loop import create_background_graph
from .graph_chat_loop import create_progenitor_graph
from .llm.client import LLMClient
from .runtime.copilot_acp import CopilotACPManager
from .runtime.loop import ProgenitorLoop
from .state.manager import StateManager
from .tools.mcp import setup_mcp_for_llm_client
from .tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


class Progenitor:
    """Main Progenitor agent service."""

    def __init__(self, config: ProgenitorConfig | None = None):
        self.config = config or get_config(PROJECT_ROOT, CONFIG_DIR)
        self.running = False
        self._shutdown_started = False

        # Use paths from configuration
        self.state_dir = self.config.storage.state_dir
        self.logs_dir = self.config.storage.log_dir
        self.worktrees_dir = self.config.storage.worktree_dir

        self.state_manager = StateManager(
            self.state_dir,
            self.logs_dir,
            lock_timeout=self.config.storage.state_lock_timeout_seconds,
        )
        self.llm_client = LLMClient(config=self.config.llm, mcp_config=self.config.mcp)
        self.tool_registry = ToolRegistry(self.state_dir / "registry.json")
        self.graph = None
        self.loop_runner: Any = None
        self._background_tasks: list[asyncio.Task] = []
        self._ipc_server: Any = None  # Socket-based IPC server
        self._copilot_acp_manager: CopilotACPManager | None = None

    async def initialize(self) -> None:
        """Initialize all components."""
        # Ensure directories exist
        for dir_path in [self.state_dir, self.logs_dir, self.worktrees_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)

        # Log resolved configuration (with secrets redacted) per SPEC
        await self._log_startup_config()

        # Load state
        await self.state_manager.initialize()

        # Load tool/agent registry
        await self.tool_registry.load()

        # Warm system tool registry so tools are discovered at startup
        from .nodes.node_helpers.registry import get_system_tool_names
        system_tool_names = get_system_tool_names()
        system_tool_count = len(system_tool_names)
        logger.info(
            "System tools discovered: %d (%s)",
            system_tool_count,
            ", ".join(system_tool_names),
        )

        # Initialize MCP tools and register with tool registry
        await setup_mcp_for_llm_client(self.llm_client, self.config.mcp)
        await self._register_mcp_tools()

        agent_count = len(self.tool_registry.get_agents())
        mcp_tool_count = len(self.tool_registry.get_tools())
        local_mcp_count = 0
        if hasattr(self.tool_registry, "get_local_mcp_tools"):
            try:
                local_mcp_count = len(self.tool_registry.get_local_mcp_tools())
            except Exception:
                local_mcp_count = 0

        logger.info(
            "Startup discovery: system_tools=%d, agents=%d, mcp_tools=%d (local_mcp=%d)",
            system_tool_count,
            agent_count,
            mcp_tool_count,
            local_mcp_count,
        )

        # Initialize Copilot ACP manager
        # The manager spawns copilot processes per-request via stdio (no persistent service)
        logger.info("Initializing Copilot ACP manager")
        self._copilot_acp_manager = CopilotACPManager(
            copilot_cmd=self.config.copilot.bin_path,
            model=self.config.copilot.model,
            yolo="--yolo" in self.config.copilot.default_flags,
            allowed_dirs=self.config.copilot.acp_allowed_dirs,
            timeout=self.config.copilot.timeout_seconds,
        )
        logger.info("Copilot ACP manager initialized")

        # Create a deferred event pusher that will be bound to the loop later
        # This allows the graph to be created before the loop, but still have
        # access to the loop's event pushing capability
        async def deferred_push_event(event_type: str, content: str, metadata=None):
            """Event pusher that delegates to the loop runner when available."""
            if self.loop_runner:
                await self.loop_runner._push_event(event_type, content, metadata)
            else:
                logger.warning("Event pusher called before loop_runner available: %s", event_type)

        # Allow the LLM client to emit realtime progress updates (e.g., tool calls)
        # through the same IPC event channel.
        self.llm_client.set_event_pusher(deferred_push_event)

        # Create the outer loop graph
        logger.info("Creating outer loop graph")
        self.graph = create_progenitor_graph(
            state_manager=self.state_manager,
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
            config=self.config,
            worktrees_dir=self.worktrees_dir,
            event_pusher=deferred_push_event,
            copilot_acp_manager=self._copilot_acp_manager,
        )

        # Create the background job graph (scheduled tasks)
        logger.info("Creating background job graph")
        background_graph = create_background_graph(
            state_manager=self.state_manager,
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
            config=self.config,
            worktrees_dir=self.worktrees_dir,
            copilot_acp_manager=self._copilot_acp_manager,
            event_pusher=deferred_push_event,
        )

        # Create loop runner
        self.loop_runner = ProgenitorLoop(
            graph=self.graph,
            background_graph=background_graph,
            state_manager=self.state_manager,
            config=self.config,
            llm_client=self.llm_client,
            worktrees_dir=self.worktrees_dir,
            copilot_acp_manager=self._copilot_acp_manager,
        )

    async def _log_startup_config(self) -> None:
        """Log resolved configuration to decisions.jsonl on startup."""
        from .runtime.time_utils import utcnow_isoformat
        config_log = {
            "event": "startup_config",
            "timestamp": utcnow_isoformat(),
            "config": self.config.to_dict(redact_secrets=True),
        }

        decisions_path = self.logs_dir / "decisions.jsonl"
        decisions_path.parent.mkdir(parents=True, exist_ok=True)

        with open(decisions_path, "a") as f:
            f.write(json.dumps(config_log) + "\n")

    async def _register_mcp_tools(self) -> None:
        """Register MCP tools discovered via LiteLLM MCP."""
        if not self.llm_client.mcp_tools:
            return

        for tool in self.llm_client.get_mcp_tool_registry_entries():
            await self.tool_registry.register_tool(tool)

    async def run(self) -> None:
        """Run the perpetual agent loop.

        This method runs indefinitely until shutdown is requested.
        On graceful shutdown, it persists state before exiting.

        Uses socket-based IPC for communication.
        """
        self.running = True

        logger.info("Progenitor starting...")

        await self.initialize()
        logger.info("Progenitor initialized. Entering perpetual loop.")

        loop_task = asyncio.create_task(self.loop_runner.run_forever())
        self._background_tasks = [loop_task]

        # Start IPC server for communication
        logger.info(
            "Starting IPC server on %s:%d",
            self.config.runtime.ipc_host,
            self.config.runtime.ipc_port,
        )
        from .ipc import IPCServer

        assert self.loop_runner is not None, "loop_runner must be initialized before IPC server"
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

        # Stop IPC server if running
        if self._ipc_server:
            await self._ipc_server.stop()
            self._ipc_server = None

        # Clear Copilot ACP manager reference
        self._copilot_acp_manager = None

        # Stop loop runner so the main loop exits promptly
        if self.loop_runner:
            self.loop_runner.stop()

        # Cancel background tasks cleanly
        for task in self._background_tasks:
            if task.cancelled():
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._background_tasks.clear()

        # Persist final state
        await self.state_manager.commit_state()

        # Write offline status to health file
        await self._write_health_status("offline")

        logger.info("State persisted. Goodbye.")

    async def _write_health_status(self, status: str) -> None:
        """Write health status to health.json file."""
        import os

        from .runtime.time_utils import utcnow_isoformat

        health_path = self.state_dir / "health.json"
        health_data = {
            "status": status,
            "last_active": utcnow_isoformat(),
            "run_id": None,
            "cycle_count": 0,
            "pid": os.getpid(),
        }

        try:
            health_path.write_text(json.dumps(health_data))
            logger.info("Health status set to '%s'", status)
        except Exception as e:
            logger.warning("Failed to write health status: %s", e)


def setup_signal_handlers(progenitor: Progenitor) -> None:
    """Set up graceful shutdown handlers."""
    def handle_signal(signum, frame):
        if progenitor._shutdown_started:
            return
        logger.info(f"Received signal {signum}. Initiating graceful shutdown...")
        progenitor.running = False
        # Create a new event loop for shutdown if needed
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(progenitor.shutdown())
        except RuntimeError:
            # No running loop, use synchronous approach
            asyncio.run(progenitor.shutdown())

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


async def main() -> None:
    """Main entry point."""
    # Load configuration and setup logging first
    config = get_config(PROJECT_ROOT, CONFIG_DIR)
    setup_logging(config)

    progenitor = Progenitor(config)
    setup_signal_handlers(progenitor)

    try:
        await progenitor.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
    finally:
        await progenitor.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
