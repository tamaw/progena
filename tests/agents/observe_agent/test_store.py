"""Integration tests for the observe agent's Postgres-backed store.

These require a reachable Postgres server (the same one the runtime uses) and are
skipped otherwise. They run against a throwaway ``observe_test`` database that is
dropped on teardown.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from app.agents.observe_agent.config import DatabaseConfig, _swap_database_name
from app.agents.observe_agent.store import ObserveStore

_ADMIN_URL = os.getenv(
    "PROGENITOR_DATABASE_URL",
    "postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor",
)
_TEST_DB = "observe_test"


def _postgres_reachable() -> bool:
    try:
        psycopg.connect(_ADMIN_URL, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(), reason="Postgres is not reachable for observe store tests"
)


def _drop_test_db() -> None:
    with psycopg.connect(_ADMIN_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (_TEST_DB,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{_TEST_DB}"')


@pytest.fixture
async def store():
    _drop_test_db()
    config = DatabaseConfig(
        admin_url=_ADMIN_URL,
        database_url=_swap_database_name(_ADMIN_URL, _TEST_DB),
        db_name=_TEST_DB,
        setup=True,
    )
    observe_store = ObserveStore(config)
    await observe_store.open()
    try:
        yield observe_store
    finally:
        await observe_store.close()
        _drop_test_db()


async def test_seed_and_read_system_prompt(store):
    assert await store.get_system_prompt() is None
    await store.seed_system_prompt("be a helpful monitor")
    assert await store.get_system_prompt() == "be a helpful monitor"
    # Seeding again does not overwrite an existing prompt.
    await store.seed_system_prompt("a different prompt")
    assert await store.get_system_prompt() == "be a helpful monitor"


async def test_seed_skills_is_idempotent(store):
    defaults = [
        {"id": "a", "name": "A", "description": "first", "tags": ["x"]},
        {"id": "b", "name": "B", "description": "second", "tags": ["y"]},
    ]
    await store.seed_skills(defaults)
    await store.seed_skills(defaults)
    skills = await store.list_skills()
    assert [skill["id"] for skill in skills] == ["a", "b"]


async def test_record_and_list_task_runs(store):
    await store.record_task_run(
        {
            "task_id": "t1",
            "context_id": "c1",
            "status": "success",
            "timestamp_utc": "2026-01-01T00:00:00Z",
        }
    )
    await store.record_task_run(
        {
            "task_id": "t2",
            "context_id": "c1",
            "status": "failure",
            "timestamp_utc": "2026-01-02T00:00:00Z",
        }
    )
    runs = await store.list_task_runs(limit=10)
    assert [run["task_id"] for run in runs] == ["t1", "t2"]
    assert runs[-1]["status"] == "failure"
