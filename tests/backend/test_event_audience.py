"""UI event audience: tab-scoped by default, global only for app chrome."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from src.backend.events import contracts as events_contracts
from src.backend.events import stream as events_stream

_ROOT = Path(__file__).resolve().parents[2]
_BROADCAST_NAMES = {"broadcast_ui_event", "_broadcast", "broadcast"}


def _untargeted_event_names() -> set[str]:
    names: set[str] = set()
    for base in ("src/backend", "src/progena"):
        for path in (_ROOT / base).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                if getattr(func, "id", getattr(func, "attr", "")) not in _BROADCAST_NAMES:
                    continue
                if any(keyword.arg in {"client_id", "topic"} for keyword in node.keywords):
                    continue
                first = node.args[0]
                if (
                    isinstance(first, ast.Attribute)
                    and first.attr in events_contracts.UiEvent.__members__
                ):
                    names.add(first.attr)
    return names


def test_untargeted_events_are_app_chrome():
    chrome = {events_contracts.UiEvent(value).name for value in events_stream.GLOBAL_UI_EVENTS}

    unexpected = _untargeted_event_names() - chrome
    assert not unexpected, (
        f"{sorted(unexpected)} are broadcast to every tab. Reply to one tab with client_id= "
        "or fan out with topic=, or add genuine app chrome to GLOBAL_UI_EVENTS."
    )


@pytest.fixture
def tabs():
    queues = {name: asyncio.Queue() for name in ("a", "b", "c")}
    for client_id, queue in queues.items():
        events_stream._ui_stream_subscribers[queue] = None
        events_stream._ui_stream_clients[queue] = client_id
    events_stream._ui_stream_topics.clear()
    replay_before = list(events_stream._ui_event_replay)
    yield queues
    for queue in queues.values():
        events_stream._ui_stream_subscribers.pop(queue, None)
        events_stream._ui_stream_clients.pop(queue, None)
    events_stream._ui_stream_topics.clear()
    events_stream._ui_event_replay[:] = replay_before


def _received(queues):
    return {name: queue.qsize() for name, queue in queues.items()}


def test_topic_event_reaches_only_subscribed_tabs_and_is_not_replayed(tabs):
    events_stream.subscribe_topic("a", "t")
    events_stream.subscribe_topic("b", "other")
    replayed = len(events_stream._ui_event_replay)

    events_stream.broadcast_ui_event("agent.deleted", {}, topic="t")

    assert _received(tabs) == {"a": 1, "b": 0, "c": 0}
    assert len(events_stream._ui_event_replay) == replayed


def test_unsubscribe_and_clear_stop_delivery(tabs):
    events_stream.subscribe_topic("a", "t")
    events_stream.subscribe_topic("b", "t")
    events_stream.unsubscribe_topic("a", "t")
    events_stream.clear_topics("b")

    events_stream.broadcast_ui_event("agent.deleted", {}, topic="t")

    assert _received(tabs) == {"a": 0, "b": 0, "c": 0}
    assert events_stream._ui_stream_topics == {}


def test_untargeted_event_reaches_every_tab_and_is_replayed(tabs):
    replayed = len(events_stream._ui_event_replay)

    events_stream.broadcast_ui_event("status.updated", {})

    assert _received(tabs) == {"a": 1, "b": 1, "c": 1}
    assert len(events_stream._ui_event_replay) == replayed + 1


def test_one_audience_only(tabs):
    with pytest.raises(ValueError):
        events_stream.broadcast_ui_event("agent.deleted", {}, client_id="a", topic="t")


def test_topic_route_registers_acknowledges_to_requester_and_releases(tabs):
    from ack_helpers import assert_acknowledged
    from fastapi.testclient import TestClient

    from src.backend.main import app

    client = TestClient(app)
    watch = client.put(
        "/api/ui/stream/topics",
        json={"client_id": "a", "topic": "chat:one", "watching": True},
        headers={"X-Operation-Id": "watch-request"},
    )

    assert_acknowledged(watch, ["stream.topic.updated"], "watch-request")
    assert events_stream.has_topic("a", "chat:one")
    assert _received(tabs) == {"a": 1, "b": 0, "c": 0}

    client.put(
        "/api/ui/stream/topics", json={"client_id": "a", "topic": "chat:one", "watching": False}
    )
    assert not events_stream.has_topic("a", "chat:one")


@pytest.mark.parametrize("topic", ["Chat", "chat one", "", "x" * 65])
def test_topic_route_rejects_malformed_topics(tabs, topic):
    from fastapi.testclient import TestClient

    from src.backend.main import app

    response = TestClient(app).put(
        "/api/ui/stream/topics", json={"client_id": "a", "topic": topic, "watching": True}
    )

    assert response.status_code == 422


def test_topic_route_requires_a_live_stream_and_caps_topics(tabs):
    from fastapi.testclient import TestClient

    from src.backend.main import app

    client = TestClient(app)
    nobody = client.put(
        "/api/ui/stream/topics", json={"client_id": "ghost", "topic": "t", "watching": True}
    )
    for index in range(32):
        events_stream.subscribe_topic("a", f"t{index}")
    over = client.put(
        "/api/ui/stream/topics", json={"client_id": "a", "topic": "one-more", "watching": True}
    )

    assert nobody.status_code == 409
    assert over.status_code == 429


def test_route_events_also_light_the_nav_dot_in_every_tab_but_replies_do_not(tabs):
    events_stream.subscribe_topic("a", "workflows")

    events_stream.broadcast_ui_event("workflow.updated", {}, topic="workflows")
    seen = {
        name: [q.get_nowait()["envelope"] for _ in range(q.qsize())] for name, q in tabs.items()
    }
    events_stream.broadcast_ui_event("workflows.snapshot", {}, client_id="b")
    reply = {
        name: [q.get_nowait()["envelope"] for _ in range(q.qsize())] for name, q in tabs.items()
    }

    assert [e.type for e in seen["a"]] == ["workflow.updated", "nav.activity"]
    assert [e.type for e in seen["b"]] == ["nav.activity"]
    assert seen["b"][0].payload == {"route": "/workflows"}
    assert [e.type for e in reply["b"]] == ["workflows.snapshot"]
    assert reply["a"] == [] and reply["c"] == []


def test_preferences_and_profiles_reads_reply_only_to_the_asking_tab(tabs):
    from fastapi.testclient import TestClient

    from src.backend.main import app

    client = TestClient(app)
    for path in ("/api/ui/preferences", "/api/ui/model-profiles"):
        response = client.get(f"{path}?client_id=b")

        assert response.status_code == 202
        assert _received(tabs) == {"a": 0, "b": 1, "c": 0}, path
        tabs["b"].get_nowait()


@pytest.fixture
def status_source(monkeypatch):
    from src.backend.monitoring import status as monitoring_status

    async def full_status():
        return {"loops": [{"name": "main"}], "cycle_count": 3}

    monkeypatch.setattr(monitoring_status, "_get_current_status", full_status)
    monkeypatch.setattr(monitoring_status, "_process_metrics", lambda: {"memory_mb": 99.0})
    return monitoring_status


def _types(queue):
    return [queue.get_nowait()["envelope"].type for _ in range(queue.qsize())]


def test_status_request_replies_only_to_the_asking_tab(tabs, status_source):
    from fastapi.testclient import TestClient

    from src.backend.main import app

    response = TestClient(app).get(
        "/api/ui/status?client_id=b", headers={"X-Operation-Id": "status-req"}
    )

    assert response.status_code == 202
    assert _received(tabs) == {"a": 0, "b": 1, "c": 0}
    envelope = tabs["b"].get_nowait()["envelope"]
    assert envelope.type == "status.updated" and envelope.payload["request_id"] == "status-req"
    assert envelope.payload["loops"] == [{"name": "main"}]


def test_status_request_needs_an_active_stream(tabs, status_source):
    from fastapi.testclient import TestClient

    from src.backend.main import app

    assert TestClient(app).get("/api/ui/status?client_id=ghost").status_code == 409


@pytest.mark.asyncio
async def test_runtime_status_goes_only_to_tabs_on_the_dashboard(tabs, status_source):
    events_stream.subscribe_topic("a", "dashboard")

    await status_source._broadcast_latest_status()

    assert _received(tabs) == {"a": 1, "b": 0, "c": 0}


@pytest.mark.asyncio
async def test_status_is_neither_computed_nor_sent_while_no_tab_watches_the_dashboard(
    tabs, status_source, monkeypatch
):
    computed = []

    async def counting_status():
        computed.append(1)
        return {}

    monkeypatch.setattr(status_source, "_get_current_status", counting_status)
    await status_source._broadcast_latest_status()

    assert computed == [] and _received(tabs) == {"a": 0, "b": 0, "c": 0}


@pytest.mark.asyncio
async def test_metrics_tick_reaches_dashboard_watchers_and_stops_when_they_leave(
    tabs, status_source, monkeypatch
):
    real_sleep = asyncio.sleep

    async def quick(_seconds):
        await real_sleep(0.01)

    monkeypatch.setattr(status_source.asyncio, "sleep", quick)
    events_stream.subscribe_topic("a", "dashboard")
    task = asyncio.create_task(status_source._status_monitor())
    await real_sleep(0.08)
    watching = _received(tabs)

    events_stream.unsubscribe_topic("a", "dashboard")
    for queue in tabs.values():
        while not queue.empty():
            queue.get_nowait()
    await real_sleep(0.08)
    after_leaving = _received(tabs)
    task.cancel()

    assert watching["a"] > 0 and watching["b"] == watching["c"] == 0
    assert after_leaving == {"a": 0, "b": 0, "c": 0}
