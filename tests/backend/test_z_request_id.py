"""End-to-end coverage for finite-request correlation over canonical SSE."""

import json
import socket
import threading
import time
from contextlib import contextmanager
from collections.abc import Iterator

import httpx
import uvicorn

from app.backend.main import app


@contextmanager
def _live_backend() -> Iterator[str]:
    """Run the ASGI app on a loopback socket so SSE is not transport-buffered."""
    from app.backend import ui_routes

    # Earlier TestClient lifespans close their SSE connections. A live server
    # needs a fresh event bound to its own loop rather than that closed lifecycle.
    ui_routes._sse_shutdown_event = None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        host, port = probe.getsockname()

    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, lifespan="off", log_level="critical")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started:
        assert thread.is_alive(), "Uvicorn stopped before accepting connections"
        assert time.monotonic() < deadline, "Timed out starting Uvicorn"
        time.sleep(0.01)

    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        ui_routes._sse_shutdown_event = None
        assert not thread.is_alive(), "Uvicorn did not stop"


def _next_sse_frame(lines: Iterator[str]) -> tuple[str, dict[str, object]]:
    """Decode one complete SSE frame from the endpoint's wire representation."""
    event_name: str | None = None
    data_lines: list[str] = []
    for line in lines:
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data_lines.append(line.removeprefix("data: "))
        elif line == "" and event_name is not None:
            return event_name, json.loads("\n".join(data_lines))
    raise AssertionError("SSE stream closed before a complete event frame")


def test_finite_ui_request_returns_client_id_and_matching_stream_event():
    """The finite acknowledgement and serialized canonical frame share the client ID."""
    request_id = "client-generated-operation-id"

    with _live_backend() as base_url, httpx.Client(base_url=base_url, timeout=5) as client:
        with client.stream("GET", "/api/ui/stream") as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            lines = stream.iter_lines()
            ready_name, ready = _next_sse_frame(lines)
            assert ready_name == "ui.event"
            assert ready["type"] == "connection.ready"

            response = client.get(
                "/api/ui/widgets",
                headers={"X-Request-ID": request_id},
            )
            event_name, event = _next_sse_frame(lines)

    assert response.status_code == 202
    assert response.headers["X-Request-ID"] == request_id
    assert response.json() == {
        "request_id": request_id,
        "event_types": ["widgets.updated"],
    }
    assert event_name == "ui.event"
    assert event["type"] == "widgets.updated"
    assert event["payload"]["request_id"] == request_id
    assert isinstance(event["payload"]["items"], list)
