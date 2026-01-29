"""
Progenitor Service Entrypoint

Perpetual loop agent that orchestrates TARDIS system operations.
Runs as a continuous LangGraph loop inside a Docker container.

Communication modes:
- IPC (default): Socket-based communication for reliability and streaming
- STDIO: Legacy stdin/stdout mode for backward compatibility
"""

import asyncio
import contextlib
import json
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

from .config import ProgenitorConfig, get_config, setup_logging
from .graph import create_progenitor_graph
from .graph_dual_loop import create_progenitor_graph as create_dual_loop_graph
from .runtime.loop import ProgenitorLoop
from .state.manager import StateManager
from .llm.client import LLMClient
from .tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


class Progenitor:
    """Main Progenitor agent service."""

    def __init__(self, config: Optional[ProgenitorConfig] = None):
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
        self.llm_client = LLMClient(config=self.config.llm)
        self.tool_registry = ToolRegistry(self.state_dir / "registry.json")
        self.graph = None
        self.loop_runner = None
        self._background_tasks: list[asyncio.Task] = []
        self._ipc_server = None  # Socket-based IPC server

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

        # Create the LangGraph - choose between original and dual-loop
        use_dual_loop = self.config.runtime.__dict__.get("use_dual_loop", True)

        if use_dual_loop:
            logger.info("Using dual-loop graph (loop.spec.md)")
            self.graph = create_dual_loop_graph(
                state_manager=self.state_manager,
                llm_client=self.llm_client,
                tool_registry=self.tool_registry,
                config=self.config,
                worktrees_dir=self.worktrees_dir,
            )
        else:
            logger.info("Using original graph")
            self.graph = create_progenitor_graph(
                state_manager=self.state_manager,
                llm_client=self.llm_client,
                tool_registry=self.tool_registry,
                config=self.config,
                worktrees_dir=self.worktrees_dir,
            )

        # Create loop runner
        self.loop_runner = ProgenitorLoop(
            graph=self.graph,
            state_manager=self.state_manager,
            config=self.config,
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

    async def run(self) -> None:
        """Run the perpetual agent loop.

        This method runs indefinitely until shutdown is requested.
        On graceful shutdown, it persists state before exiting.

        Communication mode is determined by config.runtime.ipc_enabled:
        - True (default): Use socket-based IPC for reliability and streaming
        - False: Use legacy stdin/stdout for backward compatibility
        """
        self.running = True

        logger.info("Progenitor starting...")

        await self.initialize()
        logger.info("Progenitor initialized. Entering perpetual loop.")

        loop_task = asyncio.create_task(self.loop_runner.run_forever())  # type: ignore[arg-type]
        self._background_tasks = [loop_task]

        # Choose communication mode based on configuration
        if self.config.runtime.ipc_enabled:
            logger.info(
                "Starting IPC server on %s:%d",
                self.config.runtime.ipc_host,
                self.config.runtime.ipc_port,
            )
            from .ipc import IPCServer

            self._ipc_server = IPCServer(
                loop_runner=self.loop_runner,
                host=self.config.runtime.ipc_host,
                port=self.config.runtime.ipc_port,
                max_clients=self.config.runtime.ipc_max_clients,
            )
            await self._ipc_server.start()
            ipc_task = asyncio.create_task(self._ipc_server.serve_forever())
            self._background_tasks.append(ipc_task)
        else:
            # Legacy stdin/stdout mode
            logger.info("Using legacy stdin/stdout communication mode")
            stdin_task = asyncio.create_task(self._stdin_listener())
            output_task = asyncio.create_task(self._output_writer())
            stream_task = asyncio.create_task(self._stream_writer())
            self._background_tasks.extend([stdin_task, output_task, stream_task])

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

        # Flush Langfuse traces before exit
        try:
            from .observability import flush_langfuse, shutdown_langfuse
            flush_langfuse()
            shutdown_langfuse()
        except ImportError:
            pass  # Langfuse not installed

        logger.info("State persisted. Goodbye.")

    async def _stdin_listener(self) -> None:
        """Read user messages from stdin and enqueue them for processing."""
        while not self._shutdown_started:
            try:
                line = await asyncio.to_thread(sys.stdin.readline)
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.error(f"Stdin listener error: {exc}", exc_info=True)
                await asyncio.sleep(0.1)
                continue

            if not line:
                await asyncio.sleep(0.05)
                continue

            raw = line.strip()
            if not raw:
                continue

            try:
                payload = json.loads(raw)
                content = payload.get("content") or payload.get("message")
                if payload.get("type") == "user_message" and payload.get("content"):
                    content = payload["content"]
            except json.JSONDecodeError:
                content = raw

            if content:
                await self.loop_runner.enqueue_input(str(content))  # type: ignore[union-attr]

    async def _output_writer(self) -> None:
        """Write assistant outputs to stdout as JSON lines for the client."""
        while not self._shutdown_started:
            message = await self.loop_runner.get_output(timeout=0.1)  # type: ignore[union-attr]
            if not message:
                continue

            payload = {"type": "assistant_message", "content": message}
            sys.stdout.write(json.dumps(payload) + "\n")
            sys.stdout.flush()

    async def _stream_writer(self) -> None:
        """Write streaming chunks to stdout as JSON lines for the client.

        Streaming chunks have type="stream_chunk" and include is_final flag.
        This enables real-time token streaming for better UX.
        """
        while not self._shutdown_started:
            chunk = await self.loop_runner.get_stream_chunk(timeout=0.1)  # type: ignore[union-attr]
            if not chunk:
                continue

            # Stream chunks are already dicts with type, content, is_final
            sys.stdout.write(json.dumps(chunk) + "\n")
            sys.stdout.flush()


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
