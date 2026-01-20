"""
Progenitor Service Entrypoint

Perpetual loop agent that orchestrates TARDIS system operations.
Runs as a continuous LangGraph loop inside a Docker container.
"""

import asyncio
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
        """
        self.running = True
        print("Progenitor starting...")

        await self.initialize()
        print("Progenitor initialized. Entering perpetual loop.")

        try:
            await self.loop_runner.run_forever()  # type: ignore[union-attr]
        except asyncio.CancelledError:
            print("Progenitor loop cancelled.")
            raise
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        print("Progenitor shutting down...")
        self.running = False

        # Persist final state
        await self.state_manager.commit_state()
        print("State persisted. Goodbye.")


def setup_signal_handlers(progenitor: Progenitor) -> None:
    """Set up graceful shutdown handlers."""
    def handle_signal(signum, frame):
        print(f"\nReceived signal {signum}. Initiating graceful shutdown...")
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
        print("\nInterrupted by user.")
    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await progenitor.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
