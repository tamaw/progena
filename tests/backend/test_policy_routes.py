from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.backend.settings.policies import create_policy_router
from src.backend.storage.documents import PROGENA_STATE_PREFIX, InMemoryDocumentStore
from src.progena.settings.policies import ExecutionPolicy


def test_policy_requests_acknowledge_and_stream_validated_persisted_documents():
    store, events = InMemoryDocumentStore(), []
    app = FastAPI()
    app.include_router(
        create_policy_router(
            lambda: store, lambda kind, payload, **kw: events.append((kind, payload, kw))
        )
    )
    policy = ExecutionPolicy(id="test", name="Test").model_dump(mode="json")
    with TestClient(app, headers={"X-Operation-Id": "policy-test"}) as client:
        response = client.put("/policies/test", json=policy)
        assert response.status_code == 202
        assert events[-1][0] == "policies.snapshot"
        assert events[-1][1]["items"] == [policy]
        assert store.get_document(PROGENA_STATE_PREFIX, "policy:test").payload == policy
        assert client.get("/policies").status_code == 202
        assert client.put("/policies/wrong", json=policy).status_code == 422
        policy["values"]["cache_ttl"] = -1
        assert client.put("/policies/test", json=policy).status_code == 422
        assert len(events) == 2
