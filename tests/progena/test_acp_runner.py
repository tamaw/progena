"""Tests for the generic ACP client subsystem (runner, client, arg builders)."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

from acp import text_block
from acp.schema import AgentMessageChunk, AgentThoughtChunk, ToolCallStart

from src.progena.acp import runner as runner_module
from src.progena.acp.agents import build_agent_args, build_copilot_args
from src.progena.acp.client import ACPSessionClient
from src.progena.acp.runner import ACPRunner


def _message_chunk(text: str) -> AgentMessageChunk:
    return AgentMessageChunk(session_update="agent_message_chunk", content=text_block(text))


def test_build_copilot_args_includes_acp_yolo_model_and_dirs():
    args = build_copilot_args(model="gpt-5-mini", allowed_dirs=["/work"], flags=["--extra"])
    assert args[:2] == ["--acp", "--yolo"]
    assert "--model" in args and "gpt-5-mini" in args
    assert "--add-dir" in args and "/work" in args
    assert "--extra" in args


def test_build_agent_args_profile_raw_and_generic():
    # Known profile delegates to its builder.
    assert build_agent_args("copilot", model="m")[:2] == ["--acp", "--yolo"]
    # Raw args win verbatim.
    assert build_agent_args("copilot", raw_args=["--acp", "--x"]) == ["--acp", "--x"]
    # Unknown profile falls back to a bare --acp plus explicit flags/dirs.
    generic = build_agent_args("mystery", flags=["--foo"], allowed_dirs=["/d"])
    assert generic == ["--acp", "--foo", "--add-dir", "/d"]


async def test_session_client_collects_text_thoughts_and_tool_calls():
    client = ACPSessionClient(run_id="t")
    await client.session_update("s", _message_chunk("hello "))
    await client.session_update("s", _message_chunk("world"))
    await client.session_update(
        "s", AgentThoughtChunk(session_update="agent_thought_chunk", content=text_block("thinking"))
    )
    await client.session_update(
        "s",
        ToolCallStart(
            session_update="tool_call", tool_call_id="tc1", title="run_tests", kind="execute"
        ),
    )
    assert client.get_output() == "hello world"
    assert client.thoughts == ["thinking"]
    assert [tc["name"] for tc in client.tool_calls] == ["run_tests"]


async def test_runner_missing_command_degrades_gracefully():
    result = await ACPRunner("definitely-not-real-xyz", ["--acp"], timeout=2).run(
        "hi", "/tmp", run_id="t"
    )
    assert result.success is False
    assert "not found" in (result.error or "").lower()


async def test_runner_happy_path_with_mocked_spawn(monkeypatch):
    """A mocked ACP agent streams two message chunks then ends the turn."""

    @contextlib.asynccontextmanager
    async def fake_spawn(client: Any, command: str, *args: str, **kwargs: Any):
        async def initialize(**_k: Any) -> Any:
            return SimpleNamespace()

        async def new_session(**_k: Any) -> Any:
            return SimpleNamespace(session_id="sess-1")

        async def prompt(prompt: Any, session_id: str, **_k: Any) -> Any:
            await client.session_update(session_id, _message_chunk("done"))
            return SimpleNamespace(stop_reason="end_turn")

        conn = SimpleNamespace(initialize=initialize, new_session=new_session, prompt=prompt)
        yield conn, SimpleNamespace(pid=4321)

    monkeypatch.setattr(runner_module, "spawn_agent_process", fake_spawn)

    result = await ACPRunner("copilot", ["--acp"], timeout=5).run("build", "/tmp", run_id="t")
    assert result.success is True
    assert result.output == "done"
    assert result.session_id == "sess-1"
    assert result.stop_reason == "end_turn"


async def test_runner_resumes_existing_session_with_same_id(monkeypatch):
    resumed: list[tuple[str, str]] = []

    @contextlib.asynccontextmanager
    async def fake_spawn(client: Any, command: str, *args: str, **kwargs: Any):
        async def initialize(**_k: Any) -> Any:
            return SimpleNamespace()

        async def resume_session(cwd: str, session_id: str, **_k: Any) -> Any:
            resumed.append((cwd, session_id))
            return SimpleNamespace(session_id=session_id)

        async def prompt(prompt: Any, session_id: str, **_k: Any) -> Any:
            await client.session_update(session_id, _message_chunk("continued"))
            return SimpleNamespace(stop_reason="end_turn")

        conn = SimpleNamespace(
            initialize=initialize,
            resume_session=resume_session,
            prompt=prompt,
        )
        yield conn, SimpleNamespace(pid=4321)

    monkeypatch.setattr(runner_module, "spawn_agent_process", fake_spawn)
    result = await ACPRunner("copilot", ["--acp"], timeout=5).run(
        "continue", "/tmp/worktree", run_id="t", session_id="sess-existing"
    )
    assert resumed == [("/tmp/worktree", "sess-existing")]
    assert result.session_id == "sess-existing"
    assert result.output == "continued"
