"""Tests for the model profile backend routes.

These use a fake in-memory store so they do not require Postgres.
"""

from __future__ import annotations

from typing import Any

import pytest
from ack_helpers import assert_acknowledged
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.backend.document_store import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore
from app.backend.model_profile_routes import create_model_profile_router


class FakeProviderRepository:
    def __init__(self, providers: list[dict[str, Any]]) -> None:
        self._providers = providers

    def list_public(self) -> list[dict[str, Any]]:
        return self._providers


def _make_app(
    store: InMemoryDocumentStore,
    provider_repository: FakeProviderRepository | None = None,
) -> tuple[FastAPI, list[tuple[str, dict[str, Any], dict[str, Any]]]]:
    events: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def broadcast(kind: str, payload: dict[str, Any], **kw: Any) -> Any:
        events.append((kind, payload, kw))
        return None

    app = FastAPI()

    @app.middleware("http")
    async def correlate_request(request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID", "profile-test")
        return await call_next(request)

    app.include_router(
        create_model_profile_router(
            lambda: store,
            broadcast,
            (lambda: provider_repository) if provider_repository is not None else None,
        )
    )
    return app, events


class TestPublicLeakageGuard:
    def test_canonical_fields_are_exposed(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {
                "general": {
                    "provider_id": "foundry-1",
                    "deployment": "greeting",
                    "reasoning_enabled": True,
                    "reasoning_effort": "medium",
                    "reasoning_summary": "concise",
                }
            },
        )
        app, events = _make_app(store)
        with TestClient(app) as client:
            response = client.get("/model-profiles")
        assert response.status_code == 202
        assert_acknowledged(response, ["profiles.snapshot"])
        assert events[-1][0] == "profiles.snapshot"
        profiles = events[-1][1]["profiles"]
        assert len(profiles) == 1
        public = profiles[0]
        assert public["name"] == "general"
        assert public["provider_id"] == "foundry-1"
        assert public["deployment"] == "greeting"
        assert public["reasoning_enabled"] is True
        assert public["reasoning_effort"] == "medium"
        assert public["reasoning_summary"] == "concise"


class TestGetAcknowledgement:
    @pytest.mark.parametrize(
        "payload",
        [
            "not-a-dict",
            {"invalid": {"provider_id": "", "deployment": "greeting"}},
            {
                "general": {
                    "provider_id": "foundry-1",
                    "deployment": "greeting",
                    "reasoning_summary": {"nested": "must-not-leak"},
                }
            },
        ],
    )
    def test_get_reports_malformed_store_document(self, payload):
        store = InMemoryDocumentStore()
        store.save_document(PROGENITOR_STATE_PREFIX, "profile", payload)
        app, events = _make_app(store)
        with TestClient(app) as client:
            response = client.get("/model-profiles")
        assert response.status_code == 422
        assert events == []
        assert "must-not-leak" not in response.text

    def test_get_allows_an_empty_profile_catalog(self):
        app, events = _make_app(InMemoryDocumentStore())
        with TestClient(app) as client:
            response = client.get("/model-profiles")
        assert response.status_code == 202
        assert events[-1][1] == {"profiles": []}

    def test_get_migrates_legacy_azure_profile_to_default_foundry(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {"general": {"provider": "azure_openai", "deployment": "legacy"}},
        )
        repository = FakeProviderRepository(
            [{"id": "foundry-1", "kind": "foundry", "is_default": True}]
        )
        app, events = _make_app(store, repository)
        with TestClient(app) as client:
            response = client.get("/model-profiles")
        assert response.status_code == 202
        assert store.get_document(PROGENITOR_STATE_PREFIX, "profile").payload == {
            "general": {"provider_id": "foundry-1", "deployment": "legacy"}
        }
        assert events[-1][1]["profiles"][0]["provider_id"] == "foundry-1"

    def test_get_migration_removes_legacy_connection_fields(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {
                "general": {
                    "provider": "azure_openai",
                    "deployment": "legacy",
                    "api_key": "previously-valid-secret",
                    "endpoint": "https://old-resource.openai.azure.com",
                    "reasoning_effort": "high",
                }
            },
        )
        repository = FakeProviderRepository(
            [{"id": "foundry-1", "kind": "foundry", "is_default": True}]
        )
        app, events = _make_app(store, repository)

        with TestClient(app) as client:
            response = client.get("/model-profiles")

        assert response.status_code == 202
        assert store.get_document(PROGENITOR_STATE_PREFIX, "profile").payload == {
            "general": {
                "provider_id": "foundry-1",
                "deployment": "legacy",
                "reasoning_effort": "high",
            }
        }
        assert events[-1][1]["profiles"][0]["reasoning_effort"] == "high"


class TestPatchEdits:
    def test_patch_updates_deployment(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {
                "general": {
                    "provider_id": "foundry-1",
                    "deployment": "old",
                }
            },
        )
        app, events = _make_app(store)
        with TestClient(app, headers={"X-Request-ID": "patch-test"}) as client:
            response = client.patch("/model-profiles/general", json={"deployment": "new"})
        assert response.status_code == 202
        assert_acknowledged(response, ["profiles.snapshot"], request_id="patch-test")
        # Event broadcast
        assert events[-1][0] == "profiles.snapshot"
        profiles = events[-1][1]["profiles"]
        assert profiles[0]["deployment"] == "new"
        saved = store.get_document(PROGENITOR_STATE_PREFIX, "profile").payload
        assert saved["general"]["deployment"] == "new"

    def test_patch_preserves_sibling_profiles(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {
                "general": {"provider_id": "foundry-1", "deployment": "general-deployment"},
                "review": {"provider_id": "foundry-1", "deployment": "review-deployment"},
            },
        )
        app, events = _make_app(store)
        with TestClient(app) as client:
            response = client.patch("/model-profiles/general", json={"deployment": "updated"})
        assert response.status_code == 202
        saved = store.get_document(PROGENITOR_STATE_PREFIX, "profile").payload
        assert saved["general"]["deployment"] == "updated"
        assert saved["review"]["deployment"] == "review-deployment"

    def test_patch_adds_and_removes_reasoning_fields(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {
                "general": {
                    "provider_id": "foundry-1",
                    "deployment": "greeting",
                }
            },
        )
        app, events = _make_app(store)
        with TestClient(app) as client:
            # Add reasoning fields
            r1 = client.patch(
                "/model-profiles/general",
                json={
                    "deployment": "greeting",
                    "reasoning_enabled": True,
                    "reasoning_effort": "high",
                    "reasoning_summary": "concise",
                },
            )
        assert r1.status_code == 202
        saved = store.get_document(PROGENITOR_STATE_PREFIX, "profile").payload
        assert saved["general"]["reasoning_enabled"] is True
        assert saved["general"]["reasoning_effort"] == "high"
        assert saved["general"]["reasoning_summary"] == "concise"

        with TestClient(app) as client:
            # Remove reasoning fields with null
            r2 = client.patch(
                "/model-profiles/general",
                json={
                    "reasoning_enabled": None,
                    "reasoning_effort": None,
                    "reasoning_summary": None,
                },
            )
        assert r2.status_code == 202
        saved = store.get_document(PROGENITOR_STATE_PREFIX, "profile").payload
        assert "reasoning_enabled" not in saved["general"]
        assert "reasoning_effort" not in saved["general"]
        assert "reasoning_summary" not in saved["general"]

    def test_patch_missing_profile_returns_404(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {"general": {"provider_id": "foundry-1", "deployment": "greeting"}},
        )
        app, _ = _make_app(store)
        with TestClient(app) as client:
            response = client.patch("/model-profiles/missing", json={"deployment": "x"})
        assert response.status_code == 404

    def test_patch_empty_deployment_returns_422(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {"general": {"provider_id": "foundry-1", "deployment": "greeting"}},
        )
        app, _ = _make_app(store)
        with TestClient(app) as client:
            response = client.patch("/model-profiles/general", json={"deployment": ""})
        assert response.status_code == 422

    def test_patch_null_deployment_returns_422(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {"general": {"provider_id": "foundry-1", "deployment": "greeting"}},
        )
        app, _ = _make_app(store)
        with TestClient(app) as client:
            response = client.patch("/model-profiles/general", json={"deployment": None})
        assert response.status_code == 422

    def test_patch_rejects_non_boolean_reasoning_enabled(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {"general": {"provider_id": "foundry-1", "deployment": "greeting"}},
        )
        app, _ = _make_app(store)
        with TestClient(app) as client:
            response = client.patch("/model-profiles/general", json={"reasoning_enabled": "true"})
        assert response.status_code == 422

    def test_patch_rejects_empty_reasoning_effort(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {"general": {"provider_id": "foundry-1", "deployment": "greeting"}},
        )
        app, _ = _make_app(store)
        with TestClient(app) as client:
            response = client.patch("/model-profiles/general", json={"reasoning_effort": "  "})
        assert response.status_code == 422

    def test_patch_rejects_arbitrary_extra_fields(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {"general": {"provider_id": "foundry-1", "deployment": "greeting"}},
        )
        app, _ = _make_app(store)
        with TestClient(app) as client:
            response = client.patch(
                "/model-profiles/general",
                json={"deployment": "x", "extra": "not-allowed"},
            )
        assert response.status_code == 422

    def test_patch_profile_with_special_characters_in_name(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {
                "smoke profile/review": {
                    "provider_id": "foundry-1",
                    "deployment": "reviewer",
                }
            },
        )
        app, events = _make_app(store)
        encoded_name = "smoke%20profile%2Freview"
        with TestClient(app) as client:
            response = client.patch(
                f"/model-profiles/{encoded_name}", json={"deployment": "updated"}
            )
        assert response.status_code == 202
        saved = store.get_document(PROGENITOR_STATE_PREFIX, "profile").payload
        assert saved["smoke profile/review"]["deployment"] == "updated"

    def test_patch_rejects_unsupported_go_deployment(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {"general": {"provider_id": "foundry-1", "deployment": "greeting"}},
        )
        repository = FakeProviderRepository([{"id": "go-1", "kind": "opencode_go"}])
        app, _ = _make_app(store, repository)
        with TestClient(app) as client:
            response = client.patch(
                "/model-profiles/general",
                json={"provider_id": "go-1", "deployment": "unsupported"},
            )
        assert response.status_code == 422

    def test_patch_rejects_unknown_provider_id(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {"general": {"provider_id": "foundry-1", "deployment": "greeting"}},
        )
        app, _ = _make_app(store, FakeProviderRepository([]))
        with TestClient(app) as client:
            response = client.patch("/model-profiles/general", json={"provider_id": "missing"})
        assert response.status_code == 422


class TestDeleteProfiles:
    def test_delete_preserves_remaining_raw_profiles_and_broadcasts_snapshot(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {
                "general": {
                    "provider_id": "foundry-1",
                    "deployment": "general-deployment",
                },
                "review/profile": {
                    "provider_id": "foundry-1",
                    "deployment": "review-deployment",
                },
            },
        )
        app, events = _make_app(store)
        with TestClient(app, headers={"X-Request-ID": "delete-test"}) as client:
            response = client.delete("/model-profiles/review%2Fprofile")

        assert response.status_code == 202
        assert_acknowledged(response, ["profiles.snapshot"], request_id="delete-test")
        assert store.get_document(PROGENITOR_STATE_PREFIX, "profile").payload == {
            "general": {
                "provider_id": "foundry-1",
                "deployment": "general-deployment",
            }
        }
        assert events == [
            (
                "profiles.snapshot",
                {
                    "profiles": [
                        {
                            "name": "general",
                            "provider_id": "foundry-1",
                            "deployment": "general-deployment",
                        }
                    ]
                },
                {"request_id": "delete-test"},
            )
        ]

    def test_delete_migrates_legacy_catalog_before_removing_profile(self):
        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {
                "general": {
                    "provider": "azure_openai",
                    "deployment": "general-deployment",
                    "api_key": "previously-valid-secret",
                },
                "review": {
                    "provider": "azure_openai",
                    "deployment": "review-deployment",
                    "reasoning_summary": "concise",
                },
            },
        )
        repository = FakeProviderRepository(
            [{"id": "foundry-1", "kind": "foundry", "is_default": True}]
        )
        app, events = _make_app(store, repository)

        with TestClient(app, headers={"X-Request-ID": "delete-migration-test"}) as client:
            response = client.delete("/model-profiles/general")

        assert response.status_code == 202
        assert_acknowledged(response, ["profiles.snapshot"], request_id="delete-migration-test")
        assert store.get_document(PROGENITOR_STATE_PREFIX, "profile").payload == {
            "review": {
                "provider_id": "foundry-1",
                "deployment": "review-deployment",
                "reasoning_summary": "concise",
            }
        }
        assert events[-1] == (
            "profiles.snapshot",
            {
                "profiles": [
                    {
                        "name": "review",
                        "provider_id": "foundry-1",
                        "deployment": "review-deployment",
                        "reasoning_summary": "concise",
                    }
                ]
            },
            {"request_id": "delete-migration-test"},
        )

    def test_delete_unknown_profile_does_not_persist_or_broadcast(self):
        store = InMemoryDocumentStore()
        original = {"general": {"provider_id": "foundry-1", "deployment": "general"}}
        store.save_document(PROGENITOR_STATE_PREFIX, "profile", original)
        app, events = _make_app(store)

        with TestClient(app) as client:
            response = client.delete("/model-profiles/missing")

        assert response.status_code == 404
        assert store.get_document(PROGENITOR_STATE_PREFIX, "profile").payload == original
        assert events == []

    def test_whitespace_aliases_of_one_name_are_rejected_not_half_deleted(self):
        store = InMemoryDocumentStore()
        original = {
            " general ": {"provider_id": "foundry-1", "deployment": "a"},
            "general": {"provider_id": "foundry-1", "deployment": "b"},
        }
        store.save_document(PROGENITOR_STATE_PREFIX, "profile", original)
        app, events = _make_app(store)

        with TestClient(app) as client:
            response = client.delete("/model-profiles/general")

        assert response.status_code == 422
        assert "ambiguous" in response.text
        assert store.get_document(PROGENITOR_STATE_PREFIX, "profile").payload == original
        assert events == []


