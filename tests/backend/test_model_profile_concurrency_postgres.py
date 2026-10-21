"""Concurrent model-profile mutations serialize instead of losing updates.

Opt-in on a scratch database (``PROGENITOR_TEST_DATABASE_URL``).
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from app.backend.document_store import PROGENITOR_STATE_PREFIX, PostgresDocumentStore
from app.backend.model_profile_routes import _mutate_profile
from app.progenitor.state.model_profiles import MODEL_PROFILES_DOC_KEY

DATABASE_URL = (os.getenv("PROGENITOR_TEST_DATABASE_URL") or "").strip() or None

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="requires PROGENITOR_TEST_DATABASE_URL (a scratch database)"
)


def _profile(deployment: str) -> dict:
    return {"provider_id": "foundry-1", "deployment": deployment}


@pytest.fixture
def store():
    assert DATABASE_URL is not None
    store = PostgresDocumentStore(DATABASE_URL)
    store.open()
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        MODEL_PROFILES_DOC_KEY,
        {"general": _profile("g1"), "fast": _profile("f1")},
    )
    yield store
    store.delete_document(PROGENITOR_STATE_PREFIX, MODEL_PROFILES_DOC_KEY)
    store.close()


def _slow(change, holding: threading.Event):
    def run(profile):
        holding.set()
        time.sleep(0.3)
        return change(profile)

    return run


def _interleave(store, first, second):
    """Run ``first`` (which holds its read open), then ``second`` against it."""

    holding = threading.Event()
    slow = threading.Thread(
        target=_mutate_profile, args=(store, *first[:1], _slow(first[1], holding))
    )
    slow.start()
    assert holding.wait(2)
    _mutate_profile(store, *second)
    slow.join(5)


def _stored(store) -> dict:
    document = store.get_document(PROGENITOR_STATE_PREFIX, MODEL_PROFILES_DOC_KEY)
    assert document is not None
    return document.payload


def test_concurrent_patches_of_different_profiles_both_persist(store):
    _interleave(
        store,
        ("general", lambda p: {**p, "deployment": "g2"}),
        ("fast", lambda p: {**p, "deployment": "f2"}),
    )
    stored = _stored(store)
    assert stored["general"]["deployment"] == "g2"
    assert stored["fast"]["deployment"] == "f2"


def test_patch_racing_a_delete_does_not_resurrect_the_profile(store):
    from fastapi import HTTPException

    _interleave(
        store,
        ("general", lambda p: {**p, "deployment": "g2"}),
        ("general", lambda _profile: None),
    )
    assert "general" not in _stored(store)
    with pytest.raises(HTTPException) as missing:
        _mutate_profile(store, "general", lambda p: {**p, "deployment": "g9"})
    assert missing.value.status_code == 404
