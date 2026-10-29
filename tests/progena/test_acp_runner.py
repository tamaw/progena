"""Tests for the generic ACP client subsystem (runner, client, arg builders)."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

from acp import text_block
from acp.schema import AgentMessageChunk, AgentThoughtChunk, PermissionOption, ToolCallStart

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


async def test_session_client_bounds_native_stream_collection():
    client = ACPSessionClient(run_id="t", max_output_chars=5, max_metadata_entries=2)
    for text in ("abc", "def", "ghi"):
        await client.session_update("s", _message_chunk(text))
    for index in range(3):
        await client.session_update(
            "s",
            AgentThoughtChunk(
                session_update="agent_thought_chunk", content=text_block(f"thought-{index}")
            ),
        )
        await client.session_update(
            "s",
            ToolCallStart(
                session_update="tool_call",
                tool_call_id=f"tc{index}",
                title=f"tool-{index}",
                kind="execute",
            ),
        )

    assert client.get_output() == "abcde"
    assert client.output_char_count == 9
    assert client.output_truncated is True
    assert client.thoughts == ["thought-1", "thought-2"]
    assert [call["name"] for call in client.tool_calls] == ["tool-1", "tool-2"]
    assert client.tool_call_count == 3


def test_plan_approval_prefers_documented_keep_context_option() -> None:
    response = ACPSessionClient._allow_plan_response(  # noqa: SLF001
        [
            PermissionOption(
                option_id="exit-plan-clear-auto",
                name="clear context",
                kind="allow_always",
            ),
            PermissionOption(
                option_id="exit-plan-default",
                name="keep context",
                kind="allow_once",
            ),
        ]
    )

    assert response.outcome.option_id == "exit-plan-default"


async def test_raw_claude_sdk_notification_reports_private_transcript_session() -> None:
    from acp.router import MessageRouter

    updates: list[dict[str, object]] = []
    client = ACPSessionClient(on_progress=updates.append)
    client.bind_session("public-acp-session")
    router = MessageRouter()
    router.handle_extension_notification(client.ext_notification)

    await router(
        "_claude/sdkMessage",
        {
            "sessionId": "public-acp-session",
            "message": {"session_id": "11111111-1111-1111-1111-111111111111"},
        },
        True,
    )
    await router(
        "_claude/sdkMessage",
        {
            "sessionId": "public-acp-session",
            "message": {
                "session_id": "33333333-3333-3333-3333-333333333333",
                "parent_tool_use_id": "toolu_nested",
            },
        },
        True,
    )
    await router(
        "_claude/sdkMessage",
        {
            "sessionId": "another-public-session",
            "message": {"session_id": "22222222-2222-2222-2222-222222222222"},
        },
        True,
    )
    for native_id in [
        "11111111-1111-1111-1111-111111111111",
        "44444444-4444-4444-4444-444444444444",
        "44444444-4444-4444-4444-444444444444",
    ]:
        await router(
            "_claude/sdkMessage",
            {"sessionId": "public-acp-session", "message": {"session_id": native_id}},
            True,
        )

    assert updates == [
        {
            "kind": "native_session",
            "session_id": "11111111-1111-1111-1111-111111111111",
            "acp_session_id": "public-acp-session",
        },
        {
            "kind": "native_session",
            "session_id": "44444444-4444-4444-4444-444444444444",
            "acp_session_id": "public-acp-session",
        },
    ]


async def test_late_verified_evidence_bounds_already_collected_output():
    client = ACPSessionClient(run_id="late-evidence")
    await client.session_update("s", _message_chunk("abcdef"))
    assert client.get_output() == "abcdef"
    client.set_output_limit(5)
    await client.session_update("s", _message_chunk("more output"))
    assert client.get_output() == "abcde"
    assert client.output_char_count == len("abcdefmore output")
    assert client.output_truncated


async def test_generic_capture_stays_lossless_after_memory_preview_fills(tmp_path, monkeypatch):
    monkeypatch.setenv("PROGENA_ACP_CAPTURE_DIR", str(tmp_path))
    client = ACPSessionClient(run_id="generic-capture")
    try:
        prefix = "α" * 20_000
        await client.session_update("s", _message_chunk(prefix))
        assert client.get_output() == prefix
        await client.session_update("s", _message_chunk("\nfinal evidence: passed"))
        assert client.get_output() == prefix + "\nfinal evidence: passed"
        assert sum(len(part) for part in client.text_parts) <= 16_384
        assert not client.output_truncated
    finally:
        client.close_output()
    assert list(tmp_path.iterdir()) == []


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
