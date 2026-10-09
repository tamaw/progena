from fastapi.testclient import TestClient

from app.backend.document_store import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore
from app.backend.main import app


def test_selector_metadata_does_not_expose_private_profile_fields(monkeypatch):
    store = InMemoryDocumentStore()
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "profile",
        {
            "general": {
                "provider": "azure_openai",
                "deployment": "greeting",
                "api_key": "must-not-be-exposed",
            }
        },
    )
    monkeypatch.setattr("app.backend.ui_routes.get_default_document_store", lambda: store)
    with TestClient(app) as client:
        response = client.get("/api/ui/model-profiles")
    assert response.status_code == 200
    assert response.json() == {"profiles": [{"name": "general", "deployment": "greeting"}]}


def test_invalid_profile_catalog_reports_error(monkeypatch):
    store = InMemoryDocumentStore()
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "profile",
        {"invalid": {"provider": "wrong", "deployment": "greeting"}},
    )
    monkeypatch.setattr("app.backend.ui_routes.get_default_document_store", lambda: store)
    with TestClient(app) as client:
        assert client.get("/api/ui/model-profiles").status_code == 422
