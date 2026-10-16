"""Create ``session:<thread_id>`` documents for chats that predate session documents.

New chats get their session from the Postgres trigger on their first user
message. This one-off migration does the same for existing chat threads, then
removes the old chat metadata (``backend.chat.sessions``) and the old transcript
cache (``progenitor.conversations.meta`` / ``.logs``).

Which threads: root threads of the main agent whose input checkpoints hold a
user message, keyed like the app's chats: a UUID or ``thread_sess_*`` (``ui``),
``tele_*`` (``telegram``), ``conv_*`` (``api``). Workflow, routine, and
heartbeat threads are not chats and are skipped.

Per thread:
- ``first_message`` is decoded by the same SQL function the trigger uses.
- ``created_at`` / ``last_activity`` are its first input checkpoint and newest
  root checkpoint.
- ``title`` / ``pinned`` / ``archived`` come from its old ``backend.chat.sessions``
  row when one exists; a chat whose old row is marked deleted is not migrated.
- ``content`` is rendered by the runtime's transcript renderer.

Migrated sessions are not "a user started a chat": their ``created`` document
events are removed in the same transaction, so no workflow fires.

It runs once: a marker (``progenitor.migrations`` / ``sessions``) is written
with the sessions, and later runs refuse. After the cutover, deleting a chat
deletes only its session document, so a second run would bring deleted chats
back.

Usage (stop the runtime and backend first, so the old rows can't be rewritten):

    source .venv/bin/activate
    python scripts/migrate_sessions.py --dry-run
    python scripts/migrate_sessions.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.progenitor.database.langgraph import _open_saver  # noqa: E402
from app.progenitor.document_kinds import SESSION_KEY_PREFIX  # noqa: E402
from app.progenitor.sessions.render import render_session_content  # noqa: E402
from app.progenitor.sessions.titles import DEFAULT_CHAT_TITLE  # noqa: E402

STATE_PREFIX = "progenitor.state"
OLD_CHAT_PREFIX = "backend.chat.sessions"
OLD_TRANSCRIPT_PREFIXES = ("progenitor.conversations.meta", "progenitor.conversations.logs")
MIGRATION_PREFIX = "progenitor.migrations"
MIGRATION_KEY = "sessions"

_UI_THREAD = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|thread_sess_.+)$"
)

# The first user message of each root thread of the main agent, from the
# ``messages`` write at its earliest input checkpoint that holds one.
_CANDIDATES_SQL = """
SELECT DISTINCT ON (w.thread_id)
       w.thread_id,
       progenitor_session_first_message(w.blob) AS first_message,
       c.checkpoint->>'ts' AS created_at,
       (SELECT max(t.checkpoint->>'ts') FROM checkpoints t
         WHERE t.thread_id = w.thread_id AND t.checkpoint_ns = '') AS last_activity
FROM checkpoint_writes w
JOIN checkpoints c USING (thread_id, checkpoint_ns, checkpoint_id)
WHERE w.checkpoint_ns = ''
  AND w.channel = 'messages'
  AND c.metadata->>'source' = 'input'
  AND c.metadata->>'lc_agent_name' = 'progenitor'
  AND progenitor_session_first_message(w.blob) IS NOT NULL
ORDER BY w.thread_id, w.checkpoint_id, w.idx
"""


@dataclass(frozen=True)
class Candidate:
    thread_id: str
    source: str
    payload: dict[str, Any]


def thread_source(thread_id: str) -> str | None:
    """The session ``source`` for a chat thread id, or ``None`` if it isn't a chat."""
    if thread_id.startswith("tele_"):
        return "telegram"
    if thread_id.startswith("conv_"):
        return "api"
    if _UI_THREAD.match(thread_id):
        return "ui"
    return None


async def _candidates(
    conn: psycopg.AsyncConnection[dict[str, Any]],
) -> tuple[list[Candidate], dict[str, int]]:
    skipped = {"not_a_chat": 0, "already_migrated": 0, "deleted": 0}
    existing = {
        row["key"].removeprefix(SESSION_KEY_PREFIX)
        for row in await (
            await conn.execute(
                "SELECT key FROM store WHERE prefix = %s AND key LIKE %s",
                [STATE_PREFIX, f"{SESSION_KEY_PREFIX}%"],
            )
        ).fetchall()
    }
    old_rows = {
        row["key"]: row["payload"]
        for row in await (
            await conn.execute(
                "SELECT key, value->'payload' AS payload FROM store WHERE prefix = %s",
                [OLD_CHAT_PREFIX],
            )
        ).fetchall()
    }
    candidates: list[Candidate] = []
    for row in await (await conn.execute(_CANDIDATES_SQL)).fetchall():
        thread_id = row["thread_id"]
        source = thread_source(thread_id)
        if source is None:
            skipped["not_a_chat"] += 1
            continue
        if thread_id in existing:
            skipped["already_migrated"] += 1
            continue
        old = old_rows.get(thread_id) or {}
        if old.get("deleted"):
            skipped["deleted"] += 1
            continue
        candidates.append(
            Candidate(
                thread_id=thread_id,
                source=source,
                payload={
                    "id": thread_id,
                    "thread_id": thread_id,
                    "source": source,
                    "title": str(old.get("title") or DEFAULT_CHAT_TITLE),
                    "first_message": row["first_message"],
                    "pinned": bool(old.get("pinned")),
                    "archived": bool(old.get("archived")),
                    "created_at": row["created_at"],
                    "last_activity": row["last_activity"],
                    "content": "",
                },
            )
        )
    return candidates, skipped


