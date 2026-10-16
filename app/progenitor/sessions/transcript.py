"""Render a chat's LangGraph messages as an irssi-style IRC log.

Pure formatting: callers load the messages (``history.py``) and decide where
the text goes. Every line starts ``HH:MM:SS`` and has one of these shapes::

    <nick> text / <@agent> text    something said; ``@`` marks agents
    * nick does something          reasoning title or tool use
    * nick sends an image          an image content block (human or AI)
    -nick- text                    one-way notice to the user (notify/say/ask)
    -> *agent* text                delegated request, sent privately to an agent
    -!- event                      join/part/quit, failed direct agent calls

Tool results are not shown: a failed call rewrites its action line
(``* nick can't read /x (not found)``) and the assistant's reply says what
came back. Subagents run by the ``task`` tool are inlined between their join
and part; direct ``@agent request`` turns (answered by the A2A fast-path
middleware instead of the model) read as the user addressing that agent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, tzinfo
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from ..agent.middleware.direct_a2a_fast_path import _DIRECT_A2A_EMPTY_RE, _DIRECT_A2A_RE
from ..runtime.responses_api import CANCELLED_STATUS
from ..state.models import redact_secrets

# Bump whenever rendered output changes, so cached transcripts re-render.
FORMAT_VERSION = 1

DEFAULT_USER_NICK = "user"
DEFAULT_AGENT_NICK = "progenitor"
_ASYNC_TASK_ID = re.compile(r"task_id:\s*([0-9a-f]+)")
_EXIT_CODE = re.compile(r"\[Command \w+ with exit code (-?\d+)\]\s*$")
_REASON_CHARS = 100
_QUOTE_CHARS = 80
_DIRECT_FAILED_PREFIX = "Direct agent call failed:"
_A2A_FAILED_STATUSES = {"failed", "canceled", "cancelled", "rejected"}
_DELEGATION_TOOLS = {"task", "start_async_task", "call_agent"}
_NOTICE_TOOLS = {"notify_user", "say_to_user", "ask_user"}


@dataclass(frozen=True)
class ThreadMessages:
    """One checkpoint namespace: its messages, when each was committed, and its nick."""

    messages: list[BaseMessage]
    committed_at: Mapping[str, datetime]
    nick: str = DEFAULT_AGENT_NICK


@dataclass
class Line:
    at: datetime | None
    text: str


@dataclass
class Transcript:
    """Rendered lines plus what the header needs."""

    lines: list[Line]
    agents: set[str]
    user_spoke: bool
    first_user_text: str

    @property
    def started_at(self) -> datetime | None:
        return next((line.at for line in self.lines if line.at), None)


def render_transcript(
    root: ThreadMessages,
    *,
    subagents: Mapping[str, ThreadMessages],
    channel: str,
    user_nick: str,
) -> Transcript:
    """Render the root thread; ``subagents`` maps a ``task`` tool_call_id to its thread."""
    renderer = _Renderer(channel=channel, user_nick=user_nick, subagents=subagents)
    lines = renderer.render(root, parent_nick=None)
    return Transcript(
        lines=lines,
        agents=renderer.agents,
        user_spoke=renderer.user_spoke,
        first_user_text=renderer.first_user_text,
    )


def format_log(
    transcript: Transcript,
    *,
    channel: str,
    user_nick: str,
    title: str,
    topic_set: datetime | None,
    tz: tzinfo,
    tz_name: str,
) -> str:
    """The full log text: irssi header, then every line, secrets redacted."""
    opened = transcript.started_at or topic_set
    users = sorted(f"@{agent}" for agent in transcript.agents)
    if transcript.user_spoke:
        users.append(user_nick)
    out: list[str] = []
    day = None
    if opened is not None:
        out.append(f"--- Log opened {_irc_time(opened, tz)} ({tz_name})")
        day = opened.astimezone(tz).date()
    out.append(f"-!- Topic for #{channel}: {title}")
    if topic_set is not None:
        out.append(f"-!- Topic set by {user_nick} [{_irc_time(topic_set, tz)}]")
    out.append(f"-!- [Users #{channel}] " + " ".join(f"[{user}]" for user in users))
    for line in transcript.lines:
        local = line.at.astimezone(tz) if line.at else None
        if local is not None and local.date() != day:
            day = local.date()
            out.append(f"--- Day changed {local:%a %b %d %Y}")
        out.append(f"{local:%H:%M:%S} {line.text}" if local else f"--:--:-- {line.text}")
    return redact_secrets("\n".join(out) + "\n")


@dataclass
class _PendingCall:
    call: dict[str, Any]
    nick: str
    line: int | None = None  # index of the action line to rewrite if the call fails


@dataclass
class _Renderer:
    channel: str
    user_nick: str
    subagents: Mapping[str, ThreadMessages]
    # async task id -> subagent name, learnt from ``start_async_task`` results.
    async_tasks: dict[str, str] = field(default_factory=dict)
    agents: set[str] = field(default_factory=set)
    user_spoke: bool = False
    first_user_text: str = ""

    def render(self, thread: ThreadMessages, parent_nick: str | None) -> list[Line]:
        lines: list[Line] = []
        last: datetime | None = None
        pending: dict[str, _PendingCall] = {}
        # Direct ``@agent request`` turns (root thread only): the agent the
        # latest user message addressed and that line's index, and the agent
        # currently joined because of earlier direct turns.
        addressed: tuple[str, int] | None = None
        present: str | None = None
        for message in thread.messages:
            at = _message_time(message, thread.committed_at) or last
            last = at or last
            if isinstance(message, HumanMessage):
                blocks = _content_blocks(message)
                text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
                image_count = sum(1 for b in blocks if b.get("type") == "image_url")
                text = "\n".join(text_parts)
                if parent_nick is not None:
                    lines += [Line(at, f"-> *{thread.nick}* {row}") for row in _rows(text)]
                    for _ in range(image_count):
                        lines.append(Line(at, f"* {thread.nick} sends an image"))
                    continue
                self.user_spoke = True
                self.first_user_text = self.first_user_text or text
                mention = _direct_mention(text)
                if present and (mention is None or mention[0] != present):
                    lines.append(Line(at, f"-!- {present} [a2a] has left #{self.channel}"))
                    present = None
                addressed = (mention[0], len(lines)) if mention else None
                spoken = f"{mention[0]}: {mention[1]}" if mention else text
                lines += _say(at, self.user_nick, spoken)
                for _ in range(image_count):
                    lines.append(Line(at, f"* {self.user_nick} sends an image"))
            elif isinstance(message, AIMessage):
                if addressed is not None and _is_direct_reply(message):
                    present = self._direct_reply(message, at, addressed, present, lines)
                    addressed = None
                    continue
                addressed = None
                self.agents.add(thread.nick)
                lines += self._ai(message, at, thread.nick, lines, pending)
            elif isinstance(message, ToolMessage):
                lines += self._tool_result(message, at, lines, pending)
        return lines

    def _direct_reply(
        self,
        message: AIMessage,
        at: datetime | None,
        addressed: tuple[str, int],
        present: str | None,
        lines: list[Line],
    ) -> str | None:
        """Render the A2A fast path's reply to ``@agent request``; return who is joined."""
        agent, user_line = addressed
        text = _message_text(message)
        if text.startswith(_DIRECT_FAILED_PREFIX):
            why = text.removeprefix(_DIRECT_FAILED_PREFIX).strip().rstrip(".")
            if why.startswith("unknown agent"):
                why = "No such nick/channel"
            lines.append(Line(at, f"-!- {agent}: {why}"))
            return present
        speaker = str(message.response_metadata.get("display_agent_name") or agent)
        self.agents.add(speaker)
        if speaker != present:
            joined = Line(lines[user_line].at, f"-!- {speaker} [a2a] has joined #{self.channel}")
            lines.insert(user_line, joined)
        lines += _say(at, f"@{speaker}", text)
        return speaker

    def _ai(
        self,
        message: AIMessage,
        at: datetime | None,
        nick: str,
        lines: list[Line],
        pending: dict[str, _PendingCall],
    ) -> list[Line]:
        out: list[Line] = []
        for block in _content_blocks(message):
            kind = block.get("type")
            if kind == "reasoning":
                summaries = block.get("summary") or []
                for summary in [summaries] if isinstance(summaries, str) else summaries:
                    text = summary.get("text", "") if isinstance(summary, dict) else str(summary)
                    thought = _reasoning_action(text)
                    if thought:
                        out.append(Line(at, f"* {nick} {thought}"))
            elif kind == "text":
                out += _say(at, f"@{nick}", block.get("text", ""))
            elif kind == "image_url":
                out.append(Line(at, f"* {nick} sends an image"))
        if message.response_metadata.get("status") == CANCELLED_STATUS:
            out.append(Line(at, f"-!- {nick} has quit [cancelled by {self.user_nick}]"))
        for call in message.tool_calls:
            entry = _PendingCall(call=dict(call), nick=nick)
            pending[call["id"] or ""] = entry
            out += self._tool_call(entry, at, base=len(lines) + len(out))
        return out

    def _tool_call(self, entry: _PendingCall, at: datetime | None, base: int) -> list[Line]:
        call, nick = entry.call, entry.nick
        name, args = call["name"], call.get("args") or {}
        if name in _DELEGATION_TOOLS:
            agent = _delegate_name(name, args)
            self.agents.add(agent)
            kind = {"task": "subagent", "start_async_task": "async", "call_agent": "a2a"}[name]
            out = [Line(at, f"-!- {agent} [{kind}] has joined #{self.channel}")]
            child = self.subagents.get(call["id"]) if name == "task" else None
            if child is None:
                request = args.get("description") or args.get("request") or ""
                out += [Line(at, f"-> *{agent}* {row}") for row in _rows(str(request))]
                return out
            out += self.render(child if child.nick else replace(child, nick=agent), nick)
            out.append(Line(out[-1].at, f"-!- {agent} [subagent] has left #{self.channel}"))
            entry.line = base + len(out) - 1  # the part line gains the reason if the task failed
            return out
        if name in _NOTICE_TOOLS:
            return [Line(at, f"-{nick}- {row}") for row in _rows(_notice_text(name, args))]
        if name == "check_async_task":
            return []  # Rendered from its result: success, still running, or failure.
        entry.line = base
        return [Line(at, f"* {nick} {_tool_action(name, args)[0]}")]

    def _tool_result(
        self,
        message: ToolMessage,
        at: datetime | None,
        lines: list[Line],
        pending: dict[str, _PendingCall],
    ) -> list[Line]:
        entry = pending.get(message.tool_call_id)
        if entry is None:
            return []
        call, nick = entry.call, entry.nick
        name, args = call["name"], call.get("args") or {}
        text = _message_text(message)
        failed = message.status == "error" or text.startswith("Error")
        if name in _DELEGATION_TOOLS:
            if entry.line is not None:  # inlined task subagent: transcript and part already out
                if failed:
                    lines[entry.line].text += f" [{_reason(text)}]"
                return []
            return self._delegate_result(name, args, text, failed, at)
        if name == "check_async_task":
            return self._async_check(args, text, failed, at, nick)
        if entry.line is None:
            if failed:  # notices: say they didn't go out
                return [Line(at, f"* {nick} can't {name} ({_reason(text)})")]
            return []
        if failed:
            action = _tool_action(name, args)[1]
            lines[entry.line].text = f"* {nick} can't {action} ({_reason(text)})"
        elif (code := _EXIT_CODE.search(text)) and code.group(1) != "0":
            lines[entry.line].text += f" (exit {code.group(1)})"
        return []

    def _delegate_result(
        self,
        name: str,
        args: dict[str, Any],
        text: str,
        failed: bool,
        at: datetime | None,
    ) -> list[Line]:
        agent = _delegate_name(name, args)
        if name == "start_async_task":
            if failed:
                return [Line(at, f"-!- {agent} [async] has quit [{_reason(text)}]")]
            if match := _ASYNC_TASK_ID.search(text):
                self.async_tasks[match.group(1)] = agent
            return []
        kind = "subagent" if name == "task" else "a2a"
        if failed:
            return [Line(at, f"-!- {agent} [{kind}] has left #{self.channel} [{_reason(text)}]")]
        if name == "call_agent":
            return self._a2a_result(agent, text, at)
        return [*_say(at, f"@{agent}", text), self._part(at, agent, kind)]

    def _a2a_result(self, agent: str, text: str, at: datetime | None) -> list[Line]:
        """``call_agent`` returns the tracked A2A task record (or a failure dict)."""
        result = _parse_json(text)
        if not isinstance(result, dict):
            return [*_say(at, f"@{agent}", text), self._part(at, agent, "a2a")]
        status = str(result.get("status") or "")
        error = result.get("error") or result.get("last_error")
        if result.get("success") is False or error or status in _A2A_FAILED_STATUSES:
            why = _reason(str(error or status or "failed"))
            return [Line(at, f"-!- {agent} [a2a] has left #{self.channel} [{why}]")]
        if status == "completed":
            reply = str(result.get("latest_message") or result.get("summary") or "")
            return [*_say(at, f"@{agent}", reply), self._part(at, agent, "a2a")]
        task_id = str(result.get("task_id") or "")[:8]
        return [Line(at, f"-!- {agent} [a2a] task {task_id} is {status or 'submitted'}")]

    def _async_check(
        self, args: dict[str, Any], text: str, failed: bool, at: datetime | None, nick: str
    ) -> list[Line]:
        task_id = str(args.get("task_id", ""))
        agent = self.async_tasks.get(task_id, f"async task {task_id[:8]}")
        result = _parse_json(text)
        if failed or not isinstance(result, dict):
            return [Line(at, f"* {nick} can't check on {agent} ({_reason(text)})")]
        status = result.get("status")
        if status == "success":
            return [
                *_say(at, f"@{agent}", str(result.get("result", ""))),
                self._part(at, agent, "async"),
            ]
        if status in {"error", "failed", "cancelled"}:
            why = _reason(str(result.get("error") or result.get("result") or status))
            return [Line(at, f"-!- {agent} [async] has quit [{why}]")]
        return [Line(at, f"* {nick} checks on {agent} (still {status or 'running'})")]

    def _part(self, at: datetime | None, agent: str, kind: str) -> Line:
        return Line(at, f"-!- {agent} [{kind}] has left #{self.channel}")


