"""Alerts relay."""

from __future__ import annotations

import json
import logging
from typing import Any

import app.backend.alerts.service as alerts_service
import app.backend.integrations.runtime as integrations_runtime
import app.backend.integrations.subprocess as integrations_subprocess

logger = logging.getLogger(__name__)


def _build_alert_summary_prompt(alert: alerts_service.AlertInfo) -> str:
    """Build the follow-up prompt that turns an alert into a normal user-facing message."""
    details: dict[str, Any] = {
        "id": alert.id,
        "type": alert.type,
        "title": alert.title,
        "message": alert.message,
        "urgent": alert.urgent,
        "requires_response": alert.requires_response,
    }
    if alert.context:
        details["context"] = alert.context
    lines = [
        "A backend alert was just created for the user.",
        "Use the `say_to_user` tool exactly once.",
        "Do not call `ask_user` or any other tools.",
        "Write a short alert-style summary for the user on their current platform.",
        "Keep the message faithful to the alert details, but summarize it clearly and naturally.",
    ]
    approver = (alert.context or {}).get("approver")
    if alert.requires_response and approver == "human":
        lines.append(
            "This needs a person's decision in the Progenitor UI; say where to answer it "
            "and that you cannot answer it for them."
        )
    elif alert.requires_response:
        lines.append(
            "Tell the user they can answer by replying to you (you will find it under /alerts/)."
        )
    lines += [
        "After the tool call, reply with exactly: sent",
        "```json",
        json.dumps(details, indent=2),
        "```",
    ]
    return "\n".join(lines)


async def _relay_alert(
    progenitor: integrations_subprocess.ProgenitorClient
    | integrations_runtime.SSEProgenitorClient
    | None,
    alert: alerts_service.AlertInfo,
) -> None:
    """Ask Progenitor to deliver a new alert through the normal user-message path."""
    if progenitor is None:
        logger.warning("Not relaying alert %s: backend client is unavailable", alert.id)
        return
    try:
        if not progenitor.is_running:
            await progenitor.start()
        response_text = (
            await progenitor.send_message(
                _build_alert_summary_prompt(alert),
                request_id=f"alert-summary-{alert.id}",
            )
        ).response.strip()
        if response_text and response_text.lower() != "sent":
            logger.debug(
                "Alert summary helper returned non-sentinel text for %s: %s",
                alert.id,
                response_text,
            )
    except Exception as e:
        logger.warning("Failed to relay alert %s: %s", alert.id, e)
