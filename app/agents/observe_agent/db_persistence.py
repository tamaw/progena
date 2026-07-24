"""Local Postgres/LangGraph persistence bootstrap helpers for observe_agent.

Self-contained copy of the database lifecycle helpers: observe_agent does not
depend on app.progenitor.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

import psycopg
from psycopg import sql

logger = logging.getLogger(__name__)


def ensure_database_exists(admin_url: str, db_name: str) -> None:
    """Create ``db_name`` on the shared Postgres server if it does not exist.

    ``admin_url`` must point at an existing, connectable database on the same
    server. ``CREATE DATABASE`` cannot run inside a transaction, so the admin
    connection uses autocommit.
    """
    if not admin_url or not db_name:
        raise ValueError("admin_url and db_name are required to ensure a database")
    with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        if cur.fetchone() is None:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
            logger.info("Created database %r", db_name)


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
    from psycopg.rows import DictRow, dict_row

    conn = cast(
        AsyncConnection[DictRow],
        await AsyncConnection.connect(
            database_url,
            autocommit=True,
            prepare_threshold=0,
            row_factory=cast(Any, dict_row),
        ),
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
    from psycopg.rows import DictRow, dict_row
    from psycopg_pool import AsyncConnectionPool

    pool_config = None
    if pool_min_size is not None or pool_max_size is not None:
        pool_config = PoolConfig(
            min_size=pool_min_size or 1,
            max_size=pool_max_size,
        )

    if pool_config is not None:
        pc = cast(dict[str, Any], pool_config.copy())
        pool = cast(
            AsyncConnectionPool[AsyncConnection[DictRow]],
            AsyncConnectionPool(
                database_url,
                min_size=pc.pop("min_size", 1),
                max_size=pc.pop("max_size", None),
                open=False,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                    **(pc.pop("kwargs", None) or {}),
                },
                **cast(dict, pc),
            ),
        )
        await pool.open()
        try:
            store = AsyncPostgresStore(conn=pool)
            if setup:
                await store.setup()
            return store, pool
        except BaseException:
            await pool.close()
            raise
    else:
        conn = cast(
            AsyncConnection[DictRow],
            await AsyncConnection.connect(
                database_url,
                autocommit=True,
                prepare_threshold=0,
                row_factory=cast(Any, dict_row),
            ),
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
