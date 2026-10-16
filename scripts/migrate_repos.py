"""Split the single ``repos`` state document into one ``repo:<id>`` document each.

Declared repositories used to live in one ``repos`` document shaped
``{"repos": {"<id>": {...}}}``. Each entry now is its own ``repo:<id>``
document (typed by ``repo_entry.schema.v1.json``), so every write touches one
repo and fires its own ``repo`` document event.

Runs once: it records ``progenitor.migrations``/``repos`` when done. Entries are
validated with the same rule every write uses; an invalid entry aborts the run
with nothing changed. The ``created`` events the inserts cause are deleted, so
workflows triggering on ``repo`` creation don't fire for repos that already
existed.

    python scripts/migrate_repos.py --dry-run
    python scripts/migrate_repos.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.progenitor.document_kinds import REPO_KEY_PREFIX  # noqa: E402
from app.progenitor.repos.schema import validate_repo_entry  # noqa: E402

STATE_PREFIX = "progenitor.state"
OLD_REPOS_KEY = "repos"
MIGRATION_PREFIX = "progenitor.migrations"
MIGRATION_KEY = "repos"


def migrate(database_url: str, *, dry_run: bool) -> None:
    with psycopg.connect(database_url, autocommit=False, row_factory=dict_row) as conn:
        done = conn.execute(
            "SELECT value->'payload'->>'completed_at' AS at FROM store WHERE prefix = %s AND key = %s",
            [MIGRATION_PREFIX, MIGRATION_KEY],
        ).fetchone()
        if done is not None:
            print(f"already migrated at {done['at']}; nothing to do")
            return
        row = conn.execute(
            "SELECT value->'payload' AS payload FROM store WHERE prefix = %s AND key = %s",
            [STATE_PREFIX, OLD_REPOS_KEY],
        ).fetchone()
        payload = row["payload"] if row is not None else None
        entries = payload.get("repos") if isinstance(payload, dict) else None
        if entries is not None and not isinstance(entries, dict):
            raise SystemExit("the 'repos' document's 'repos' value is not an object; fix it first")
        entries = entries or {}
        documents = {
            repo_id: validate_repo_entry(
                repo_id,
                entry,
                created_at=entry.get("created_at") if isinstance(entry, dict) else None,
            )
            for repo_id, entry in entries.items()
        }
        existing = {
            r["key"].removeprefix(REPO_KEY_PREFIX)
            for r in conn.execute(
                "SELECT key FROM store WHERE prefix = %s AND key LIKE %s",
                [STATE_PREFIX, f"{REPO_KEY_PREFIX}%"],
            ).fetchall()
        }
        clashes = sorted(set(documents) & existing)
        if clashes:
            raise SystemExit(f"repo documents already exist for {clashes}; nothing changed")

        print(
            f"repos to split out: {len(documents)} (old 'repos' document present: {row is not None})"
        )
        for repo_id, entry in sorted(documents.items()):
            print(f"  {repo_id}: {entry['name']} -> {entry['local_path']} [{entry['access']}]")
        if dry_run:
            conn.rollback()
            return

        before = conn.execute("SELECT coalesce(max(seq), 0) AS seq FROM document_events").fetchone()
        keys = [f"{REPO_KEY_PREFIX}{repo_id}" for repo_id in documents]
        for repo_id, entry in documents.items():
            conn.execute(
                "INSERT INTO store (prefix, key, value, created_at, updated_at) "
                "VALUES (%s, %s, %s, now(), now())",
                [STATE_PREFIX, f"{REPO_KEY_PREFIX}{repo_id}", Jsonb({"payload": entry})],
            )
        removed_events = conn.execute(
            "DELETE FROM document_events WHERE seq > %s AND prefix = %s AND key = ANY(%s) AND op = 'created'",
            [before["seq"] if before else 0, STATE_PREFIX, keys],
        )
        old_rows = conn.execute(
            "DELETE FROM store WHERE prefix = %s AND key = %s", [STATE_PREFIX, OLD_REPOS_KEY]
        )
        conn.execute(
            "INSERT INTO store (prefix, key, value, created_at, updated_at) "
            "VALUES (%s, %s, jsonb_build_object('payload', jsonb_build_object("
            "'completed_at', now(), 'repos_created', %s::int)), now(), now())",
            [MIGRATION_PREFIX, MIGRATION_KEY, len(documents)],
        )
        conn.commit()
        print(
            f"created {len(documents)} repo documents; suppressed {removed_events.rowcount} created "
            f"events; removed {old_rows.rowcount} old 'repos' document"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split the 'repos' document into repo:<id> documents."
    )
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
    migrate(args.database_url, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
