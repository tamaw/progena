"""Progena service entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from .config import get_config
from .runtime.logging import setup_logging
from .runtime.progena import Progena

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent


def setup_signal_handlers(progena: Progena) -> None:
    """Set up graceful shutdown handlers."""

    def handle_signal(signum, _frame):
        if progena._shutdown_started:
            return
        logger.info("Received signal %s. Initiating graceful shutdown...", signum)
        progena.running = False
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(progena.shutdown())
        except RuntimeError:
            asyncio.run(progena.shutdown())

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


async def main() -> None:
    """Main entry point."""
    config = get_config(PROJECT_ROOT)
    agent_name = os.getenv("AGENT_NAME", "").strip()
    if not agent_name:
        raise RuntimeError("AGENT_NAME must be set for progena")
    setup_logging(config, agent_name)

    progena = Progena(config)
    setup_signal_handlers(progena)

    try:
        await progena.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
    finally:
        await progena.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
