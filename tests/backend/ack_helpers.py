"""Shared assertions for backend finite-request acknowledgement envelopes."""

from collections.abc import Mapping
from typing import Any


def assert_acknowledgement_body(
    body: Mapping[str, Any],
    event_types: list[str] | None = None,
    request_id: str | None = None,
) -> Mapping[str, Any]:
    """Assert the data-free acknowledgement envelope returned by finite routes."""
    assert set(body) == {"request_id", "event_types"}
    assert isinstance(body["request_id"], str) and body["request_id"]
    assert isinstance(body["event_types"], list) and body["event_types"]
    assert all(isinstance(event_type, str) and event_type for event_type in body["event_types"])
    if request_id is not None:
        assert body["request_id"] == request_id
    if event_types is not None:
        assert body["event_types"] == event_types
    return body


def assert_acknowledged(
    response: Any,
    event_types: list[str],
    request_id: str | None = None,
    *,
    check_request_id_header: bool = False,
) -> Mapping[str, Any]:
    """Assert a 202 acknowledgement and optionally correlate its request header."""
    assert response.status_code == 202
    body = assert_acknowledgement_body(response.json(), event_types, request_id)
    if check_request_id_header:
        assert request_id is not None and response.headers["X-Operation-Id"] == request_id
    return body
