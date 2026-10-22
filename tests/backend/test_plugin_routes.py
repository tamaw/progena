"""Tests for the installed plugins routes (/api/ui/plugins)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from ack_helpers import assert_acknowledged
from fastapi.testclient import TestClient

from src.backend.main import app
from src.backend.storage.documents import (
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
    set_default_document_store,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    from src.backend.plugins.marketplaces import _CATALOG_CACHE

    _CATALOG_CACHE.clear()


@pytest.fixture
def document_store():
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    yield store
    set_default_document_store(InMemoryDocumentStore())


class _StreamCapture:
    """Subscribe to the UI event stream for the duration of a request."""

    def __init__(self):
        from src.backend.events import stream as events_stream

        self.events_stream = events_stream
        self.queue = asyncio.Queue()

    def __enter__(self):
        self.events_stream._ui_stream_subscribers[self.queue] = None
        return self

    def __exit__(self, *exc_info):
        self.events_stream._ui_stream_subscribers.pop(self.queue, None)
        return False

    def event_types(self):
        events = []
        while not self.queue.empty():
            event = self.queue.get_nowait()
            events.append(event["envelope"])
        return events


class TestPluginRoutes:
    def test_get_returns_ack_and_broadcasts_snapshot(self, client, document_store):
        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            "plugins",
            {
                "plugins": {
                    "acme/formatter": {
                        "source": {"source": "github", "repo": "acme/formatter"},
                        "version": "1.0.0",
                        "status": "installed",
                    }
                }
            },
        )
        with _StreamCapture() as capture:
            response = client.get("/api/ui/plugins")

        assert_acknowledged(response, ["plugins.snapshot"])
        envelopes = capture.event_types()
        assert [e.type for e in envelopes] == ["plugins.snapshot"]
        assert "acme/formatter" in envelopes[0].payload.get("plugins", {})

    def test_get_empty_defaults(self, client, document_store):
        with _StreamCapture() as capture:
            response = client.get("/api/ui/plugins")

        assert_acknowledged(response, ["plugins.snapshot"])
        envelopes = capture.event_types()
        assert envelopes[0].payload.get("plugins") == {}

    def test_post_installs_and_broadcasts(self, client, document_store, monkeypatch):
        # Seed marketplace source so the backend can resolve the catalog.
        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            "marketplaces",
            {
                "marketplaces": {
                    "acme": {
                        "source": "https://github.com/acme/plugins",
                        "added_at": "2026-07-29T10:00:00Z",
                    }
                }
            },
        )

        fake_catalog = type(
            "MarketplaceCatalog",
            (),
            {
                "name": "acme",
                "owner": {"name": "Acme Corp"},
                "plugins": [
                    {
                        "name": "formatter",
                        "source": {"source": "github", "repo": "acme/formatter"},
                        "version": "1.0.0",
                    }
                ],
                "description": None,
                "version": None,
                "metadata": {},
            },
        )()

        monkeypatch.setattr(
            "src.backend.plugins.routes.fetch_marketplace",
            AsyncMock(return_value=fake_catalog),
        )

        with _StreamCapture() as capture:
            response = client.post(
                "/api/ui/plugins",
                json={"marketplace": "acme", "plugin": "formatter"},
            )

        assert_acknowledged(response, ["plugins.snapshot"])
        stored = document_store.get_document(PROGENITOR_STATE_PREFIX, "plugins")
        assert "acme/formatter" in stored.payload["plugins"]
        assert stored.payload["plugins"]["acme/formatter"]["status"] == "pending"
        assert stored.payload["plugins"]["acme/formatter"]["source"]["repo"] == "acme/formatter"

        envelopes = capture.event_types()
        assert [e.type for e in envelopes] == ["plugins.snapshot"]

    def test_post_resolves_relative_source_to_absolute(self, client, document_store, monkeypatch):
        """A plugin whose marketplace entry uses a relative ``source`` (e.g.
        ``"./"`` -- what real Claude Code marketplaces like
        anthropics/skills actually ship) must be resolved to an absolute
        git-subdir source before being stored. A raw relative path is
        meaningless once persisted in isolation: the reconciler never has
        the marketplace archive to resolve it against, and would fail
        every single install (found live against a real marketplace)."""
        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            "marketplaces",
            {
                "marketplaces": {
                    "acme": {
                        "source": "acme/plugins@main",
                        "added_at": "2026-07-29T10:00:00Z",
                    }
                }
            },
        )

        fake_catalog = type(
            "MarketplaceCatalog",
            (),
            {
                "name": "acme",
                "owner": {"name": "Acme Corp"},
                "plugins": [
                    {"name": "everything", "source": "./", "version": None},
                ],
                "description": None,
                "version": None,
                "metadata": {},
            },
        )()

        monkeypatch.setattr(
            "src.backend.plugins.routes.fetch_marketplace",
            AsyncMock(return_value=fake_catalog),
        )

        response = client.post(
            "/api/ui/plugins",
            json={"marketplace": "acme", "plugin": "everything"},
        )

        assert_acknowledged(response, ["plugins.snapshot"])
        stored = document_store.get_document(PROGENITOR_STATE_PREFIX, "plugins")
        source = stored.payload["plugins"]["acme/everything"]["source"]
        assert source == {"source": "git-subdir", "url": "acme/plugins", "path": "", "ref": "main"}

    def test_post_rejects_invalid_plugin_document(self, client, document_store, monkeypatch):
        # Seed a marketplace.
        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            "marketplaces",
            {
                "marketplaces": {
                    "acme": {
                        "source": "https://github.com/acme/plugins",
                        "added_at": "2026-07-29T10:00:00Z",
                    }
                }
            },
        )

        fake_catalog = type(
            "MarketplaceCatalog",
            (),
            {
                "name": "acme",
                "owner": {"name": "Acme Corp"},
                "plugins": [
                    # Missing "source" — the marketplace entry itself is malformed,
                    # but the POST will still try to write it and validation should catch it.
                    {"name": "bad", "version": "1.0.0"}
                ],
                "description": None,
                "version": None,
                "metadata": {},
            },
        )()

        monkeypatch.setattr(
            "src.backend.plugins.routes.fetch_marketplace",
            AsyncMock(return_value=fake_catalog),
        )

        # Since "source" is missing, the resulting entry will fail plugin_entry schema validation.
        # However, our new_entry builder strips None values, so it won't include "source" at all,
        # and validate_plugins_document will raise because "source" is required.
        response = client.post(
            "/api/ui/plugins",
            json={"marketplace": "acme", "plugin": "bad"},
        )
        # Wait, the POST currently uses plugin_entry.get("source") which is None, and then strips it.
        # So the new_entry won't have "source", and _save_plugins_document will fail validation.
        assert response.status_code == 400
        # The document must remain unchanged.
        stored = document_store.get_document(PROGENITOR_STATE_PREFIX, "plugins")
        assert stored is None or stored.payload.get("plugins") == {}

    def test_post_rejects_missing_marketplace_or_plugin(self, client, document_store):
        response = client.post("/api/ui/plugins", json={"marketplace": "acme"})
        assert response.status_code == 400

        response = client.post("/api/ui/plugins", json={"plugin": "formatter"})
        assert response.status_code == 400

    def test_post_rejects_unknown_plugin(self, client, document_store, monkeypatch):
        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            "marketplaces",
            {
                "marketplaces": {
                    "acme": {
                        "source": "https://github.com/acme/plugins",
                        "added_at": "2026-07-29T10:00:00Z",
                    }
                }
            },
        )

        fake_catalog = type(
            "MarketplaceCatalog",
            (),
            {
                "name": "acme",
                "owner": {"name": "Acme Corp"},
                "plugins": [],
                "description": None,
                "version": None,
                "metadata": {},
            },
        )()

        monkeypatch.setattr(
            "src.backend.plugins.routes.fetch_marketplace",
            AsyncMock(return_value=fake_catalog),
        )

        response = client.post(
            "/api/ui/plugins",
            json={"marketplace": "acme", "plugin": "unknown"},
        )
        assert response.status_code == 404

    def test_delete_removes_plugin_and_broadcasts(self, client, document_store):
        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            "plugins",
            {
                "plugins": {
                    "acme/formatter": {
                        "source": {"source": "github", "repo": "acme/formatter"},
                        "version": "1.0.0",
                        "status": "installed",
                    },
                    "beta/linter": {
                        "source": {"source": "github", "repo": "beta/linter"},
                        "version": "2.0.0",
                        "status": "installed",
                    },
                }
            },
        )

        with _StreamCapture() as capture:
            response = client.delete("/api/ui/plugins/acme/formatter")

        assert_acknowledged(response, ["plugins.snapshot"])
        stored = document_store.get_document(PROGENITOR_STATE_PREFIX, "plugins")
        assert "acme/formatter" not in stored.payload["plugins"]
        assert "beta/linter" in stored.payload["plugins"]

        envelopes = capture.event_types()
        assert [e.type for e in envelopes] == ["plugins.snapshot"]

    def test_delete_unknown_plugin_returns_404(self, client, document_store):
        response = client.delete("/api/ui/plugins/acme/formatter")
        assert response.status_code == 404
