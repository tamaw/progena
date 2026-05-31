"""LangGraph-native Postgres persistence lifecycle helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LangGraphPersistence:
    """Native LangGraph persistence objects kept alive for graph lifetime."""

    checkpointer: Any
    store: Any | None = None


@asynccontextmanager
async def open_langgraph_persistence(
    database_url: str,
    *,
    setup: bool = True,
    pool_min_size: int | None = None,
    pool_max_size: int | None = None,
) -> AsyncIterator[LangGraphPersistence]:
    """Open native LangGraph Postgres checkpointer/store resources."""
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:  # pragma: no cover - exercised when optional dep is absent
        raise RuntimeError(
            "Postgres checkpointing requires langgraph-checkpoint-postgres. "
            "Install project dependencies before enabling PROGENITOR_DATABASE_URL."
        ) from exc

    store_cls: Any | None
    try:
        from langgraph.store.postgres.aio import AsyncPostgresStore
        from langgraph.store.postgres.base import PoolConfig

        store_cls = AsyncPostgresStore
        pool_config_cls = PoolConfig
    except ImportError:  # pragma: no cover - store package availability varies by LangGraph version
        store_cls = None
        pool_config_cls = None

    async with AsyncPostgresSaver.from_conn_string(database_url) as checkpointer:
        if setup:
            await checkpointer.setup()

        if store_cls is None:
            yield LangGraphPersistence(checkpointer=checkpointer)
            return

        pool_config = None
        if pool_config_cls is not None and (pool_min_size is not None or pool_max_size is not None):
            pool_config = pool_config_cls(
                min_size=pool_min_size or 1,
                max_size=pool_max_size,
            )

        async with store_cls.from_conn_string(database_url, pool_config=pool_config) as store:
            if setup:
                await store.setup()
            yield LangGraphPersistence(checkpointer=checkpointer, store=store)
