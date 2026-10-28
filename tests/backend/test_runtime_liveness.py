from src.backend.runtime_liveness import LiveRuntime, live_runtime, owner_is_gone
from src.backend.storage.documents import PROGENA_STATE_PREFIX, InMemoryDocumentStore


def test_live_runtime_reads_complete_health_identity() -> None:
    store = InMemoryDocumentStore()
    store.save_document(
        PROGENA_STATE_PREFIX,
        "health",
        {"runtime_instance": "new", "started_at": "2026-10-28T12:00:00Z"},
    )

    assert live_runtime(store) == LiveRuntime("new", "2026-10-28T12:00:00Z")


def test_owner_is_gone_requires_a_known_newer_runtime() -> None:
    live = LiveRuntime("new", "2026-10-28T12:00:00Z")

    assert owner_is_gone("old", "2026-10-28T11:00:00Z", live)
    assert not owner_is_gone("new", "2026-10-28T11:00:00Z", live)
    assert owner_is_gone(None, "2026-10-28T11:00:00Z", live)
    assert not owner_is_gone(None, "2026-10-28T12:00:00Z", live)
    assert not owner_is_gone(None, None, live)
    assert not owner_is_gone("old", "2026-10-28T11:00:00Z", None)
