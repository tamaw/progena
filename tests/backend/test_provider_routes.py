"""Focused acknowledgement and secret-safety tests for provider Settings routes."""

from __future__ import annotations

from typing import Any

import pytest
from ack_helpers import assert_acknowledged
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backend.document_store import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore
from app.backend.provider_routes import create_provider_router
from app.progenitor.providers import (
    _normalize_foundry_base_url,
    _normalize_legacy_foundry_base_url,
)


class FakeProviderRepository:
    def __init__(self) -> None:
        self.providers = [
            {
                "id": "foundry-1",
                "name": "Foundry",
                "kind": "foundry",
                "base_url": "https://example.openai.azure.com/openai/v1",
                "default_model": "model",
                "secret_source": "environment",
                "env_var": "AZURE_OPENAI_API_KEY",
                "has_api_key": False,
                "is_default": True,
                "revision": 1,
            }
        ]
        self.deleted: list[str] = []

    def list_public(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.providers]

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("name") == "bad":
            raise ValueError("name is invalid")
        return self.providers[0]

    def reject_duplicate(self, data: dict[str, Any]) -> dict[str, Any]:
        from psycopg.errors import UniqueViolation

        raise UniqueViolation("sensitive database details must not leave the API")

    def update(self, provider_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if provider_id != "foundry-1":
            raise LookupError("provider not found")
        return self.providers[0]

    def delete(self, provider_id: str) -> None:
        self.deleted.append(provider_id)

    def resolve(
        self, provider_id: str | None = None, model_override: str | None = None
    ) -> dict[str, Any]:
        if provider_id != "foundry-1":
            raise LookupError("provider not found")
        raise ValueError("provider API key is not configured")


def _app(store: InMemoryDocumentStore, repository: FakeProviderRepository):
    events: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    app = FastAPI()

    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID", "provider-test")
        return await call_next(request)

    def broadcast(kind: str, payload: dict[str, Any], **kwargs: Any) -> None:
        events.append((kind, payload, kwargs))

    app.include_router(create_provider_router(lambda: repository, lambda: store, broadcast))
    return app, events


def test_provider_input_error_is_422_without_event():
    app, events = _app(InMemoryDocumentStore(), FakeProviderRepository())
    with TestClient(app) as client:
        response = client.post("/providers", json={"name": "bad"})
    assert response.status_code == 422
    assert events == []


def test_duplicate_provider_name_is_a_conflict_without_database_details():
    repository = FakeProviderRepository()
    repository.create = repository.reject_duplicate
    app, events = _app(InMemoryDocumentStore(), repository)
    with TestClient(app) as client:
        response = client.post("/providers", json={"name": "Foundry"})
    assert response.status_code == 409
    assert response.json()["detail"] == "A provider with that name already exists"
    assert events == []


def test_delete_rejects_profile_connection_reference():
    store = InMemoryDocumentStore()
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "profile",
        {"general": {"provider_id": "foundry-1", "deployment": "model"}},
    )
    repository = FakeProviderRepository()
    app, events = _app(store, repository)
    with TestClient(app) as client:
        response = client.delete("/providers/foundry-1")
    assert response.status_code == 409
    assert repository.deleted == []
    assert events == []


def test_interactive_foundry_url_rejects_responses_endpoint_but_seed_normalizes_it():
    legacy = "https://sample.openai.azure.com/openai/v1/responses?api-version=preview"
    assert _normalize_legacy_foundry_base_url(legacy) == "https://sample.openai.azure.com/openai/v1"
    with pytest.raises(ValueError):
        _normalize_foundry_base_url(legacy)
    with pytest.raises(ValueError):
        _normalize_legacy_foundry_base_url(
            "https://sample.openai.azure.com/openai/v1/responses?api-version=preview#fragment"
        )


def test_connection_test_reports_unconfigured_without_secret_detail():
    app, events = _app(InMemoryDocumentStore(), FakeProviderRepository())
    with TestClient(app) as client:
        response = client.post("/providers/foundry-1/test")
    assert response.status_code == 202
    assert_acknowledged(response, ["providers.tested"])
    assert events[-1][0] == "providers.tested"
    assert events[-1][1] == {
        "provider_id": "foundry-1",
        "ok": False,
        "error": "Provider is not configured",
    }
