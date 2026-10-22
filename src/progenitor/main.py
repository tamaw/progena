"""Progenitor service entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from .config import get_config
from .runtime.logging import setup_logging
from .runtime.progenitor import Progenitor

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent


def setup_signal_handlers(progenitor: Progenitor) -> None:
    """Set up graceful shutdown handlers."""

    def handle_signal(signum, _frame):
        if progenitor._shutdown_started:
            return
        logger.info("Received signal %s. Initiating graceful shutdown...", signum)
        progenitor.running = False
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(progenitor.shutdown())
        except RuntimeError:
            asyncio.run(progenitor.shutdown())

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


async def main() -> None:
    """Main entry point."""
    config = get_config(PROJECT_ROOT)
    agent_name = os.getenv("AGENT_NAME", "").strip()
    if not agent_name:
        raise RuntimeError("AGENT_NAME must be set for progenitor")
    setup_logging(config, agent_name)

    progenitor = Progenitor(config)
    setup_signal_handlers(progenitor)

    try:
        await progenitor.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
    finally:
        await progenitor.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
