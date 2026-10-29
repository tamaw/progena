"""Shared pieces for rendering runs as IRC logs with YAML front matter."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, tzinfo
from typing import Any

import yaml

from src.progena.runtime.ownership import parse_timestamp
from src.progena.sessions.history import load_conversation
from src.progena.sessions.transcript import Line, Transcript, format_log, render_transcript

from .values import describe_value, full_text, head_tail

# Nested child runs (a workflow's routine step, a routine's routine node) are
# inlined up to this depth.
MAX_CHILD_DEPTH = 3


@dataclass
class RunContext:
    """What every renderer reads; built once per mount read."""

    state_manager: Any
    checkpointer: Any
    revision_reader: Any
    tz: tzinfo
    tz_name: str
    live_instance: str | None
    live_started_at: str | None
    routine_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Steps:
    """Full step values of one run file, addressed as ``<path>#<n>``."""

    path: str
    values: list[Any] = field(default_factory=list)

    def add(self, value: Any) -> str:
        self.values.append(value)
        return f"{self.path}#{len(self.values)}"

    def text(self, index: str) -> str | None:
        """The ``#n`` value as readable text, or ``None`` if out of range."""
        try:
            number = int(index)
        except ValueError:
            return None
        if not 1 <= number <= len(self.values):
            return None
        return full_text(self.values[number - 1])


@dataclass
class RunLog:
    """Lines of one run plus the nicks that took part in it."""

    nick: str
    lines: list[Line] = field(default_factory=list)
    agents: set[str] = field(default_factory=set)
    last_speech: str | None = None

    def act(self, at: datetime | None, text: str) -> None:
        self.lines.append(Line(at, f"* {self.nick} {text}"))

    def event(self, at: datetime | None, text: str) -> None:
        self.lines.append(Line(at, f"-!- {text}"))

    def say(self, at: datetime | None, nick: str, text: str) -> None:
        rows = [row.rstrip() for row in text.splitlines() if row.strip()]
        self.lines.extend(Line(at, f"<{nick}> {row}") for row in rows)
        if rows:
            self.last_speech = text.strip()

    def merge(self, child: RunLog) -> None:
        self.lines.extend(child.lines)
        self.agents |= child.agents
        self.last_speech = child.last_speech or self.last_speech


def at(value: Any) -> datetime | None:
    return parse_timestamp(value)


def step_result(value: Any, steps: Steps) -> str:
    """``→ <description>``, registering the full value under ``#n``."""
    pointer = steps.add(value)
    return describe_value(value, pointer=pointer)


# A request handed to an agent step is the step's filled-in prompt template:
# the same instructions every run, so only its ends are shown.
_REQUEST_CHARS = 300


def _text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return None


def _as_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None


def _compact_messages(messages: list[Any], steps: Steps) -> list[Any]:
    """Agent requests and structured (JSON) replies are data, not speech."""
    compacted: list[Any] = []
    for message in messages:
        kind = getattr(message, "type", None)
        text = _text(getattr(message, "content", None))
        if text is None or kind not in {"human", "ai"}:
            compacted.append(message)
            continue
        if kind == "ai" and getattr(message, "tool_calls", None):
            compacted.append(message)
            continue
        parsed = _as_json(text)
        if parsed is not None:
            summary = describe_value(parsed, pointer=steps.add(parsed))
        elif kind == "human" and len(text) > _REQUEST_CHARS:
            summary = head_tail(text, pointer=steps.add(text))
        else:
            compacted.append(message)
            continue
        compacted.append(message.model_copy(update={"content": summary}))
    return compacted


async def agent_transcript(
    ctx: RunContext,
    log: RunLog,
    thread_id: str,
    *,
    agent: str,
    channel: str,
    when: datetime | None,
    steps: Steps,
) -> bool:
    """Inline an agent step's own thread between join/part; ``False`` if it has none."""
    if ctx.checkpointer is None or not thread_id:
        return False
    conversation = await load_conversation(ctx.checkpointer, thread_id)
    if conversation is None:
        return False
    root = conversation.root
    transcript = render_transcript(
        replace(root, nick=agent, messages=_compact_messages(root.messages, steps)),
        subagents=conversation.subagents,
        channel=channel,
        user_nick=log.nick,
    )
    joined = transcript.started_at or when
    log.event(joined, f"{agent} [agent] has joined #{channel}")
    log.lines.extend(transcript.lines)
    log.agents |= transcript.agents
    reply = _last_reply(root.messages)
    if reply:
        log.last_speech = reply
    left = next((line.at for line in reversed(transcript.lines) if line.at), joined)
    log.event(left, f"{agent} [agent] has left #{channel}")
    return True


def _last_reply(messages: list[Any]) -> str | None:
    for message in reversed(messages):
        if getattr(message, "type", None) != "ai":
            continue
        content = getattr(message, "content", "")
        if isinstance(content, list):
            content = "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        if isinstance(content, str) and content.strip():
            return content.strip()
    return None


def render_file(
    front: dict[str, Any], log: RunLog, *, channel: str, topic: str, ctx: RunContext
) -> str:
    """YAML front matter, a blank line, then the IRC log (secrets redacted)."""
    transcript = Transcript(
        lines=log.lines, agents=set(log.agents), user_spoke=False, first_user_text=""
    )
    body = format_log(
        transcript,
        channel=channel,
        user_nick=log.nick,
        title=topic,
        topic_set=None,
        tz=ctx.tz,
        tz_name=ctx.tz_name,
    )
    header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=1000)
    return f"---\n{header}---\n\n{body}"
