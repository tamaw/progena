"""Registry document edits must trigger the live agents snapshot path."""

from __future__ import annotations

from app.backend.document_store import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore
from app.backend.state_reader import StateReader


def _reader(enabled: bool) -> tuple[StateReader, InMemoryDocumentStore]:
    store = InMemoryDocumentStore()
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "registry",
        {
            "agents": [
                {
                    "name": "greetings_agent",
                    "kind": "a2a_agent",
                    "enabled": enabled,
                    "status": "running",
                    "url": "http://localhost:8109",
                }
            ],
            "tools": [],
        },
    )
    return StateReader(document_store=store), store


def test_in_place_enabled_change_is_detected():
    reader, store = _reader(True)
    before = reader.get_snapshot()
    reader._last_snapshot = before

    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "registry",
        {
            "agents": [
                {
                    "name": "greetings_agent",
                    "kind": "a2a_agent",
                    "enabled": False,
                    "status": "running",
                    "url": "http://localhost:8109",
                }
            ],
            "tools": [],
        },
    )
    after = reader.get_snapshot()

    assert len(after.agents) == len(before.agents)
    assert after.agents[0].enabled is False
    assert after.registry_fingerprint != before.registry_fingerprint
    assert reader._state_changed(after)


def test_unchanged_registry_is_not_flagged():
    reader, _store = _reader(True)
    before = reader.get_snapshot()
    reader._last_snapshot = before

    after = reader.get_snapshot()

    assert after.registry_fingerprint == before.registry_fingerprint
    assert not reader._state_changed(after)
