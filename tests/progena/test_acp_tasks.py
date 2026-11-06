"""Tests for ACP task helpers used by the backend supervisor."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import jsonschema
import pytest

from src.progena.acp.models import ACPTaskRecord, ACPTaskStatus
from src.progena.acp.tasks import (
    acp_config_from_agent_config,
    build_acp_completion_message,
    enqueue_acp_completion_followup,
    task_command,
    task_cwd,
)
from src.progena.documents.catalog import load_schema


def test_acp_config_requires_explicit_acp_settings() -> None:
    with pytest.raises(ValueError, match="missing its 'acp' configuration"):
        acp_config_from_agent_config({"name": "claude"})


def test_tcp_task_uses_requested_cwd_and_endpoint_label() -> None:
    config = acp_config_from_agent_config(
        {"name": "claude", "acp": {"host": "claude", "port": 3001, "model": "opus"}}
    )
    assert task_cwd(config, "/app/repos/card") == "/app/repos/card"
    assert task_command(config) == "claude:3001"


def test_interrupted_task_is_terminal_and_reports_reason() -> None:
    task = ACPTaskRecord(
        task_id="task-1",
        agent_name="claude",
        prompt="implement it",
        status=ACPTaskStatus.INTERRUPTED,
        last_error="backend restarted",
    )
    assert task.is_terminal
    assert "backend restarted" in build_acp_completion_message(task)


def test_acp_task_normalizes_legacy_naive_activity_timestamp() -> None:
    task = ACPTaskRecord(
        task_id="task-1",
        agent_name="claude",
        progress={"last_activity_at": "2026-10-29T12:00:00"},
    )

    assert task.progress["last_activity_at"] == "2026-10-29T12:00:00+00:00"


class _StateManager:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict[str, Any]]] = []

    async def update_acp_task(self, task_id: str, update: dict[str, Any]) -> None:
        self.updates.append((task_id, update))


class _RejectedQueues:
    async def enqueue_input(self, _content: str, *, metadata: dict[str, Any]) -> bool:
        return False


@pytest.mark.asyncio
async def test_rejected_completion_followup_does_not_persist_acknowledgement() -> None:
    record = ACPTaskRecord(
        task_id="task-1",
        agent_name="claude",
        request_metadata={"thread_id": "thread-1"},
        status=ACPTaskStatus.COMPLETED,
    )
    state_manager = _StateManager()

    queued = await enqueue_acp_completion_followup(
        state_manager,
        _RejectedQueues(),
        record,
        event_identity="terminal:0:completed",
    )

    assert not queued
    assert state_manager.updates == []


def _completed_task_payload(evidence: dict[str, object] | None) -> dict[str, object]:
    return ACPTaskRecord(
        task_id="task-1",
        agent_name="claude",
        prompt="implement it",
        status=ACPTaskStatus.COMPLETED,
        turn=1,
        output="Completed.",
        evidence=evidence,
    ).model_dump(mode="json")


def test_acp_task_schema_accepts_produced_and_legacy_evidence_references() -> None:
    schema = load_schema("acp_task")
    native_evidence = {
        "provider": "claude_native",
        "session_id": "11111111-1111-1111-1111-111111111111",
        "cwd": "/app/repos/project",
        "line_start": 4,
        "line_end": 8,
        "byte_start": 128,
        "byte_end": 512,
        "block_size": 1048576,
        "block_sha256": ["a" * 64],
        "verified": True,
        "final_output_available": True,
        "final_message_line": 7,
    }
    generic_evidence = {"output_truncated": True, "final_output_available": False}
    partial_native_evidence = {
        **native_evidence,
        "final_output_available": False,
        "output_truncated": True,
    }

    assert schema["properties"]["evidence"]["readOnly"] is True
    jsonschema.validate(_completed_task_payload(native_evidence), schema)
    jsonschema.validate(_completed_task_payload(partial_native_evidence), schema)
    jsonschema.validate(_completed_task_payload(generic_evidence), schema)
    jsonschema.validate(_completed_task_payload(None), schema)
    legacy_payload = _completed_task_payload(None)
    legacy_payload.pop("evidence")
    jsonschema.validate(legacy_payload, schema)


@pytest.mark.parametrize(
    "evidence",
    [
        {
            "provider": "claude_native",
            "session_id": "11111111-1111-1111-1111-111111111111",
            "line_start": 0,
            "byte_start": 0,
            "line_end": 1,
        },
        {"output_truncated": False, "final_output_available": False},
        {"output_truncated": True, "final_output_available": False, "extra": "nope"},
    ],
)
def test_acp_task_schema_rejects_malformed_evidence_references(
    evidence: dict[str, object],
) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_completed_task_payload(evidence), load_schema("acp_task"))


def test_acp_task_schema_rejects_unrelated_persisted_fields() -> None:
    payload = deepcopy(_completed_task_payload(None))
    payload["unrelated"] = "rejected"

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, load_schema("acp_task"))
