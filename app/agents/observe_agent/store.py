"""Postgres-backed store for the observe agent.

The observe agent keeps its own data (system prompt, skills catalog, task run
history) and its LangGraph conversation checkpoints in a dedicated database on
the same Postgres server as the Progenitor runtime. This wraps LangGraph's
native checkpointer + store (see ``app.progenitor.database``) with a small
semantic API, mirroring ``LangGraphRuntimeStore``.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.progenitor.database import ensure_database_exists, open_langgraph_persistence

from .config import DatabaseConfig

logger = logging.getLogger(__name__)

PROMPTS_NAMESPACE = ("observe", "prompts")
SKILLS_NAMESPACE = ("observe", "skills")
TASKS_NAMESPACE = ("observe", "tasks")

SYSTEM_PROMPT_KEY = "system"


def _item_value(item: Any) -> dict[str, Any]:
    value = getattr(item, "value", item)
    return value if isinstance(value, dict) else {}


class ObserveStore:
    """Async Postgres client for observe-agent data and checkpoints."""

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._stack = contextlib.AsyncExitStack()
        self._store: Any | None = None
        self.checkpointer: Any | None = None

    async def open(self) -> None:
        """Create the database if needed and open LangGraph persistence."""
        if self._config.admin_url and self._config.db_name:
            ensure_database_exists(self._config.admin_url, self._config.db_name)
        persistence = await self._stack.enter_async_context(
            open_langgraph_persistence(
                self._config.database_url,
                setup=self._config.setup,
                pool_min_size=self._config.pool_min_size,
                pool_max_size=self._config.pool_max_size,
            )
        )
        self.checkpointer = persistence.checkpointer
        self._store = persistence.store
        if self._store is None:
            raise RuntimeError("LangGraph Postgres store is required for the observe agent")
        logger.info("Observe store connected to Postgres database %r", self._config.db_name)

    async def close(self) -> None:
        await self._stack.aclose()
        self._store = None
        self.checkpointer = None

    @property
    def store(self) -> Any:
        if self._store is None:
            raise RuntimeError("ObserveStore is not open")
        return self._store

    # -- system prompt -------------------------------------------------------

    async def get_system_prompt(self) -> str | None:
        item = await self.store.aget(PROMPTS_NAMESPACE, SYSTEM_PROMPT_KEY)
        if item is None:
            return None
        body = _item_value(item).get("body")
        return body if isinstance(body, str) else None

    async def set_system_prompt(self, body: str) -> None:
        await self.store.aput(PROMPTS_NAMESPACE, SYSTEM_PROMPT_KEY, {"body": body})

    async def seed_system_prompt(self, default: str) -> None:
        """Write the default prompt only when none is stored yet."""
        if await self.get_system_prompt() is None and default:
            await self.set_system_prompt(default)

    # -- skills --------------------------------------------------------------

    async def list_skills(self) -> list[dict[str, Any]]:
        items = await self.store.asearch(SKILLS_NAMESPACE, limit=100)
        skills = [_item_value(item) for item in items]
        skills = [skill for skill in skills if skill.get("id")]
        skills.sort(key=lambda skill: str(skill.get("order", "")) + str(skill.get("id", "")))
        return skills

    async def seed_skills(self, defaults: list[dict[str, Any]]) -> None:
        """Insert default skills that are not already stored. Idempotent."""
        for index, skill in enumerate(defaults):
            skill_id = str(skill.get("id") or "").strip()
            if not skill_id:
                continue
            existing = await self.store.aget(SKILLS_NAMESPACE, skill_id)
            if existing is None:
                payload = {**skill, "id": skill_id, "order": index}
                await self.store.aput(SKILLS_NAMESPACE, skill_id, payload)

    # -- task run history ----------------------------------------------------

    async def record_task_run(self, record: dict[str, Any]) -> None:
        timestamp = str(record.get("timestamp_utc") or datetime.now(UTC).isoformat())
        key = f"{timestamp}_{uuid.uuid4().hex}"
        await self.store.aput(TASKS_NAMESPACE, key, dict(record))

    async def list_task_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        items = await self.store.asearch(TASKS_NAMESPACE, limit=max(1, limit))
        rows = [_item_value(item) for item in items]
        rows.sort(key=lambda row: str(row.get("timestamp_utc", "")))
        return rows[-limit:] if limit > 0 else rows
