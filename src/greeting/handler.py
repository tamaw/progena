"""Greeting handler — acknowledges user greetings and logs interaction metadata.

Handles simple user greetings (e.g. 'hi') by generating a friendly reply that
offers assistance and prompts for clarification of the user's intent.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GREETING_REPLY = "Hi there! How can I help you today? Let me know what you need and I'll get right on it."


def handle_greeting(
    user_input: str,
    work_item_id: str,
    a2a_context_id: str,
    log_dir: Path | str | None = None,
) -> str:
    """Acknowledge a user greeting and log the interaction.

    Args:
        user_input: The incoming user message (e.g. 'hi').
        work_item_id: The work item identifier for this interaction.
        a2a_context_id: The A2A context identifier for this interaction.
        log_dir: Directory to write interaction logs. Defaults to data/state/.

    Returns:
        The greeting reply string.
    """
    reply = GREETING_REPLY
    log_interaction(
        user_input=user_input,
        reply=reply,
        work_item_id=work_item_id,
        a2a_context_id=a2a_context_id,
        log_dir=log_dir,
    )
    return reply


def log_interaction(
    user_input: str,
    reply: str,
    work_item_id: str,
    a2a_context_id: str,
    log_dir: Path | str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write a greeting interaction entry to the JSONL interaction log.

    Args:
        user_input: The user's original message.
        reply: The assistant reply that was sent.
        work_item_id: Work item identifier for traceability.
        a2a_context_id: A2A context identifier for traceability.
        log_dir: Directory to write logs. Defaults to data/state/.
        extra: Optional additional metadata fields.
    """
    entry: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": "greeting_interaction",
        "user_input": user_input,
        "reply": reply,
        "metadata": {
            "work_item_id": work_item_id,
            "a2a_context_id": a2a_context_id,
            **(extra or {}),
        },
    }

    logger.info(
        "Greeting interaction logged — work_item_id=%s a2a_context_id=%s",
        work_item_id,
        a2a_context_id,
    )

    if log_dir is None:
        root = Path(__file__).resolve().parents[2]
        log_dir = root / "data" / "state"

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / "interaction_log.jsonl"

    with open(log_file, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
