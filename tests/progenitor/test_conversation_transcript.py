"""IRC rendering of chat messages for the /sessions/ mount."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.progenitor.sessions.transcript import (
    ThreadMessages,
    format_log,
    render_transcript,
)

T0 = datetime(2026, 10, 12, 1, 0, 0, tzinfo=UTC)
MODEL = {"model_provider": "openai"}


def _thread(*messages: BaseMessage, nick: str = "progenitor") -> ThreadMessages:
    """Messages committed one second apart, in order."""
    for index, message in enumerate(messages):
        message.id = message.id or f"m{index}-{nick}"
    times = {
        message.id: T0 + timedelta(seconds=i) for i, message in enumerate(messages) if message.id
    }
    return ThreadMessages(messages=list(messages), committed_at=times, nick=nick)


def _lines(root: ThreadMessages, subagents: dict[str, ThreadMessages] | None = None) -> list[str]:
    transcript = render_transcript(
        root, subagents=subagents or {}, channel="abcd1234", user_nick="Alex"
    )
    return [line.text for line in transcript.lines]


def _call(name: str, call_id: str, **args: object) -> dict[str, object]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def test_direct_agent_turns_join_before_the_address_and_part_when_the_user_moves_on() -> None:
    lines = _lines(
        _thread(
            HumanMessage("@greeter hi there"),
            AIMessage("Ahoy!", response_metadata={"display_agent_name": "greeter"}),
            HumanMessage("@greeter bye"),
            AIMessage("Farewell!", response_metadata={"display_agent_name": "greeter"}),
            HumanMessage("what did it say?"),
            AIMessage("It said farewell.", response_metadata=MODEL),
        )
    )
    assert lines == [
        "-!- greeter [a2a] has joined #abcd1234",
        "<Alex> greeter: hi there",
        "<@greeter> Ahoy!",
        "<Alex> greeter: bye",
        "<@greeter> Farewell!",
        "-!- greeter [a2a] has left #abcd1234",
        "<Alex> what did it say?",
        "<@progenitor> It said farewell.",
    ]


def test_failed_direct_agent_calls_never_join() -> None:
    lines = _lines(
        _thread(
            HumanMessage("@nobody are you there?"),
            AIMessage("Direct agent call failed: unknown agent 'nobody'."),
            HumanMessage("@greeter hi"),
            AIMessage("Direct agent call failed: Agent is in failed state: None."),
        )
    )
    assert lines == [
        "<Alex> nobody: are you there?",
        "-!- nobody: No such nick/channel",
        "<Alex> greeter: hi",
        "-!- greeter: Agent is in failed state: None",
    ]


def test_failed_task_subagent_parts_once_with_the_reason_and_unnamed_child_uses_its_type() -> None:
    root = _thread(
        HumanMessage("which repos?"),
        AIMessage(
            "",
            tool_calls=[
                _call("task", "call-1", subagent_type="repo-manager", description="List repos")
            ],
            response_metadata=MODEL,
        ),
        ToolMessage("Error: subagent crashed", tool_call_id="call-1", name="task", status="error"),
    )
    child = _thread(HumanMessage("List repos"), nick="")
    assert _lines(root, {"call-1": child}) == [
        "<Alex> which repos?",
        "-!- repo-manager [subagent] has joined #abcd1234",
        "-> *repo-manager* List repos",
        "-!- repo-manager [subagent] has left #abcd1234 [subagent crashed]",
    ]


def test_reasoning_summaries_that_are_plain_strings_still_render() -> None:
    lines = _lines(
        _thread(
            HumanMessage("go"),
            AIMessage(
                content=[
                    {"type": "reasoning", "summary": "**Reading the schedule**\n\nbody"},
                    {"type": "reasoning", "summary": ["**Planning a reply**"]},
                ],
                response_metadata=MODEL,
            ),
        )
    )
    assert lines[1:] == [
        "* progenitor is reading the schedule",
        "* progenitor is planning a reply",
    ]


def test_task_subagent_is_inlined_between_join_and_part_without_repeating_its_reply() -> None:
    root = _thread(
        HumanMessage("which repos?"),
        AIMessage(
            "",
            tool_calls=[
                _call("task", "call-1", subagent_type="repo-manager", description="List repos")
            ],
            response_metadata=MODEL,
        ),
        ToolMessage("No repos configured.", tool_call_id="call-1", name="task"),
        AIMessage("None are configured.", response_metadata=MODEL),
    )
    child = _thread(
        HumanMessage("List repos"),
        AIMessage(
            "",
            tool_calls=[_call("read_file", "call-2", file_path="/config/repos.json")],
            response_metadata=MODEL,
        ),
        ToolMessage("Error: File '/config/repos.json' not found", tool_call_id="call-2"),
        AIMessage("No repos configured.", response_metadata=MODEL),
        nick="repo-manager",
    )
    assert _lines(root, {"call-1": child}) == [
        "<Alex> which repos?",
        "-!- repo-manager [subagent] has joined #abcd1234",
        "-> *repo-manager* List repos",
        "* repo-manager can't read /config/repos.json (File '/config/repos.json' not found)",
        "<@repo-manager> No repos configured.",
        "-!- repo-manager [subagent] has left #abcd1234",
        "<@progenitor> None are configured.",
    ]


def test_tool_failures_rewrite_the_action_and_nonzero_exits_are_flagged() -> None:
    lines = _lines(
        _thread(
            HumanMessage("check"),
            AIMessage(
                "",
                tool_calls=[
                    _call("ls", "c1", path="/routines"),
                    _call("execute", "c2", command="git status"),
                    _call("execute", "c3", command="false"),
                ],
                response_metadata=MODEL,
            ),
            ToolMessage("[]", tool_call_id="c1"),
            ToolMessage("clean\n[Command succeeded with exit code 0]", tool_call_id="c2"),
            ToolMessage("\n[Command failed with exit code 1]", tool_call_id="c3"),
        )
    )
    assert lines[1:] == [
        "* progenitor lists /routines",
        "* progenitor runs `git status`",
        "* progenitor runs `false` (exit 1)",
    ]


def test_reasoning_titles_become_actions_and_cancelled_turns_quit() -> None:
    reasoning = {
        "type": "reasoning",
        "summary": [
            {"type": "summary_text", "text": "**Checking available agents**\n\nI need to look."},
            {"type": "summary_text", "text": "**Schedule options**\n\nTwo ways."},
        ],
    }
    lines = _lines(
        _thread(
            HumanMessage("write an essay"),
            AIMessage(
                content=[reasoning, {"type": "text", "text": "Once upon"}], response_metadata=MODEL
            ),
            HumanMessage("again"),
            AIMessage("Part of a repl", response_metadata={"status": "cancelled"}),
        )
    )
    assert lines == [
        "<Alex> write an essay",
        "* progenitor is checking available agents",
        "* progenitor thinks about schedule options",
        "<@progenitor> Once upon",
        "<Alex> again",
        "<@progenitor> Part of a repl",
        "-!- progenitor has quit [cancelled by Alex]",
    ]


def test_a2a_send_completed_speaks_the_reply_and_failure_parts_with_the_reason() -> None:
    lines = _lines(
        _thread(
            HumanMessage("greet me twice"),
            AIMessage(
                "",
                tool_calls=[
                    _call("a2a_send", "a1", agent="greeter", message="Bonjour please"),
                    _call("a2a_send", "a2", agent="greeter", message="Again"),
                ],
                response_metadata=MODEL,
            ),
            ToolMessage(
                '{"status": "completed", "latest_message": "Bonjour, Alex !"}', tool_call_id="a1"
            ),
            ToolMessage(
                '{"success": false, "error": "Agent is in failed state"}', tool_call_id="a2"
            ),
        )
    )
    assert lines[1:] == [
        "-!- greeter [a2a] has joined #abcd1234",
        "-> *greeter* Bonjour please",
        "-!- greeter [a2a] has joined #abcd1234",
        "-> *greeter* Again",
        "<@greeter> Bonjour, Alex !",
        "-!- greeter [a2a] has left #abcd1234",
        "-!- greeter [a2a] has left #abcd1234 [Agent is in failed state]",
    ]


def test_log_header_lists_participants_and_marks_day_changes_in_the_users_timezone() -> None:
    late = HumanMessage("late night", id="late")
    reply = AIMessage("good morning", id="reply", response_metadata=MODEL)
    root = ThreadMessages(
        messages=[late, reply],
        committed_at={
            "late": T0.replace(hour=22, minute=59),
            "reply": T0.replace(hour=23, minute=1),
        },
    )
    transcript = render_transcript(root, subagents={}, channel="abcd1234", user_nick="Alex")

    log = format_log(
        transcript,
        channel="abcd1234",
        user_nick="Alex",
        title="late night",
        topic_set=T0.replace(hour=22, minute=58),
        tz=ZoneInfo("Europe/London"),
        tz_name="Europe/London",
    )
    assert log.splitlines() == [
        "--- Log opened Mon Oct 12 23:59:00 2026 (Europe/London)",
        "-!- Topic for #abcd1234: late night",
        "-!- Topic set by Alex [Mon Oct 12 23:58:00 2026]",
        "-!- [Users #abcd1234] [@progenitor] [Alex]",
        "23:59:00 <Alex> late night",
        "--- Day changed Tue Oct 13 2026",
        "00:01:00 <@progenitor> good morning",
    ]


def test_image_blocks_render_as_sends_an_image() -> None:
    root = _thread(
        HumanMessage(
            content=[
                {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
                {"type": "text", "text": "what is this"},
            ]
        ),
        AIMessage(
            content=[
                {"type": "image_url", "image_url": {"url": "http://example.com/ai.png"}},
                {"type": "text", "text": "a cat"},
            ],
            response_metadata=MODEL,
        ),
    )
    transcript = render_transcript(root, subagents={}, channel="abcd1234", user_nick="Alex")
    lines = [line.text for line in transcript.lines]
    assert lines == [
        "<Alex> what is this",
        "* Alex sends an image",
        "* progenitor sends an image",
        "<@progenitor> a cat",
    ]
