"""Declared repositories are one ``repo:<id>`` document each."""

import pytest

from src.backend.events.documents import seed_document_types
from src.backend.repositories import service as repo_service
from src.backend.storage.documents import (
    EVENT_LOG_START,
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
    set_default_document_store,
)
from src.progenitor.agent.filesystem.repositories import RepoDirectoryBackend


@pytest.fixture
def store():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    set_default_document_store(store)
    yield store
    set_default_document_store(InMemoryDocumentStore())


def test_direct_repo_document_write_reaches_ui_watchers(store):
    """The agent writes repo:<id> straight to the store; the NOTIFY must still
    reach the Repositories page even though no snapshot field changes."""
    from src.backend.monitoring.state import StateReader

    reader = StateReader(document_store=store)
    reader._last_snapshot = reader.get_snapshot()
    seen: list[object] = []
    reader.add_watcher(lambda _snapshot, key: seen.append(key))

    reader._on_store_change('{"prefix": "progenitor.state", "key": "repo:web-app"}')
    reader._on_store_change('{"prefix": "progenitor.state", "key": "unrelated"}')

    assert seen == ["repo:web-app"]


def _repo_events(store):
    return [
        (event.key, event.op)
        for event in store.read_document_events(EVENT_LOG_START, 100)
        if event.key.startswith("repo:")
    ]


def test_each_repo_write_is_its_own_document_and_event(store):
    first_id, first = repo_service.create_repo(name="My Service", local_path="svc", access="read")
    second_id, _ = repo_service.create_repo(name="My Service", local_path="svc2", access="write")
    assert (first_id, second_id) == ("my-service", "my-service-2")
    assert first["created_at"] and first["updated_at"]

    repo_service.update_repo(first_id, {"access": "write"})
    assert repo_service.delete_repo(second_id) is True
    assert repo_service.delete_repo(second_id) is False

    assert set(repo_service.load_repos()) == {"my-service"}
    assert repo_service.load_repo("my-service")["access"] == "write"
    assert _repo_events(store) == [
        ("repo:my-service", "created"),
        ("repo:my-service-2", "created"),
        ("repo:my-service", "updated"),
        ("repo:my-service-2", "deleted"),
    ]


def test_update_keeps_created_at_and_validates(store):
    repo_id, created = repo_service.create_repo(name="Svc", local_path="svc", access="read")
    updated = repo_service.update_repo(repo_id, {"summary": "notes", "created_at": "1999-01-01"})
    assert updated["created_at"] == created["created_at"]
    assert updated["summary"] == "notes"

    with pytest.raises(ValueError):
        repo_service.update_repo(repo_id, {"access": "admin"})
    assert repo_service.load_repo(repo_id)["access"] == "read"
    with pytest.raises(repo_service.RepoNotFoundError):
        repo_service.update_repo("missing", {"access": "write"})


@pytest.mark.asyncio
async def test_agent_repo_files_validate_and_keep_created_at(store):
    from src.progenitor.state.manager import StateManager

    class _Store:
        async def peek_document(self, key):
            doc = store.get_document(PROGENITOR_STATE_PREFIX, key, None)
            return doc.payload if doc else None

        async def save_document(self, key, payload):
            store.save_document(PROGENITOR_STATE_PREFIX, key, payload)

        async def delete_document(self, key):
            store.delete_document(PROGENITOR_STATE_PREFIX, key)

        async def list_documents(self, prefix):
            return [
                (k, d.payload) for k, d in store.list_documents(PROGENITOR_STATE_PREFIX, prefix)
            ]

    manager = StateManager(_Store())
    backend = RepoDirectoryBackend(manager)

    written = await backend.awrite(
        "/web-app.json", '{"name": "Web", "local_path": "web", "access": "read"}'
    )
    assert written.error is None
    created_at = repo_service.load_repo("web-app")["created_at"]

    edited = await backend.aedit("/web-app.json", '"access": "read"', '"access": "write"')
    assert edited.error is None
    assert repo_service.load_repo("web-app")["access"] == "write"
    assert repo_service.load_repo("web-app")["created_at"] == created_at

    bad = await backend.awrite("/Bad_Id.json", '{"name": "x", "local_path": "x", "access": "read"}')
    assert bad.error and "kebab-case" in bad.error
    invalid = await backend.awrite("/other.json", '{"name": "x", "access": "read"}')
    assert invalid.error and "local_path" in invalid.error
    assert [entry["path"] for entry in (await backend.als("/")).entries] == ["/web-app.json"]

    assert (await backend.adelete("/web-app.json")).error is None
    assert repo_service.load_repo("web-app") is None
