from app.backend.document_store import InMemoryDocumentStore
from app.backend.routine_service import get_routine_revision, list_routines, save_routine


def routine_payload():
    return {
        "id": "review-change",
        "title": "Review change",
        "description": "Review a repository change.",
        "category": "engineering",
        "prompt_template": "Review {{ change }}.",
        "input_schema": {"type": "object", "required": ["change"]},
        "output_schema": {"type": "object", "required": ["summary"]},
        "allowed_tools": ["repository.read"],
    }


def test_publish_creates_immutable_pinned_routine_revision():
    store = InMemoryDocumentStore()

    first = save_routine(store, routine_payload(), publish=True)
    updated_payload = {**routine_payload(), "title": "Review change safely"}
    second = save_routine(store, updated_payload, publish=True)

    assert first.published_revision == 1
    assert second.published_revision == 2
    assert get_routine_revision(store, "review-change", 1).title == "Review change"
    assert get_routine_revision(store, "review-change", 2).title == "Review change safely"


def test_draft_does_not_mutate_published_revision():
    store = InMemoryDocumentStore()
    published = save_routine(store, routine_payload(), publish=True)
    draft = save_routine(store, {**routine_payload(), "title": "Draft title"})

    assert draft.published_revision == published.published_revision
    assert list_routines(store)[0].title == "Draft title"
