"""Schedules that deliver into a chat: destination validation and chat lifecycle."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import src.backend.schedules.service as schedules_service
from src.backend.chat.service import _chat_message_from_raw
from src.backend.monitoring.state import StateReader
from src.backend.storage.documents import PROGENA_STATE_PREFIX, InMemoryDocumentStore
from src.progena.documents.kinds import SESSION_KEY_PREFIX
from src.progena.schedules.dispatch import SCHEDULED_INPUT_PREFIX


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> InMemoryDocumentStore:
    store = InMemoryDocumentStore()
    state_reader = StateReader(document_store=store)
    monkeypatch.setattr("src.backend.monitoring.state.get_state_reader", lambda: state_reader)
    monkeypatch.setattr(schedules_service, "_broadcast_widget_refresh", lambda *a, **k: None)
    monkeypatch.setattr(schedules_service, "broadcast_schedule_snapshot", lambda *a, **k: None)
    return store


def _session(store: InMemoryDocumentStore, thread_id: str, *, archived: bool = False) -> None:
    store.save_document(
        PROGENA_STATE_PREFIX,
        f"{SESSION_KEY_PREFIX}{thread_id}",
        {"id": thread_id, "thread_id": thread_id, "archived": archived},
    )


def _schedules(store: InMemoryDocumentStore) -> dict:
    document = store.get_document(PROGENA_STATE_PREFIX, "schedule", {})
    assert document is not None
    return document.payload


def test_destination_must_be_an_existing_unarchived_chat(store: InMemoryDocumentStore) -> None:
    _session(store, "conv_live")
    _session(store, "conv_archived", archived=True)

    assert schedules_service.validate_destination("conv_live") == "conv_live"
    assert schedules_service.validate_destination(None) is None
    assert schedules_service.validate_destination("  ") is None
    with pytest.raises(HTTPException) as missing:
        schedules_service.validate_destination("conv_gone")
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as archived:
        schedules_service.validate_destination("conv_archived")
    assert archived.value.status_code == 409


def test_archiving_pauses_only_that_chats_schedules(store: InMemoryDocumentStore) -> None:
    store.save_document(
        PROGENA_STATE_PREFIX,
        "schedule",
        {
            "sched_a": {"id": "sched_a", "conversation_id": "conv_a", "enabled": True},
            "sched_b": {"id": "sched_b", "conversation_id": "conv_b", "enabled": True},
            "sched_s": {"id": "sched_s", "conversation_id": None, "enabled": True},
        },
    )

    assert schedules_service.pause_conversation_schedules("conv_a") == 1

    entries = _schedules(store)
    assert entries["sched_a"]["paused"] is True
    assert entries["sched_a"]["enabled"] is False
    assert entries["sched_b"].get("paused") is None
    assert entries["sched_s"].get("paused") is None


def test_deleting_a_chat_deletes_only_its_schedules(store: InMemoryDocumentStore) -> None:
    store.save_document(
        PROGENA_STATE_PREFIX,
        "schedule",
        {
            "sched_a": {"id": "sched_a", "conversation_id": "conv_a"},
            "sched_b": {"id": "sched_b", "conversation_id": "conv_b"},
            "sched_s": {"id": "sched_s", "conversation_id": None},
        },
    )

    assert schedules_service.delete_conversation_schedules("conv_a") == 1
    assert set(_schedules(store)) == {"sched_b", "sched_s"}
    assert schedules_service.delete_conversation_schedules("conv_a") == 0


def test_scheduled_input_keeps_its_provenance_when_history_reloads() -> None:
    raw = {
        "type": "human",
        "id": "m1",
        "content": f"{SCHEDULED_INPUT_PREFIX}check agent X",
        "additional_kwargs": {"scheduled": {"schedule_id": "sched_a", "run_id": "schedrun_1"}},
    }
    message = _chat_message_from_raw(raw, 0)

    assert message is not None
    assert message.role == "user"
    assert message.content == "check agent X"
    assert message.scheduled == {"schedule_id": "sched_a", "run_id": "schedrun_1"}


def test_ordinary_messages_have_no_scheduled_provenance() -> None:
    message = _chat_message_from_raw({"type": "human", "id": "m1", "content": "hello"}, 0)

    assert message is not None
    assert message.scheduled is None
