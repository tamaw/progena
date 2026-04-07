"""Unit tests for the greeting_ack agent."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the agent at a temporary log directory for each test."""
    monkeypatch.setenv("GREETING_ACK_LOG_DIR", str(tmp_path))
    # Force the module to reload so it picks up the new env var and creates a
    # fresh logger (avoids handler state leaking between tests).
    if "app.agents.greeting_ack.agent" in sys.modules:
        mod = sys.modules["app.agents.greeting_ack.agent"]
        # Close and remove any existing file handlers
        logger = logging.getLogger("greeting_ack")
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)
        # Reset the cached handler
        mod._file_handler = None  # type: ignore[attr-defined]
        # Patch the log dir on the already-imported module
        mod._LOG_DIR = tmp_path  # type: ignore[attr-defined]
        mod._LOG_FILE = tmp_path / "greeting-ack.log"  # type: ignore[attr-defined]
    yield tmp_path


def _get_handle():
    from app.agents.greeting_ack.agent import handle
    return handle


GREETINGS = ["hi", "Hi", "HI", "hI", "hello", "hey", "hiya"]


@pytest.mark.parametrize("greeting", GREETINGS)
def test_greeting_returns_acknowledgement(greeting: str):
    handle = _get_handle()
    result = handle({"message": greeting, "context": {}})
    text: str = result["text"]
    assert "hi there" in text.lower() or "hello" in text.lower() or "hey" in text.lower()
    assert "?" in text, "Response should include a follow-up question"


def test_greeting_hi_exact_response():
    handle = _get_handle()
    result = handle({"message": "hi"})
    assert result["text"] == "Hi there! What can I help you with next?"


def test_non_greeting_does_not_raise():
    handle = _get_handle()
    result = handle({"message": "What is the weather?"})
    assert "text" in result


def test_invocation_logs_work_item_id(isolated_log_dir: Path):
    handle = _get_handle()
    handle({"message": "hi"})
    log_file = isolated_log_dir / "greeting-ack.log"
    assert log_file.exists(), "Log file was not created"
    content = log_file.read_text(encoding="utf-8")
    assert "b64cd775-2cc6-4ec9-8895-ab55fbccc6ca" in content


def test_work_item_id_logged_on_non_greeting(isolated_log_dir: Path):
    """work_item_id should be logged regardless of message content."""
    handle = _get_handle()
    handle({"message": "something else"})
    log_file = isolated_log_dir / "greeting-ack.log"
    content = log_file.read_text(encoding="utf-8")
    assert "b64cd775-2cc6-4ec9-8895-ab55fbccc6ca" in content
