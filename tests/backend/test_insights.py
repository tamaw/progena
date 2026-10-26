from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi.testclient import TestClient
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

import src.backend.events.stream as events_stream
from src.backend.insights.service import build_token_usage_snapshot
from src.backend.main import app
from src.backend.storage.documents import CheckpointMessageBlob, StoredDocument


class _InsightsStore:
    def __init__(self, records: list[CheckpointMessageBlob], profiles: dict) -> None:
        self.records = records
        self.profiles = profiles

    def list_checkpoint_message_blobs(self, *, before: datetime, limit: int | None = None):
        eligible = [record for record in self.records if record.checkpoint_ts < before]
        return eligible[:limit], limit is not None and len(eligible) > limit

    def get_document(self, _prefix: str, _key: str):
        return StoredDocument(payload=self.profiles)


def _record(
    checkpoint_id: str,
    timestamp: datetime,
    messages: list[dict],
    *,
    metadata: dict | None = None,
    source: Literal["channel_blob", "checkpoint_write"] = "channel_blob",
) -> CheckpointMessageBlob:
    blob_type, blob = JsonPlusSerializer(pickle_fallback=False).dumps_typed(messages)
    return CheckpointMessageBlob(
        thread_id="thread-1",
        checkpoint_ns="",
        checkpoint_id=checkpoint_id,
        checkpoint_ts=timestamp,
        checkpoint_metadata=metadata or {},
        blob_type=blob_type,
        blob=blob,
        source=source,
    )


def _message(
    message_id: str,
    occurred_at: str,
    usage: dict | None,
    *,
    profile: str | None = "fast",
    deployment: str = "deployed-fast",
) -> dict:
    insights = {"profile_name": profile, "deployment": deployment, "occurred_at": occurred_at}
    return {
        "type": "ai",
        "id": message_id,
        "usage_metadata": usage or {},
        "response_metadata": {"insights": insights},
    }


def _profiles() -> dict:
    return {
        "fast": {
            "provider_id": "foundry-1",
            "deployment": "deployed-fast",
            "pricing": {
                "currency": "USD",
                "input_per_million": "2",
                "cached_input_per_million": "1",
                "output_per_million": "4",
            },
        },
        "euro": {
            "provider_id": "foundry-1",
            "deployment": "deployed-euro",
            "pricing": {
                "currency": "EUR",
                "input_per_million": "1",
                "cached_input_per_million": "0",
                "output_per_million": "3",
            },
        },
    }


def test_chatgpt_plan_usage_is_not_charged_at_profile_api_rates():
    end = datetime(2026, 10, 20, 12, tzinfo=UTC)
    occurred = end - timedelta(hours=1)
    message = _message(
        "plan-call", occurred.isoformat(), {"input_tokens": 100, "output_tokens": 20}
    )
    message["response_metadata"]["insights"]["provider"] = "chatgpt"
    snapshot = build_token_usage_snapshot(
        _InsightsStore([_record("plan", occurred, [message])], _profiles()),
        range_name="24h",
        profile=None,
        generated_at=end,
    )

    assert snapshot["totals"]["total_tokens"] == 120
    assert snapshot["totals"]["costs"] == []
    assert snapshot["totals"]["unpriced_call_count"] == 1
    assert snapshot["by_profile"][0]["costs"] == []
    assert all(bucket["costs"] == [] for bucket in snapshot["series"])


