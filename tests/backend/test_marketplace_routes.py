"""Tests for the marketplace sources routes (/api/ui/marketplaces)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from ack_helpers import assert_acknowledged
from fastapi.testclient import TestClient

from app.backend.main import app
from app.backend.storage.documents import (
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
    set_default_document_store,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    from app.backend.plugins.marketplaces import _CATALOG_CACHE

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
        from app.backend.events import stream as events_stream

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


class TestMarketplaceRoutes:
    def test_get_returns_ack_and_broadcasts_snapshot(self, client, document_store):
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
        with _StreamCapture() as capture:
            response = client.get("/api/ui/marketplaces")

        assert_acknowledged(response, ["marketplaces.snapshot"])
        envelopes = capture.event_types()
        assert [e.type for e in envelopes] == ["marketplaces.snapshot"]
        assert (
            envelopes[0].payload["marketplaces"]["acme"]["source"]
            == "https://github.com/acme/plugins"
        )

    def test_get_empty_defaults(self, client, document_store):
        with _StreamCapture() as capture:
            response = client.get("/api/ui/marketplaces")

        assert_acknowledged(response, ["marketplaces.snapshot"])
        envelopes = capture.event_types()
        assert envelopes[0].payload["marketplaces"] == {}

    def test_put_validates_persists_and_broadcasts(self, client, document_store):
        payload = {
            "marketplaces": {
                "acme": {"source": "https://github.com/acme/plugins"},
                "beta": {"source": "https://github.com/beta/plugins"},
            }
        }
        with _StreamCapture() as capture:
            response = client.put("/api/ui/marketplaces", json=payload)

        assert_acknowledged(response, ["marketplaces.snapshot"])
        stored = document_store.get_document(PROGENITOR_STATE_PREFIX, "marketplaces")
        assert len(stored.payload["marketplaces"]) == 2
        assert stored.payload["marketplaces"]["acme"]["source"] == "https://github.com/acme/plugins"

        envelopes = capture.event_types()
        assert [e.type for e in envelopes] == ["marketplaces.snapshot"]

    def test_put_rejects_bad_name(self, client, document_store):
        payload = {"marketplaces": {"Bad Name": {"source": "https://github.com/acme/plugins"}}}
        response = client.put("/api/ui/marketplaces", json=payload)
        assert response.status_code == 400
        assert "kebab-case" in response.json()["detail"].lower()

    def test_put_rejects_missing_source(self, client, document_store):
        payload = {"marketplaces": {"acme": {}}}
        response = client.put("/api/ui/marketplaces", json=payload)
        assert response.status_code == 400
        assert "source" in response.json()["detail"].lower()

    def test_browse_returns_ack_and_broadcasts_catalog(self, client, document_store, monkeypatch):
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
                "plugins": [{"name": "formatter", "source": "./formatter", "version": "1.0.0"}],
                "description": None,
                "version": None,
                "metadata": {},
            },
        )()

        monkeypatch.setattr(
            "app.backend.plugins.marketplace_routes.fetch_marketplace",
            AsyncMock(return_value=fake_catalog),
        )

        with _StreamCapture() as capture:
            response = client.get("/api/ui/marketplaces/acme/plugins")

        assert_acknowledged(response, ["plugin.catalog.snapshot"])
        envelopes = capture.event_types()
        assert [e.type for e in envelopes] == ["plugin.catalog.snapshot"]
        assert envelopes[0].payload["name"] == "acme"
        assert envelopes[0].payload["catalog"]["plugins"][0]["name"] == "formatter"

    def test_browse_unknown_marketplace_returns_404(self, client, document_store):
        response = client.get("/api/ui/marketplaces/unknown/plugins")
        assert response.status_code == 404

    def test_browse_fetch_error_returns_400(self, client, document_store, monkeypatch):
        from app.progenitor.plugins.sources import PluginFetchError

        document_store.save_document(
            PROGENITOR_STATE_PREFIX,
            "marketplaces",
            {
                "marketplaces": {
                    "bad": {
                        "source": "https://bad.example",
                        "added_at": "2026-07-29T10:00:00Z",
                    }
                }
            },
        )
        monkeypatch.setattr(
            "app.backend.plugins.marketplace_routes.fetch_marketplace",
            AsyncMock(side_effect=PluginFetchError("network error")),
        )

        response = client.get("/api/ui/marketplaces/bad/plugins")
        assert response.status_code == 400
        assert "network error" in response.json()["detail"]
