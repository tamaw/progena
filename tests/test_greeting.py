"""Tests for the greeting handler module."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.greeting.handler import GREETING_REPLY, handle_greeting, log_interaction


def test_greeting_reply_not_empty():
    assert GREETING_REPLY.strip()


def test_greeting_reply_contains_offer():
    assert "how can i help" in GREETING_REPLY.lower()


def test_greeting_reply_length():
    word_count = len(GREETING_REPLY.split())
    assert word_count <= 40, f"Reply too long: {word_count} words"


def test_handle_greeting_returns_reply(tmp_path):
    reply = handle_greeting(
        user_input="hi",
        work_item_id="greeting-test-001",
        a2a_context_id="ctx-test-001",
        log_dir=tmp_path,
    )
    assert reply == GREETING_REPLY


def test_log_interaction_writes_jsonl(tmp_path):
    log_interaction(
        user_input="hi",
        reply=GREETING_REPLY,
        work_item_id="greeting-test-001",
        a2a_context_id="ctx-test-001",
        log_dir=tmp_path,
    )
    log_file = tmp_path / "interaction_log.jsonl"
    assert log_file.exists()
    entry = json.loads(log_file.read_text().strip())
    assert entry["metadata"]["work_item_id"] == "greeting-test-001"
    assert entry["metadata"]["a2a_context_id"] == "ctx-test-001"
    assert entry["reply"] == GREETING_REPLY
    assert entry["event"] == "greeting_interaction"


def test_log_interaction_metadata_fields(tmp_path):
    log_interaction(
        user_input="hi",
        reply=GREETING_REPLY,
        work_item_id="greeting-55a0c5d9-df97ad67",
        a2a_context_id="df97ad67-9715-4304-946d-fb2c6fbd48c2",
        log_dir=tmp_path,
    )
    log_file = tmp_path / "interaction_log.jsonl"
    entry = json.loads(log_file.read_text().strip())
    assert "timestamp" in entry
    assert "work_item_id" in entry["metadata"]
    assert "a2a_context_id" in entry["metadata"]