def test_usage_deduplicates_checkpoint_snapshots_and_honors_half_open_boundaries():
    end = datetime(2026, 10, 20, 12, tzinfo=UTC)
    start = end - timedelta(hours=24)
    records = [
        _record(
            "one",
            start,
            [_message("call-a", start.isoformat(), {"input_tokens": 10, "output_tokens": 2})],
        ),
        _record(
            "two",
            start + timedelta(minutes=1),
            [_message("call-a", start.isoformat(), {"input_tokens": 20, "output_tokens": 3})],
        ),
        _record(
            "three",
            end - timedelta(seconds=1),
            [
                _message(
                    "call-b",
                    (end - timedelta(seconds=1)).isoformat(),
                    {"input_tokens": 4, "output_tokens": 1},
                )
            ],
        ),
        _record(
            "four",
            end - timedelta(seconds=1),
            [_message("call-c", end.isoformat(), {"input_tokens": 99, "output_tokens": 1})],
        ),
    ]

    snapshot = build_token_usage_snapshot(
        _InsightsStore(records, _profiles()), range_name="24h", profile=None, generated_at=end
    )

    assert snapshot["totals"]["call_count"] == 2
    assert snapshot["totals"]["input_tokens"] == 24
    assert snapshot["totals"]["output_tokens"] == 4
    assert snapshot["totals"]["total_tokens"] == 28
    assert snapshot["totals"]["unknown_usage_count"] == 0
    assert snapshot["by_profile"][0]["profile_name"] == "fast"
    assert all(row["bucket_start"].endswith("Z") for row in snapshot["series"])


def test_usage_includes_persisted_message_delta_without_channel_snapshot():
    end = datetime(2026, 10, 20, 12, tzinfo=UTC)
    record = _record(
        "delta-only",
        end - timedelta(hours=1),
        [
            _message(
                "delta-call",
                (end - timedelta(hours=1)).isoformat(),
                {"input_tokens": 3, "output_tokens": 2},
            )
        ],
        source="checkpoint_write",
    )

    snapshot = build_token_usage_snapshot(
        _InsightsStore([record], _profiles()), range_name="24h", profile=None, generated_at=end
    )

    assert snapshot["totals"]["call_count"] == 1
    assert snapshot["totals"]["total_tokens"] == 5


def test_usage_counts_copied_messages_once_and_ignores_synthetic_summaries():
    end = datetime(2026, 10, 20, 12, tzinfo=UTC)
    when = (end - timedelta(hours=1)).isoformat()
    call = _message("call-1", when, {"input_tokens": 3, "output_tokens": 2})
    parent = _record("parent", end - timedelta(hours=1), [call])
    child = replace(_record("child", end - timedelta(minutes=30), [call]), thread_id="thread-2")
    synthetic = {"type": "ai", "id": "summary", "usage_metadata": {}, "response_metadata": {}}
    extra = _record("summary", end - timedelta(minutes=20), [synthetic])

    snapshot = build_token_usage_snapshot(
        _InsightsStore([parent, child, extra], _profiles()),
        range_name="24h",
        profile=None,
        generated_at=end,
    )

    assert snapshot["totals"]["call_count"] == 1
    assert snapshot["totals"]["total_tokens"] == 5


def test_repeated_incomplete_snapshot_keeps_first_observation_and_known_usage():
    end = datetime(2026, 10, 20, 12, tzinfo=UTC)
    start = end - timedelta(hours=24)
    first = {
        "type": "ai",
        "id": "legacy-call",
        "usage_metadata": {"input_tokens": 10, "output_tokens": 4},
        "response_metadata": {},
    }
    incomplete_repeat = {
        "type": "ai",
        "id": "legacy-call",
        "usage_metadata": {},
        "response_metadata": {},
    }
    records = [
        _record(
            "before-window",
            start - timedelta(minutes=1),
            [first],
            metadata={"model_profile": "fast"},
        ),
        _record(
            "inside-window",
            start + timedelta(minutes=1),
            [incomplete_repeat],
            metadata={},
        ),
    ]

    current_window = build_token_usage_snapshot(
        _InsightsStore(records, _profiles()), range_name="24h", profile=None, generated_at=end
    )
    longer_window = build_token_usage_snapshot(
        _InsightsStore(records, _profiles()), range_name="7d", profile=None, generated_at=end
    )

    assert current_window["totals"]["call_count"] == 0
    assert longer_window["totals"]["call_count"] == 1
    assert longer_window["totals"]["input_tokens"] == 10
    assert longer_window["totals"]["output_tokens"] == 4
    assert longer_window["by_profile"][0]["profile_name"] == "fast"


