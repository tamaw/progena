"""Tests for gh_cli system tool."""

from unittest.mock import MagicMock

import pytest

from app.progenitor.tools.gh import GhCliTool
from app.progenitor.tools.protocol import ToolContext


@pytest.mark.asyncio
async def test_gh_cli_tool_executes_with_args(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run_gh(args, cwd=None, timeout=60.0):  # type: ignore[no-untyped-def]
        return {
            "returncode": 0,
            "stdout": f"ok:{' '.join(args)}@{cwd}",
            "stderr": "",
        }

    monkeypatch.setattr("app.progenitor.tools.gh._run_gh", _fake_run_gh)

    tool = GhCliTool()
    context = ToolContext(
        tool_registry=MagicMock(),
        llm_client=MagicMock(),
        tool_args={"args": ["auth", "status"]},
    )

    result = await tool.execute(context)
    assert result.success
    assert result.data is not None
    assert result.data["ok"] is True
    assert result.data["command"] == ["gh", "auth", "status"]


@pytest.mark.asyncio
async def test_gh_cli_tool_requires_args() -> None:
    tool = GhCliTool()
    context = ToolContext(
        tool_registry=MagicMock(),
        llm_client=MagicMock(),
        user_input="",
        tool_args={},
    )
    result = await tool.execute(context)
    assert not result.success
    assert "Missing gh arguments" in (result.error or "")


def test_gh_cli_tool_auto_discovery() -> None:
    from app.progenitor.tools.system_registry import get_system_tool

    tool = get_system_tool("gh_cli")
    assert tool is not None
    assert tool.name == "gh_cli"
