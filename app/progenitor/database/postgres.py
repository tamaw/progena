"""Shared Postgres administration helpers."""

from __future__ import annotations

import logging

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