def test_usage_calculates_cache_discount_without_mixing_currencies_or_pricing_unknown_calls():
    end = datetime(2026, 10, 20, 12, tzinfo=UTC)
    occurred = (end - timedelta(hours=1)).isoformat()
    records = [
        _record(
            "one",
            end - timedelta(hours=1),
            [
                _message(
                    "cache",
                    occurred,
                    {
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "input_token_details": {"cache_read": 200},
                    },
                ),
                _message(
                    "euro",
                    occurred,
                    {"input_tokens": 1000, "output_tokens": 1000},
                    profile="euro",
                    deployment="deployed-euro",
                ),
                _message(
                    "unpriced",
                    occurred,
                    {"input_tokens": 1, "output_tokens": 1},
                    profile=None,
                ),
            ],
        )
    ]

    snapshot = build_token_usage_snapshot(
        _InsightsStore(records, _profiles()), range_name="24h", profile=None, generated_at=end
    )

    assert snapshot["totals"]["unpriced_call_count"] == 1
    assert snapshot["totals"]["costs"] == [
        {"currency": "EUR", "amount": "0.004"},
        {"currency": "USD", "amount": "0.0038"},
    ]
    assert snapshot["coverage"]["unattributed_call_count"] == 1
    filtered = build_token_usage_snapshot(
        _InsightsStore(records, _profiles()), range_name="24h", profile="euro", generated_at=end
    )
    assert filtered["totals"]["call_count"] == 1
    assert filtered["totals"]["costs"] == [{"currency": "EUR", "amount": "0.004"}]


def test_usage_marks_missing_usage_and_missing_cache_detail_without_calling_them_zero_cost():
    end = datetime(2026, 10, 20, 12, tzinfo=UTC)
    occurred = (end - timedelta(hours=1)).isoformat()
    records = [
        _record(
            "one",
            end - timedelta(hours=1),
            [
                _message(
                    "known-no-cache", occurred, {"input_tokens": 1_000_000, "output_tokens": 0}
                ),
                _message("total-only", occurred, {"total_tokens": 8}),
                _message("unknown", occurred, None),
            ],
        )
    ]

    snapshot = build_token_usage_snapshot(
        _InsightsStore(records, _profiles()), range_name="24h", profile=None, generated_at=end
    )

    assert snapshot["totals"]["call_count"] == 3
    assert snapshot["totals"]["unknown_usage_count"] == 2
    assert snapshot["totals"]["unpriced_call_count"] == 2
    assert snapshot["totals"]["total_tokens"] == 1_000_008
    assert snapshot["totals"]["costs"] == [{"currency": "USD", "amount": "2"}]
    assert any(
        "do not report cache-read" in warning for warning in snapshot["coverage"]["warnings"]
    )


def test_token_usage_route_acknowledges_and_correlates_snapshot(monkeypatch):
    from src.backend.insights import routes as insights_routes

    payload = {
        "range": "24h",
        "profile": None,
        "start": "2026-10-19T12:00:00Z",
        "end": "2026-10-20T12:00:00Z",
        "generated_at": "2026-10-20T12:00:00Z",
        "totals": {},
        "by_profile": [],
        "series": [],
        "coverage": {
            "source": "checkpoints",
            "unattributed_call_count": 0,
            "timestamp_basis": "test",
            "warnings": [],
        },
        "pricing_basis": "current_profile_rates",
    }
    monkeypatch.setattr(
        insights_routes, "build_token_usage_snapshot", lambda *_args, **_kwargs: payload
    )
    monkeypatch.setattr(events_stream, "_ui_event_replay", [])
    request_id = "insights-request"
    event_start = len(events_stream._ui_event_replay)

    response = TestClient(app).get(
        "/api/ui/insights/token-usage?range=24h", headers={"X-Operation-Id": request_id}
    )

    assert response.status_code == 202
    assert response.json()["event_types"] == ["insights.token_usage.snapshot"]
    event = next(
        event
        for event in events_stream._ui_event_replay[event_start:]
        if event.payload.get("request_id") == request_id
    )
    assert event.type == "insights.token_usage.snapshot"
    assert event.payload["range"] == "24h"
