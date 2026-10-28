"""Tests for A2AClient message sends and cancellation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.progena.a2a.client import A2AClient
from src.progena.a2a.models import A2ATaskRecord
from src.progena.a2a.payloads import build_task_response


@pytest.mark.asyncio
async def test_send_message_waits_for_input_required(monkeypatch) -> None:
    client = A2AClient()
    submitted = {"kind": "task", "id": "task-1", "status": {"state": "working"}}
    required = {"kind": "task", "id": "task-1", "status": {"state": "input-required"}}

    async def _stream():
        yield submitted

    class _Sender:
        def send_message(self, *_args, **_kwargs):
            return _stream()

    class _SendContext:
        async def __aenter__(self):
            return _Sender()

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(
        client, "get_task", AsyncMock(return_value={"success": True, "result": required})
    )
    monkeypatch.setattr(client, "_sdk_client", lambda *_args, **_kwargs: _SendContext())
    result = await client.send_message({"url": "http://example.test"}, "continue", blocking=True)

    assert result["success"] is True
    assert result["result"]["status"]["state"] == "input-required"


@pytest.mark.asyncio
async def test_send_message_includes_follow_up_identifiers(monkeypatch) -> None:
    client = A2AClient()
    captured = {}

    class _Sender:
        def send_message(self, message, **_kwargs):
            captured["message"] = message

            async def _stream():
                yield {"kind": "task", "id": "task-1", "status": {"state": "submitted"}}

            return _stream()

    class _Context:
        async def __aenter__(self):
            return _Sender()

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(client, "_sdk_client", lambda *_args, **_kwargs: _Context())
    result = await client.send_message(
        {"url": "http://example.test"},
        "answer",
        task_id="task-1",
        context_id="ctx-1",
    )

    assert result["success"] is True
    assert captured["message"].task_id == "task-1"
    assert captured["message"].context_id == "ctx-1"
    assert captured["message"].reference_task_ids == ["task-1"]


def test_task_response_marks_legacy_naive_timestamps_as_utc() -> None:
    response = build_task_response(
        A2ATaskRecord(
            task_id="task-1",
            agent_name="agent",
            created_at="2026-10-29T12:00:00",
            updated_at="2026-10-29T12:00:00",
        )
    )

    assert response["created_at"] == "2026-10-29T12:00:00+00:00"
