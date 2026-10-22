"""Postgres capture and immutability coverage for module revisions."""

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from src.backend.storage.documents import PROGENITOR_STATE_PREFIX, PostgresDocumentStore
from src.progenitor.modules.contracts import module_file_key

DATABASE_URL = (os.getenv("PROGENITOR_TEST_DATABASE_URL") or "").strip() or None
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="requires PROGENITOR_TEST_DATABASE_URL (a scratch database)"
)


@pytest.fixture
def store():
    assert DATABASE_URL is not None
    schema = f"module_revisions_{uuid4().hex}"
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped_url = psycopg.conninfo.make_conninfo(DATABASE_URL, options=f"-csearch_path={schema}")
    value = PostgresDocumentStore(scoped_url)
    value.open()
    try:
        yield value, scoped_url
    finally:
        value.close()
        with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_module_revision_capture_skips_unchanged_writes_and_is_immutable(store):
    value, scoped_url = store
    key = module_file_key("demo", "index.mjs")
    value.save_document(PROGENITOR_STATE_PREFIX, key, {"content": "one"})
    value.save_document(PROGENITOR_STATE_PREFIX, key, {"content": "one"})
    value.save_document(PROGENITOR_STATE_PREFIX, key, {"content": "two"})
    value.delete_document(PROGENITOR_STATE_PREFIX, key)

    revisions = value.list_module_revisions("demo")
    assert [(row.op, row.content) for row in revisions] == [
        ("delete", None),
        ("write", "two"),
        ("write", "one"),
    ]
    with (
        psycopg.connect(scoped_url, autocommit=True) as conn,
        pytest.raises(psycopg.errors.RaiseException, match="immutable"),
    ):
        conn.execute("DELETE FROM module_revisions")