def _tool_action(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """``(action, failed-action base)`` for a tool call, e.g. ``("reads /a", "read /a")``."""
    path = args.get("file_path") or args.get("path") or "/"
    if name == "read_file":
        return f"reads {path}", f"read {path}"
    if name == "ls":
        return f"lists {path}", f"list {path}"
    if name == "write_file":
        return f"writes {path}", f"write {path}"
    if name == "edit_file":
        return f"edits {path}", f"edit {path}"
    if name == "glob":
        pattern = args.get("pattern", "")
        return f"looks for files matching {pattern}", f"look for files matching {pattern}"
    if name == "grep":
        pattern = _clip(str(args.get("pattern", "")), _QUOTE_CHARS)
        return f'searches {path} for "{pattern}"', f'search {path} for "{pattern}"'
    if name in {"execute", "shell_exec"}:
        command = _clip(_one_line(str(args.get("command", ""))), _QUOTE_CHARS)
        return f"runs `{command}`", f"run `{command}`"
    if name == "write_todos":
        todos = args.get("todos") or []
        done = sum(
            1 for todo in todos if isinstance(todo, dict) and todo.get("status") == "completed"
        )
        return f"updates the todo list ({done}/{len(todos)} done)", "update the todo list"
    if name == "search":
        query = _clip(str(args.get("query", "")), _QUOTE_CHARS)
        return f'searches knowledge for "{query}"', f'search knowledge for "{query}"'
    if name == "list_agents":
        return "lists agents", "list agents"
    return f"uses {name}", f"use {name}"


def _notice_text(name: str, args: dict[str, Any]) -> str:
    if name == "notify_user":
        level = args.get("level") or "info"
        return f"[{level}] {args.get('title', '')}: {args.get('body', '')}"
    if name == "ask_user":
        options = args.get("options") or []
        suffix = f" [{' / '.join(map(str, options))}]" if options else ""
        return f"{args.get('question', '')}{suffix}"
    return str(args.get("message", ""))


def _delegate_name(tool: str, args: dict[str, Any]) -> str:
    if tool == "call_agent":
        return str(args.get("agent") or args.get("agent_name") or "a2a-agent")
    return str(args.get("subagent_type") or "subagent")


def _direct_mention(text: str) -> tuple[str, str] | None:
    """``(agent, request)`` when ``text`` is a direct ``@agent request`` message."""
    match = _DIRECT_A2A_RE.match(text) or _DIRECT_A2A_EMPTY_RE.match(text)
    if match is None:
        return None
    return match.group("agent"), (match.groupdict().get("request") or "").strip()


def _is_direct_reply(message: AIMessage) -> bool:
    """The fast path's reply: no model ran, so no provider metadata and no tool calls."""
    metadata = message.response_metadata
    return (
        not message.tool_calls
        and "model_provider" not in metadata
        and metadata.get("status") != CANCELLED_STATUS
    )


def _reasoning_action(summary: str) -> str:
    """``**Checking available agents**\\n\\n...`` -> ``is checking available agents``."""
    stripped = summary.strip()
    title = stripped.splitlines()[0].strip("*").strip().rstrip(".:").lower() if stripped else ""
    if not title:
        return ""
    phrase = title[0].lower() + title[1:]
    if phrase.split()[0].endswith("ing"):
        return f"is {phrase}"
    return f"thinks about {phrase}"


def _message_time(message: BaseMessage, committed_at: Mapping[str, datetime]) -> datetime | None:
    if message.id and message.id in committed_at:
        return committed_at[message.id]
    created = message.additional_kwargs.get("created_at") or message.response_metadata.get(
        "created_at"
    )
    return datetime.fromtimestamp(created, UTC) if isinstance(created, int | float) else None


def _content_blocks(message: BaseMessage) -> list[dict[str, Any]]:
    if isinstance(message.content, str):
        return [{"type": "text", "text": message.content}] if message.content else []
    return [
        block if isinstance(block, dict) else {"type": "text", "text": str(block)}
        for block in message.content
    ]


def _message_text(message: BaseMessage) -> str:
    return "\n".join(
        block.get("text", "") for block in _content_blocks(message) if block.get("type") == "text"
    )


def _rows(text: str) -> list[str]:
    return [row.rstrip() for row in text.splitlines() if row.strip()]


def _say(at: datetime | None, nick: str, text: str) -> list[Line]:
    return [Line(at, f"<{nick}> {row}") for row in _rows(text)]


def _reason(text: str) -> str:
    return _clip(_one_line(text).removeprefix("Error:").strip(), _REASON_CHARS)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _irc_time(at: datetime, tz: tzinfo) -> str:
    return f"{at.astimezone(tz):%a %b %d %H:%M:%S %Y}"
