"""The conversation list's ``preview``, written onto the session after each turn."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.progena.sessions.history import Conversation
from src.progena.sessions.render import latest_message_preview
from src.progena.sessions.titles import (
    ATTACHMENT_CONTEXT_MARKER,
    SESSION_PREVIEW_CHARS,
    session_preview,
)
from src.progena.sessions.transcript import ThreadMessages


def _conversation(*messages) -> Conversation:
    return Conversation(
        root=ThreadMessages(messages=list(messages), committed_at={}),
        subagents={},
        checkpoint_id="cp-1",
    )


def test_preview_is_the_newest_spoken_message_not_a_tool_result_or_empty_call():
    conversation = _conversation(
        HumanMessage(content="look it up"),
        AIMessage(content=[{"type": "text", "text": "Found  it:\n\nthe   answer"}]),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "read_file", "args": {}}]),
        ToolMessage(content="raw file body", tool_call_id="c1"),
    )

    assert latest_message_preview(conversation) == "Found it: the answer"


def test_preview_drops_the_attachment_note_redacts_and_is_cut_with_an_ellipsis():
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
    note = f"{ATTACHMENT_CONTEXT_MARKER}\n[attachment: report.pdf]"

    assert session_preview(f"see {secret} {note}") == "see " + session_preview(secret)
    assert secret not in session_preview(f"see {secret}")
    long = session_preview("word " * 100)
    assert len(long) == SESSION_PREVIEW_CHARS
    assert long.endswith("…")
    assert latest_message_preview(_conversation()) == ""
