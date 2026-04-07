"""Greeting acknowledgement agent.

Detects simple greetings and returns a friendly acknowledgement with a
follow-up question.  Logs the work_item_id on every invocation.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

WORK_ITEM_ID = "b64cd775-2cc6-4ec9-8895-ab55fbccc6ca"

_LOG_DIR = Path(os.environ.get("GREETING_ACK_LOG_DIR", "logs"))
_LOG_FILE = _LOG_DIR / "greeting-ack.log"

_file_handler: logging.FileHandler | None = None


def _get_logger() -> logging.Logger:
    global _file_handler  # noqa: PLW0603
    logger = logging.getLogger("greeting_ack")
    if not logger.handlers:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _file_handler = logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8")
        _file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(_file_handler)
        logger.setLevel(logging.INFO)
    return logger


GREETINGS = {"hi", "hello", "hey", "hiya"}


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle an incoming A2A payload.

    Args:
        payload: dict with keys ``message`` (str) and optional ``context`` (dict).

    Returns:
        dict with key ``text`` containing the agent's response.
    """
    logger = _get_logger()
    logger.info("invoked work_item_id=%s", WORK_ITEM_ID)

    message: str = str(payload.get("message", "")).strip().lower().rstrip("!.,?")

    if message in GREETINGS:
        response = "Hi there! What can I help you with next?"
    else:
        response = (
            "I'm the greeting agent. Say 'hi' and I'll be happy to help you get started!"
        )

    return {"text": response}


class GreetingAckAgent:
    """Thin class wrapper around :func:`handle` for registry compatibility."""

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return handle(payload)
