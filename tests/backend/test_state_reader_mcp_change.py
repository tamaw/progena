"""MCP config change detection in StateReader.

``tools`` carries one entry per MCP server, so adding/removing a server is
already caught by the count check. These tests cover the gap: in-place edits
(enable/disable, url/description) that leave the server count unchanged but must
still trigger a live UI push.
"""

from __future__ import annotations

from src.backend.monitoring.state import StateReader
from src.backend.storage.documents import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore


def _reader(servers: dict) -> tuple[StateReader, InMemoryDocumentStore]:
    store = InMemoryDocumentStore()
    store.save_document(PROGENITOR_STATE_PREFIX, "mcp", {"mcpServers": servers})
    reader = StateReader(
        document_store=store,
    )
    return reader, store


def _resnapshot(reader: StateReader) -> object:
    return reader.get_snapshot()


def test_store_change_threads_key_to_watchers():
    reader, _store = _reader({"jina": {"url": "https://x/mcp", "transport": "http"}})
    seen: list[tuple[object, object]] = []
    reader.add_watcher(lambda snapshot, key: seen.append((snapshot, key)))

    # NOTIFY payload carries the changed store key; it must reach the watcher.
    reader._on_store_change('{"prefix": "progenitor.state", "key": "mcp"}')

    assert len(seen) == 1
    assert seen[0][1] == "mcp"

    # A full resync (no payload) threads None.
    reader._last_snapshot = None
    reader._on_store_change("")
    assert seen[-1][1] is None


def test_toggling_enabled_is_detected():
    reader, store = _reader(
        {"jina": {"url": "https://x/mcp", "transport": "http", "enabled": True}},
    )
    snap1 = reader.get_snapshot()
    reader._last_snapshot = snap1

    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "mcp",
        {"mcpServers": {"jina": {"url": "https://x/mcp", "transport": "http", "enabled": False}}},
    )
    snap2 = _resnapshot(reader)

    # Server count (and thus tool count) is unchanged, but the edit is detected.
    assert len(snap2.tools) == len(snap1.tools)
    assert snap2.mcp_fingerprint != snap1.mcp_fingerprint
    assert reader._state_changed(snap2)


def test_editing_url_is_detected():
    reader, store = _reader(
        {"jina": {"url": "https://old/mcp", "transport": "http"}},
    )
    snap1 = reader.get_snapshot()
    reader._last_snapshot = snap1

    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "mcp",
        {"mcpServers": {"jina": {"url": "https://new/mcp", "transport": "http"}}},
    )
    snap2 = _resnapshot(reader)

    assert len(snap2.tools) == len(snap1.tools)
    assert reader._state_changed(snap2)


def test_unchanged_mcp_config_is_not_flagged():
    reader, _store = _reader(
        {"jina": {"url": "https://x/mcp", "transport": "http"}},
    )
    snap1 = reader.get_snapshot()
    reader._last_snapshot = snap1

    snap2 = _resnapshot(reader)

    assert snap2.mcp_fingerprint == snap1.mcp_fingerprint
    assert not reader._state_changed(snap2)
