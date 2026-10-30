from __future__ import annotations

import httpx
import pytest

from src.backend.chat.service import (
    _messages_from_checkpoint,
    _summary_markers_from_checkpoint,
    summary_markers_for_page,
)
from src.backend.integrations.runtime_history import (
    RuntimeConversationReader,
    RuntimeSummaryOccurrence,
    RuntimeUnavailable,
)
from src.backend.storage.documents import CheckpointThreadSnapshot


def test_committed_root_marker_anchors_at_created_boundary_not_compaction_cutoff() -> None:
    snapshot = CheckpointThreadSnapshot(
        thread_id="thread-1",
        checkpoint_id="cp-1",
        checkpoint_ts="2026-10-30T00:00:00Z",
        messages=[
            {"id": "u1", "type": "human", "content": "first"},
            {"id": "a1", "type": "ai", "content": "reply"},
            {"id": "u2", "type": "human", "content": "later"},
        ],
        summary_occurrences=(
            RuntimeSummaryOccurrence(
                id="summary:root:cp-1",
                summary="Native summary body",
                # The native cutoff can be earlier; the marker belongs after a1,
                # the row immediately before this creation boundary.
                created_at_index=2,
                anchor_message_id="a1",
                scope={"kind": "conversation", "id": "thread-1"},
                committed_at="2026-10-30T00:00:00Z",
            ),
        ),
    )

    messages = _messages_from_checkpoint(snapshot)
    markers = _summary_markers_from_checkpoint(snapshot, messages)

    assert [(marker.id, marker.anchor_message_id) for marker in markers] == [
        ("summary:root:cp-1", "a1")
    ]
    assert summary_markers_for_page(markers, [messages[-1]], includes_transcript_head=False) == []
    assert (
        summary_markers_for_page(markers, [messages[1]], includes_transcript_head=False) == markers
    )


def test_summary_read_never_substitutes_an_older_checkpoint_when_runtime_fails() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params["checkpoint_id"] == "cp-2":
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "progena_summary_occurrences": [
                    {
                        "id": "summary:root:cp-1",
                        "summary": "Committed context",
                        "created_at_index": 2,
                        "anchor_message_id": "a1",
                        "scope": {"kind": "conversation", "id": "thread-1"},
                    }
                ]
            },
        )

    reader = RuntimeConversationReader("http://runtime.test")
    reader.close()
    reader._client = httpx.Client(
        base_url="http://runtime.test", transport=httpx.MockTransport(respond)
    )
    try:
        assert reader.get_summary_occurrences("thread-1", "cp-1")[0].id == "summary:root:cp-1"
        with pytest.raises(RuntimeUnavailable, match="503"):
            reader.get_summary_occurrences("thread-1", "cp-2")
        assert reader.get_summary_occurrences("thread-1", "cp-1")[0].id == "summary:root:cp-1"
    finally:
        reader.close()
