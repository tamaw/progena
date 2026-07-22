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


async def _open_saver(
    database_url: str,
    *,
    setup: bool = True,
) -> Any:
    """Open AsyncPostgresSaver with manual connection lifetime."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    conn = await AsyncConnection.connect(
        database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row
    )
    try:
        saver = AsyncPostgresSaver(conn=conn)
        if setup:
            await saver.setup()
        return saver, conn
    except BaseException:
        await conn.close()
        raise


async def _open_store(
    database_url: str,
    *,
    setup: bool = True,
    pool_min_size: int | None = None,
    pool_max_size: int | None = None,
) -> Any:
    """Open AsyncPostgresStore with manual connection lifetime."""
    from langgraph.store.postgres.aio import AsyncPostgresStore
    from langgraph.store.postgres.base import PoolConfig
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool_config = None
    if pool_min_size is not None or pool_max_size is not None:
        pool_config = PoolConfig(
            min_size=pool_min_size or 1,
            max_size=pool_max_size,
        )

    if pool_config is not None:
        pc = pool_config.copy()
        pool = AsyncConnectionPool(
            database_url,
            min_size=pc.pop("min_size", 1),
            max_size=pc.pop("max_size", None),
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
                **(pc.pop("kwargs", None) or {}),
            },
            **pc,
        )
        try:
            store = AsyncPostgresStore(conn=pool)
            if setup:
                await store.setup()
            return store, pool
        except BaseException:
            await pool.close()
            raise
    else:
        conn = await AsyncConnection.connect(
            database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row
        )
        try:
            store = AsyncPostgresStore(conn=conn)
            if setup:
                await store.setup()
            return store, conn
        except BaseException:
            await conn.close()
            raise


@asynccontextmanager
async def open_langgraph_persistence(
    database_url: str,
    *,
    setup: bool = True,
    pool_min_size: int | None = None,
    pool_max_size: int | None = None,
) -> AsyncIterator[LangGraphPersistence]:
    """Open native LangGraph Postgres checkpointer/store resources."""
    store_cls: Any | None
    try:
        from langgraph.store.postgres.aio import AsyncPostgresStore

        store_cls = AsyncPostgresStore
    except ImportError:  # pragma: no cover - store package availability varies by LangGraph version
        store_cls = None

    checkpointer, cp_conn = await _open_saver(database_url, setup=setup)
    store = None
    st_conn = None
    try:
        if store_cls is not None:
            store, st_conn = await _open_store(
                database_url,
                setup=setup,
                pool_min_size=pool_min_size,
                pool_max_size=pool_max_size,
            )
        yield LangGraphPersistence(checkpointer=checkpointer, store=store)
    finally:
        if st_conn is not None:
            await st_conn.close()
        await cp_conn.close()
