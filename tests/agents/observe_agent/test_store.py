"""Integration tests for the observe agent's Postgres-backed store."""

from __future__ import annotations

import pytest

from app.agents.observe_agent.config import DatabaseConfig
from app.agents.observe_agent.store import ObserveStore


@pytest.fixture
async def store(postgres_url):
    config = DatabaseConfig(
        admin_url="",
        database_url=postgres_url,
        db_name="",
        setup=True,
    )
    observe_store = ObserveStore(config)
    await observe_store.open()
    try:
        yield observe_store
    finally:
        await observe_store.close()


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
