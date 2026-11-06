"""A tab that reconnects can ask for the replies it missed to its requests."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.backend.events import routes as events_routes
from src.backend.events import stream as events_stream
from src.backend.main import app
from src.backend.monitoring import status as monitoring_status


def _connect(client_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    events_stream._ui_stream_subscribers[queue] = None
    events_stream._ui_stream_clients[queue] = client_id
    return queue


def _disconnect(queue: asyncio.Queue) -> None:
    events_stream._ui_stream_subscribers.pop(queue, None)
    events_stream._ui_stream_clients.pop(queue, None)


def _drain(queue: asyncio.Queue) -> list:
    return [queue.get_nowait()["envelope"] for _ in range(queue.qsize())]


@pytest.fixture(autouse=True)
def _clean_outcomes():
    events_stream._request_outcomes.clear()
    events_stream._request_outcome_bytes = 0
    queues: list[asyncio.Queue] = []
    yield queues
    for queue in queues:
        _disconnect(queue)
    events_stream._request_outcomes.clear()
    events_stream._request_outcome_bytes = 0


@pytest.fixture
def tab(_clean_outcomes):
    def connect(client_id: str) -> asyncio.Queue:
        queue = _connect(client_id)
        _clean_outcomes.append(queue)
        return queue

    return connect


def test_a_reply_missed_while_the_stream_was_down_is_redelivered_to_the_same_tab(tab):
    first = tab("a")
    events_stream.broadcast_ui_event(
        "agents.snapshot", {"agents": [1]}, request_id="r1", client_id="a"
    )
    sent = _drain(first)[0]
    _disconnect(first)
    # The reply to the next request is sent while the tab has no stream at all.
    events_stream.broadcast_ui_event(
        "agents.snapshot", {"agents": [2]}, request_id="r2", client_id="a"
    )

    reconnected = tab("a")
    replayed, unknown, lost = events_stream.replay_request_outcomes("a", ["r1", "r2"])

    assert (sorted(replayed), unknown, lost) == (["r1", "r2"], [], [])
    got = {envelope.payload["request_id"]: envelope for envelope in _drain(reconnected)}
    assert got["r1"].id == sent.id
    assert got["r2"].payload["agents"] == [2]


def test_one_tab_cannot_read_another_tabs_reply_and_unknown_ids_are_reported(tab):
    tab("a")
    other = tab("b")
    events_stream.broadcast_ui_event(
        "agents.snapshot", {"agents": [1]}, request_id="r1", client_id="a"
    )

    replayed, unknown, lost = events_stream.replay_request_outcomes("b", ["r1", "never-seen"])

    assert replayed == [] and lost == []
    assert unknown == ["r1", "never-seen"]
    assert _drain(other) == []


def test_a_reply_with_no_single_audience_can_be_replayed_to_whoever_asks(tab):
    events_stream.broadcast_ui_event(
        "agents.snapshot", {"agents": []}, request_id="r1", topic="agents"
    )
    asking = tab("b")

    replayed, _, _ = events_stream.replay_request_outcomes("b", ["r1"])

    assert replayed == ["r1"] and len(_drain(asking)) == 1


def test_replaying_is_repeatable_until_the_record_expires(tab):
    events_stream.broadcast_ui_event("agents.snapshot", {}, request_id="r1", client_id="a")
    queue = tab("a")

    assert events_stream.replay_request_outcomes("a", ["r1"])[0] == ["r1"]
    assert events_stream.replay_request_outcomes("a", ["r1"])[0] == ["r1"]
    assert len(_drain(queue)) == 2


def test_events_without_a_request_are_not_recorded(tab):
    tab("a")
    events_stream.broadcast_ui_event("inbox.message.upserted", {"id": "m"}, topic="inbox")
    events_stream.broadcast_ui_event("status.updated", {}, topic="dashboard")

    assert events_stream._request_outcomes == {}


def test_a_reply_too_large_to_keep_is_reported_as_lost_not_unknown(tab, monkeypatch):
    monkeypatch.setattr(events_stream, "_OUTCOME_MAX_ENVELOPE_BYTES", 200)
    events_stream.broadcast_ui_event(
        "workflow_runs.snapshot", {"runs": ["x" * 500]}, request_id="big", client_id="a"
    )
    events_stream.broadcast_ui_event("agents.snapshot", {"n": 1}, request_id="small", client_id="a")
    queue = tab("a")

    replayed, unknown, lost = events_stream.replay_request_outcomes("a", ["big", "small"])

    assert (replayed, unknown, lost) == (["small"], [], ["big"])
    assert len(_drain(queue)) == 1
    assert events_stream._request_outcome_bytes == events_stream._request_outcomes["small"].size


def test_replies_older_than_the_record_are_forgotten(tab, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(events_stream.time, "monotonic", lambda: clock["now"])
    events_stream.broadcast_ui_event("agents.snapshot", {}, request_id="old", client_id="a")
    clock["now"] += events_stream._OUTCOME_TTL_SECONDS + 1
    events_stream.broadcast_ui_event("agents.snapshot", {}, request_id="new", client_id="a")
    tab("a")

    replayed, unknown, _ = events_stream.replay_request_outcomes("a", ["old", "new"])

    assert replayed == ["new"] and unknown == ["old"]


def test_the_record_is_bounded_by_total_size_and_drops_the_oldest_first(tab, monkeypatch):
    events_stream.broadcast_ui_event(
        "agents.snapshot", {"p": "x" * 100}, request_id="probe", client_id="a"
    )
    one = events_stream._request_outcomes["probe"].size
    events_stream._request_outcomes.clear()
    events_stream._request_outcome_bytes = 0
    monkeypatch.setattr(events_stream, "_OUTCOME_BUDGET_BYTES", one * 2 + 10)

    for name in ("first", "second", "third"):
        events_stream.broadcast_ui_event(
            "agents.snapshot", {"p": "x" * 100}, request_id=name, client_id="a"
        )

    assert list(events_stream._request_outcomes) == ["second", "third"]
    assert events_stream._request_outcome_bytes == sum(
        outcome.size for outcome in events_stream._request_outcomes.values()
    )


def test_a_tab_with_a_full_queue_is_told_the_reply_is_unknown_not_replayed():
    events_stream.broadcast_ui_event("agents.snapshot", {}, request_id="r1", client_id="a")
    full: asyncio.Queue = asyncio.Queue(maxsize=1)
    full.put_nowait({"type": "filler"})
    events_stream._ui_stream_subscribers[full] = None
    events_stream._ui_stream_clients[full] = "a"
    try:
        replayed, unknown, _ = events_stream.replay_request_outcomes("a", ["r1"])
    finally:
        _disconnect(full)

    assert replayed == [] and unknown == ["r1"]


def test_the_resolve_route_redelivers_to_the_asking_tab_and_reports_the_boot(tab):
    events_stream.broadcast_ui_event(
        "agents.snapshot", {"agents": [1]}, request_id="r1", client_id="a"
    )
    queue = tab("a")

    response = TestClient(app).post(
        "/api/ui/stream/resolve", json={"client_id": "a", "request_ids": ["r1", "r2"]}
    )

    assert response.status_code == 200
    assert response.json() == {
        "boot_id": events_stream.BOOT_ID,
        "replayed": ["r1"],
        "unknown": ["r2"],
        "lost": [],
    }
    assert [envelope.payload["request_id"] for envelope in _drain(queue)] == ["r1"]


def test_the_resolve_route_needs_a_live_stream_and_a_sane_request(tab):
    tab("a")
    client = TestClient(app)

    assert (
        client.post(
            "/api/ui/stream/resolve", json={"client_id": "ghost", "request_ids": ["r"]}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/ui/stream/resolve", json={"client_id": "a", "request_ids": []}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/ui/stream/resolve",
            json={"client_id": "a", "request_ids": [str(n) for n in range(65)]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/ui/stream/resolve", json={"client_id": "a", "request_ids": ["x" * 65]}
        ).status_code
        == 422
    )


@pytest.mark.asyncio
async def test_connection_ready_carries_the_boot_id_so_a_restart_is_detectable(monkeypatch):
    monkeypatch.setattr(monitoring_status, "start_status_monitor", lambda: None)
    events_stream._sse_shutdown_event = None
    generator = events_routes._generate_ui_stream()
    try:
        ready = await generator.__anext__()
    finally:
        await generator.aclose()
        events_stream._sse_shutdown_event = None

    assert ready.data["type"] == "connection.ready"
    assert ready.data["payload"]["boot_id"] == events_stream.BOOT_ID
