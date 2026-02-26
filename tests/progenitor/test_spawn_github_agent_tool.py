from unittest.mock import MagicMock

import pytest

from app.progenitor.tools.protocol import ToolContext
from app.progenitor.tools.spawn_github_agent import SpawnGithubAgentTool


@pytest.mark.asyncio
async def test_spawn_github_agent_requires_request() -> None:
    tool = SpawnGithubAgentTool()
    ctx = ToolContext(
        tool_registry=MagicMock(),
        llm_client=MagicMock(),
        user_input="",
        tool_args={},
    )

    result = await tool.execute(ctx)
    assert result.success is False
    assert "Missing request text" in str(result.error)


@pytest.mark.asyncio
async def test_spawn_github_agent_fails_on_auth(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    tool = SpawnGithubAgentTool()

    async def _fake_run_gh(args, cwd=None, timeout=60.0):  # noqa: ANN001
        if args[:2] == ["auth", "status"]:
            return {"returncode": 1, "stdout": "", "stderr": "not logged in"}
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("app.progenitor.tools.spawn_github_agent._run_gh", _fake_run_gh)
    monkeypatch.setenv("PROGENITOR_GH_AGENT_CWD", str(tmp_path))

    ctx = ToolContext(
        tool_registry=MagicMock(),
        llm_client=MagicMock(),
        user_input="spawn a github agent to update docs",
        tool_args={},
    )
    result = await tool.execute(ctx)
    assert result.success is False
    assert "not authenticated" in str(result.error).lower()


@pytest.mark.asyncio
async def test_spawn_github_agent_dispatch_success(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    tool = SpawnGithubAgentTool()

    async def _fake_run_gh(args, cwd=None, timeout=60.0):  # noqa: ANN001
        if args[:2] == ["auth", "status"]:
            return {"returncode": 0, "stdout": "ok", "stderr": ""}
        if args[:2] == ["workflow", "run"]:
            return {"returncode": 0, "stdout": "dispatched", "stderr": ""}
        if args[:2] == ["run", "list"]:
            return {
                "returncode": 0,
                "stdout": (
                    '[{"databaseId":123,"url":"https://github.com/org/repo/actions/runs/123",'
                    '"status":"queued","conclusion":null}]'
                ),
                "stderr": "",
            }
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("app.progenitor.tools.spawn_github_agent._run_gh", _fake_run_gh)
    monkeypatch.setenv("PROGENITOR_GH_AGENT_CWD", str(tmp_path))

    ctx = ToolContext(
        tool_registry=MagicMock(),
        llm_client=MagicMock(),
        user_input="spawn github agent to refactor telemetry",
        tool_args={"workflow": "agents.yml", "ref": "main"},
    )
    result = await tool.execute(ctx)

    assert result.success is True
    assert result.data is not None
    assert result.data["dispatched"] is True
    assert result.data["workflow"] == "agents.yml"
    assert result.data["latest_run"]["databaseId"] == 123
