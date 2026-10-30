from __future__ import annotations

from types import SimpleNamespace

from src.progena.sessions.history import _summary_occurrences


def _checkpoint(
    checkpoint_id: str,
    *,
    event: dict | None = None,
    pending_event: dict | None = None,
) -> SimpleNamespace:
    values = {} if event is None else {"_summarization_event": event}
    pending_writes = ()
    if pending_event is not None:
        pending_writes = (("task", "_summarization_event", pending_event),)
    return SimpleNamespace(
        config={"configurable": {"checkpoint_id": checkpoint_id}},
        checkpoint={
            "ts": f"2026-10-30T00:00:0{len(checkpoint_id)}Z",
            "channel_values": values,
        },
        pending_writes=pending_writes,
    )


def _event(summary: str, *, cutoff_index: int, created_at_index: int) -> dict:
    return {
        "summary_message": {"content": summary},
        "cutoff_index": cutoff_index,
        "created_at_index": created_at_index,
        "file_path": "/private/native-detail",
    }


def test_summary_history_uses_committed_channel_values_and_recovers_overwrites() -> None:
    first = _event("first native summary", cutoff_index=1, created_at_index=3)
    second = _event("second native summary", cutoff_index=4, created_at_index=6)

    occurrences = _summary_occurrences(
        [
            _checkpoint("p", pending_event=first),
            _checkpoint("a", event=first),
            _checkpoint("b", event=first),
            _checkpoint("c", event=second),
        ],
        ns="",
    )

    assert [(item.id, item.summary, item.created_at_index) for item in occurrences] == [
        ("summary:root:a", "first native summary", 3),
        ("summary:root:c", "second native summary", 6),
    ]
