from __future__ import annotations

import os

import pytest

from app.progenitor.tools.protocol import ToolContext
from app.progenitor.tools.shell import ShellCheckTool, ShellExecTool


@pytest.mark.asyncio
async def test_shell_check_reports_state(monkeypatch):
    monkeypatch.delenv("PROGENITOR_ENABLE_SHELL_TOOLS", raising=False)
    tool = ShellCheckTool()
    result = await tool.execute(ToolContext(tool_registry=object(), llm_client=object()))
    assert result.success is True
    assert result.data is not None
    assert "enabled" in result.data
    assert "shell" in result.data


@pytest.mark.asyncio
async def test_shell_exec_disabled(monkeypatch):
    monkeypatch.delenv("PROGENITOR_ENABLE_SHELL_TOOLS", raising=False)
    tool = ShellExecTool()
    ctx = ToolContext(tool_registry=object(), llm_client=object(), user_input="echo hi")
    result = await tool.execute(ctx)
    assert result.success is False
    assert "disabled" in (result.error or "").lower()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="shell_exec only supports POSIX shells")
async def test_shell_exec_runs_command_when_enabled(monkeypatch):
    monkeypatch.setenv("PROGENITOR_ENABLE_SHELL_TOOLS", "true")
    tool = ShellExecTool()
    ctx = ToolContext(tool_registry=object(), llm_client=object(), user_input="echo progenitor_shell_test")
    result = await tool.execute(ctx)
    assert result.success is True
    assert result.data is not None
    assert result.data.get("returncode") == 0
    assert "progenitor_shell_test" in (result.data.get("stdout") or "")

