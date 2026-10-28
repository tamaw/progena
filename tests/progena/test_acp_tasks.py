"""Tests for ACP task helpers used by the backend supervisor."""

from __future__ import annotations

import pytest

from src.progena.acp.models import ACPTaskRecord, ACPTaskStatus
from src.progena.acp.tasks import (
    acp_config_from_agent_config,
    build_acp_completion_message,
    task_command,
    task_cwd,
)


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
