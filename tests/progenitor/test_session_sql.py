"""Postgres session trigger: msgpack decoder and once-per-thread creation.

Opt-in on a scratch database (``PROGENITOR_TEST_DATABASE_URL``): the runtime's
own setup creates the LangGraph tables and installs the triggers; each test
runs in a rolled-back transaction.
"""

from __future__ import annotations

import os
import re
from typing import Any

import psycopg
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.progenitor.database.langgraph import _open_saver, _open_store
from app.progenitor.document_kinds import SESSION_KEY_PREFIX

DATABASE_URL = (os.getenv("PROGENITOR_TEST_DATABASE_URL") or "").strip() or None

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="requires PROGENITOR_TEST_DATABASE_URL (a scratch database)"
)

_SERDE = JsonPlusSerializer()
_UTC_STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00$")


def _blob(*messages: Any) -> bytes:
    """A ``messages`` channel write as LangGraph stores it (a list of messages)."""
    return _SERDE.dumps_typed(list(messages))[1]


@pytest.fixture
async def conn():
    assert DATABASE_URL is not None
    _saver, setup_conn = await _open_saver(DATABASE_URL)
    await setup_conn.close()
    _store, pool = await _open_store(DATABASE_URL)
    await pool.close()
    async with await psycopg.AsyncConnection.connect(
        DATABASE_URL, autocommit=False, row_factory=dict_row
    ) as connection:
        yield connection
        await connection.rollback()


async def _write(
    conn: Any, thread_id: str, checkpoint_id: str, metadata: dict[str, Any], blob: bytes
) -> None:
    await conn.execute(
        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) "
        "VALUES (%s, '', %s, '{}'::jsonb, %s)",
        [thread_id, checkpoint_id, Jsonb(metadata)],
    )
    await conn.execute(
        "INSERT INTO checkpoint_writes "
        "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob) "
        "VALUES (%s, '', %s, 'task', 0, 'messages', 'msgpack', %s)",
        [thread_id, checkpoint_id, blob],
    )


async def _session(conn: Any, thread_id: str) -> dict[str, Any] | None:
    row = await (
        await conn.execute(
            "SELECT value->'payload' AS payload FROM store "
            "WHERE prefix = 'progenitor.state' AND key = %s",
            [f"{SESSION_KEY_PREFIX}{thread_id}"],
        )
    ).fetchone()
    return None if row is None else row["payload"]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("hello", "hello"),
        ("x" * 50, "x" * 50),
        ("x" * 256, "x" * 256),
        ("x" * 70_000, "x" * 70_000),
        ("你好世界 🌍", "你好世界 🌍"),
        ([{"type": "text", "text": "from a list"}], "from a list"),
        (
            [
                {"type": "image_url", "image_url": {"url": "http://example.com/a.png"}},
                {"type": "text", "text": "after image"},
            ],
            "after image",
        ),
        ([{"type": "image_url", "image_url": {"url": "http://example.com/a.png"}}], ""),
    ],
)
async def test_decoder_matches_langgraph_serde(conn: Any, content: Any, expected: str) -> None:
    row = await (
        await conn.execute(
            "SELECT progenitor_human_text(%s) AS text", [_blob(HumanMessage(content=content))]
        )
    ).fetchone()
    assert row["text"] == expected


async def test_decoder_ignores_non_human_messages(conn: Any) -> None:
    row = await (
        await conn.execute("SELECT progenitor_human_text(%s) AS text", [_blob(AIMessage("hi"))])
    ).fetchone()
    assert row["text"] is None


async def test_first_user_message_creates_one_session(conn: Any) -> None:
    tagged = {"source": "input", "progenitor_source": "telegram"}
    await _write(conn, "t-once", "cp1", tagged, _blob(HumanMessage("plan a hike")))
    await _write(conn, "t-once", "cp2", tagged, _blob(HumanMessage("second turn")))

    session = await _session(conn, "t-once")
    assert session is not None
    assert {k: session[k] for k in ("id", "thread_id", "source", "title", "first_message")} == {
        "id": "t-once",
        "thread_id": "t-once",
        "source": "telegram",
        "title": "New chat",
        "first_message": "plan a hike",
    }
    assert (session["pinned"], session["archived"], session["content"]) == (False, False, "")
    assert _UTC_STAMP.match(session["created_at"])
    assert session["last_activity"] == session["created_at"]


async def test_first_message_is_capped_and_image_only_reads_as_an_action(conn: Any) -> None:
    tagged = {"source": "input", "progenitor_source": "ui"}
    await _write(conn, "t-long", "cp1", tagged, _blob(HumanMessage("y" * 5000)))
    image = [{"type": "image_url", "image_url": {"url": "http://example.com/a.png"}}]
    await _write(conn, "t-image", "cp1", tagged, _blob(HumanMessage(content=image)))

    long_session = await _session(conn, "t-long")
    image_session = await _session(conn, "t-image")
    assert long_session is not None and len(long_session["first_message"]) == 4000
    assert image_session is not None and image_session["first_message"] == "* sends an image"


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"source": "input"}, HumanMessage("workflow step")),
        ({"source": "input", "progenitor_source": "workflow"}, HumanMessage("workflow step")),
        ({"source": "loop", "progenitor_source": "ui"}, HumanMessage("not an input")),
        ({"source": "input", "progenitor_source": "ui"}, AIMessage("assistant reply")),
    ],
)
async def test_non_conversation_writes_create_nothing(
    conn: Any, metadata: dict[str, Any], message: Any
) -> None:
    await _write(conn, "t-skip", "cp1", metadata, _blob(message))
    assert await _session(conn, "t-skip") is None
