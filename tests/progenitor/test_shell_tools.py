from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.progenitor.tools import shell as shell_module
from app.progenitor.tools.protocol import ToolContext
from app.progenitor.tools.shell import ShellCheckTool, ShellExecTool


@pytest.mark.asyncio
async def test_shell_check_reports_state():
    tool = ShellCheckTool()
    result = await tool.execute(ToolContext(tool_registry=object(), llm_client=object()))
    assert result.success is True
    assert result.data is not None
    assert "shell" in result.data


@pytest.mark.asyncio
async def test_shell_exec_rejects_on_windows(monkeypatch):
    monkeypatch.setattr(shell_module, "os", SimpleNamespace(name="nt"))
    tool = ShellExecTool()
    ctx = ToolContext(tool_registry=object(), llm_client=object(), user_input="echo hi")
    result = await tool.execute(ctx)
    assert result.success is False
    assert "posix" in (result.error or "").lower()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="shell_exec only supports POSIX shells")
async def test_shell_exec_runs_command():
    tool = ShellExecTool()
    ctx = ToolContext(
        tool_registry=object(), llm_client=object(), user_input="echo progenitor_shell_test"
    )
    result = await tool.execute(ctx)
    assert result.success is True
    assert result.data is not None
    assert result.data.get("returncode") == 0
    assert "progenitor_shell_test" in (result.data.get("stdout") or "")
