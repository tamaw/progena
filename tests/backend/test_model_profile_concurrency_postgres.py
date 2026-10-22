"""Concurrent model-profile mutations serialize instead of losing updates."""

from __future__ import annotations

import threading
import time

import pytest

from src.backend.settings.model_profiles import _mutate_profile
from src.backend.storage.documents import PROGENITOR_STATE_PREFIX, PostgresDocumentStore
from src.progenitor.settings.model_profiles import MODEL_PROFILES_DOC_KEY


def _profile(deployment: str) -> dict:
    return {"provider_id": "foundry-1", "deployment": deployment}


@pytest.fixture
def store(postgres_url: str):
    store = PostgresDocumentStore(postgres_url)
    store.open()
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        MODEL_PROFILES_DOC_KEY,
        {"general": _profile("g1"), "fast": _profile("f1")},
    )
    yield store
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
    errors: list[Exception] = []

    def mutate_first() -> None:
        try:
            _mutate_profile(store, *first[:1], _slow(first[1], holding))
        except Exception as exc:
            errors.append(exc)

    slow = threading.Thread(target=mutate_first)
    slow.start()
    first_holding = holding.wait(timeout=2)
    try:
        if first_holding:
            _mutate_profile(store, *second)
    finally:
        slow.join(timeout=5)
        assert not slow.is_alive()
    if errors:
        raise errors[0]
    assert first_holding


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
