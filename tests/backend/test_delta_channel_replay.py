from __future__ import annotations

from app.backend.delta_channel_replay import replay_delta_messages


def test_replay_delta_messages_reconstructs_from_writes_with_no_seed():
    # Mirrors the common case in this codebase: every dev thread is well
    # under the DeltaChannel's snapshot_frequency, so no ancestor checkpoint
    # ever populated channel_values["messages"] and there is no seed.
    entry = {
        "writes": [
            ("task-1", "messages", [{"type": "human", "content": "hi"}]),
            ("task-2", "messages", [{"type": "ai", "content": "hello"}]),
        ],
    }

    messages = replay_delta_messages(entry)

    assert [(type(m).__name__, m.content) for m in messages] == [
        ("HumanMessage", "hi"),
        ("AIMessage", "hello"),
    ]


def test_replay_delta_messages_appends_writes_after_a_seed():
    entry = {
        "seed": [{"type": "human", "content": "seed message"}],
        "writes": [
            ("task-1", "messages", [{"type": "ai", "content": "follow up"}]),
        ],
    }

    messages = replay_delta_messages(entry)

    assert [(type(m).__name__, m.content) for m in messages] == [
        ("HumanMessage", "seed message"),
        ("AIMessage", "follow up"),
    ]


def test_replay_delta_messages_returns_empty_list_for_no_writes():
    assert replay_delta_messages({"writes": []}) == []