class TestNotificationSameTransactionDiffConsumption:
    """When an external edit commits in the same Postgres transaction as a
    health heartbeat update, the health diff may be consumed first. The profile
    key must still trigger its own dedicated snapshot broadcast even when the
    coarse StateSnapshot hash hasn't changed.
    """

    def test_profile_notify_survives_snapshot_consumed_by_sibling_document(self):
        from app.backend.state_reader import StateReader

        store = InMemoryDocumentStore()
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            "profile",
            {"general": {"provider_id": "foundry-1", "deployment": "greeting"}},
        )
        state_reader = StateReader(document_store=store)
        state_reader._last_snapshot = state_reader.get_snapshot()

        received: list[tuple[Any, str | None]] = []

        def watcher(snapshot, changed_key):
            received.append((snapshot, changed_key))

        state_reader.add_watcher(watcher)

        state_reader._on_store_change(
            '{"prefix":"progenitor.state","key":"profile","action":"UPDATE"}'
        )
        assert [key for _, key in received] == ["profile"]


def test_invalid_sibling_rejects_patch_without_persisting():
    store = InMemoryDocumentStore()
    original = {
        "general": {"provider_id": "foundry-1", "deployment": "original"},
        "broken": {"provider_id": "foundry-1", "deployment": "other", "reasoning_enabled": "wrong"},
    }
    store.save_document(PROGENITOR_STATE_PREFIX, "profile", original)
    app, events = _make_app(store)
    with TestClient(app) as client:
        response = client.patch("/model-profiles/general", json={"deployment": "changed"})
    assert response.status_code == 422
    assert store.get_document(PROGENITOR_STATE_PREFIX, "profile").payload == original
    assert events == []


def test_patch_preserves_normalized_profile_identity_without_duplicating_store_key():
    store = InMemoryDocumentStore()
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "profile",
        {
            " general ": {"provider_id": "foundry-1", "deployment": "original"},
        },
    )
    app, _ = _make_app(store)
    with TestClient(app) as client:
        response = client.patch("/model-profiles/general", json={"deployment": "changed"})
    assert response.status_code == 202
    assert store.get_document(PROGENITOR_STATE_PREFIX, "profile").payload == {
        " general ": {"provider_id": "foundry-1", "deployment": "changed"},
    }