async def migrate(database_url: str, *, dry_run: bool) -> None:
    # Opening the saver runs the runtime's setup, which installs the session
    # trigger and ``progenitor_session_first_message``.
    saver, saver_conn = await _open_saver(database_url)
    try:
        async with await psycopg.AsyncConnection.connect(
            database_url, autocommit=False, row_factory=dict_row
        ) as conn:
            done = await (
                await conn.execute(
                    "SELECT value->'payload'->>'completed_at' AS at FROM store WHERE prefix = %s AND key = %s",
                    [MIGRATION_PREFIX, MIGRATION_KEY],
                )
            ).fetchone()
            if done is not None:
                print(f"already migrated at {done['at']}; nothing to do")
                return
            candidates, skipped = await _candidates(conn)
            preferences_row = await (
                await conn.execute(
                    "SELECT value->'payload' AS payload FROM store WHERE prefix = %s AND key = 'preferences'",
                    [STATE_PREFIX],
                )
            ).fetchone()
            preferences = preferences_row["payload"] if preferences_row else {}

            by_source: dict[str, int] = {}
            for candidate in candidates:
                by_source[candidate.source] = by_source.get(candidate.source, 0) + 1
                content = await render_session_content(
                    saver,
                    candidate.thread_id,
                    title=candidate.payload["title"],
                    preferences=preferences,
                )
                candidate.payload["content"] = content or ""

            print(f"sessions to create: {len(candidates)} {by_source}")
            print(f"skipped: {skipped}")
            if dry_run:
                for candidate in candidates:
                    print(
                        f"  {candidate.thread_id} [{candidate.source}] {candidate.payload['first_message'][:60]!r}"
                    )
                await conn.rollback()
                return

            before = await (
                await conn.execute("SELECT coalesce(max(seq), 0) AS seq FROM document_events")
            ).fetchone()
            keys = [f"{SESSION_KEY_PREFIX}{c.thread_id}" for c in candidates]
            for candidate in candidates:
                await conn.execute(
                    "INSERT INTO store (prefix, key, value, created_at, updated_at) "
                    "VALUES (%s, %s, %s, now(), now()) ON CONFLICT (prefix, key) DO NOTHING",
                    [
                        STATE_PREFIX,
                        f"{SESSION_KEY_PREFIX}{candidate.thread_id}",
                        Jsonb({"payload": candidate.payload}),
                    ],
                )
            removed_events = await conn.execute(
                "DELETE FROM document_events WHERE seq > %s AND prefix = %s AND key = ANY(%s) AND op = 'created'",
                [before["seq"] if before else 0, STATE_PREFIX, keys],
            )
            old_chat_rows = await conn.execute(
                "DELETE FROM store WHERE prefix = %s", [OLD_CHAT_PREFIX]
            )
            old_cache_rows = await conn.execute(
                "DELETE FROM store WHERE prefix = ANY(%s)", [list(OLD_TRANSCRIPT_PREFIXES)]
            )
            await conn.execute(
                "INSERT INTO store (prefix, key, value, created_at, updated_at) "
                "VALUES (%s, %s, jsonb_build_object('payload', jsonb_build_object("
                "'completed_at', now(), 'sessions_created', %s::int)), now(), now())",
                [MIGRATION_PREFIX, MIGRATION_KEY, len(candidates)],
            )
            await conn.commit()
            print(
                f"created {len(candidates)} sessions; suppressed {removed_events.rowcount} created events; "
                f"removed {old_chat_rows.rowcount} backend.chat.sessions rows and "
                f"{old_cache_rows.rowcount} transcript cache rows"
            )
    finally:
        await saver_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create session documents for existing chats.")
    parser.add_argument(
        "--database-url",
        default=os.getenv("PROGENITOR_DATABASE_URL", "").strip(),
        help="Postgres URL (default: $PROGENITOR_DATABASE_URL)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be created; change nothing"
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or PROGENITOR_DATABASE_URL is required")
    asyncio.run(migrate(args.database_url, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
