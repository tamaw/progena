"""Shared pieces for rendering runs as IRC logs with YAML front matter."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, tzinfo
from typing import Any

import yaml

from src.progena.persistence.langgraph import CheckpointVersions, checkpoint_versions
from src.progena.runtime.ownership import parse_timestamp
from src.progena.sessions.history import Conversation, load_conversation
from src.progena.sessions.messages import extract_token_usage
from src.progena.sessions.transcript import Line, Transcript, format_log, render_transcript

from .values import describe_value, full_text, head_tail

# Nested child runs (a workflow's routine step, a routine's routine node) are
# inlined up to this depth.
MAX_CHILD_DEPTH = 3

RoutineEventRevisions = tuple[tuple[str, str, str, str], ...]


def routine_event_revisions(events: Iterable[dict[str, Any]]) -> RoutineEventRevisions:
    """Stable lifecycle revision for run threads a render causally inspected."""
    return tuple(
        sorted(
            (
                str(event.get("request_id") or ""),
                str(event.get("status") or ""),
                str(event.get("timestamp") or ""),
                str(event.get("runtime_instance") or ""),
            )
            for event in events
        )
    )


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

    checkpoint_dependencies: set[tuple[str, str]] = field(default_factory=set)
    checkpoint_versions_before: CheckpointVersions | None = ()
    _tracked_checkpoint_threads: set[str] = field(default_factory=set)
    _checkpoint_versions: dict[str, CheckpointVersions | None] = field(default_factory=dict)
    routine_event_versions: dict[str, RoutineEventRevisions] = field(default_factory=dict)

    loaded_threads: set[str] = field(default_factory=set)
    conversations: dict[str, Conversation | None] = field(default_factory=dict)
    seen_token_messages: set[str] = field(default_factory=set)
    _workflow_definitions: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    _workflow_fingerprints: dict[str, str] = field(default_factory=dict)
    _routine_graphs: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    _routine_fingerprints: dict[str, str] = field(default_factory=dict)
    _preferences: dict[str, Any] | None = None
    _preferences_fingerprint: str | None = None
    _preferences_used: bool = False

    async def checkpoint(self, thread_id: str, namespace: str = "") -> Any | None:
        """Load one checkpoint tip while recording its thread as a cache dependency."""
        if self.checkpointer is None or not thread_id:
            return None
        await self._track_checkpoint_thread(thread_id)
        self.checkpoint_dependencies.add((thread_id, namespace))
        return await self.checkpointer.aget_tuple(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace}}
        )

    async def _track_checkpoint_thread(self, thread_id: str) -> None:
        """Snapshot every namespace before rendering can hydrate this thread."""
        if not thread_id:
            return
        self.checkpoint_dependencies.add((thread_id, ""))
        if thread_id in self._tracked_checkpoint_threads:
            return
        self._tracked_checkpoint_threads.add(thread_id)
        versions = await self.checkpoint_version_snapshot((thread_id,))
        if versions is None:
            self.checkpoint_versions_before = None
            return
        if self.checkpoint_versions_before is not None:
            self.checkpoint_versions_before = tuple(
                sorted((*self.checkpoint_versions_before, *versions))
            )

    async def checkpoint_version_snapshot(
        self, thread_ids: Iterable[str], *, refresh: bool = False
    ) -> CheckpointVersions | None:
        """Read compact checkpoint tips once per thread, unless an admission rechecks them."""
        threads = tuple(dict.fromkeys(thread_id for thread_id in thread_ids if thread_id))
        if not threads:
            return ()
        missing = (
            threads
            if refresh
            else tuple(
                thread_id for thread_id in threads if thread_id not in self._checkpoint_versions
            )
        )
        if missing:
            versions = await checkpoint_versions(self.checkpointer, missing)
            if versions is None:
                for thread_id in missing:
                    self._checkpoint_versions[thread_id] = None
            else:
                for thread_id in missing:
                    self._checkpoint_versions[thread_id] = tuple(
                        version for version in versions if version[0] == thread_id
                    )
        snapshots = [self._checkpoint_versions.get(thread_id) for thread_id in threads]
        if any(snapshot is None for snapshot in snapshots):
            return None
        return tuple(sorted(version for snapshot in snapshots for version in snapshot or ()))

    async def prepare_thread_events(self, thread_id: str, *, refresh: bool = False) -> None:
        """Load compact lifecycle facts needed for status and cache validation."""
        if not thread_id:
            return
        await self._track_checkpoint_thread(thread_id)
        if thread_id in self.routine_event_versions and not refresh:
            return
        events = await self.state_manager.list_routine_run_event_revisions(thread_ids=[thread_id])
        compact = [event for event in events if isinstance(event, dict)]
        self.routine_event_versions[thread_id] = routine_event_revisions(compact)
        self.routine_events = [
            event for event in self.routine_events if event.get("thread_id") != thread_id
        ]
        self.routine_events.extend(compact)

    async def lifecycle_revision_snapshot(
        self, thread_ids: Iterable[str], *, refresh: bool = False
    ) -> RoutineEventRevisions:
        """Return compact lifecycle revisions, reusing facts prepared for this read."""
        threads = tuple(dict.fromkeys(thread_id for thread_id in thread_ids if thread_id))
        for thread_id in threads:
            await self.prepare_thread_events(thread_id, refresh=refresh)
        return tuple(
            sorted(
                revision
                for thread_id in threads
                for revision in self.routine_event_versions.get(thread_id, ())
            )
        )

    async def workflow_definition(
        self, workflow_id: str, *, refresh: bool = False
    ) -> dict[str, Any] | None:
        """Get one mutable workflow definition once and retain it for cache validation."""
        if refresh or workflow_id not in self._workflow_definitions:
            definition = await self.state_manager.get_workflow_definition(workflow_id)
            value = definition if isinstance(definition, dict) else None
            self._workflow_definitions[workflow_id] = value
            self._workflow_fingerprints[workflow_id] = self._configuration_value(value)
        return self._workflow_definitions[workflow_id]

    async def routine_graph(
        self, routine_id: str, *, refresh: bool = False
    ) -> dict[str, Any] | None:
        """Get one mutable routine graph once and retain it for cache validation."""
        if refresh or routine_id not in self._routine_graphs:
            graph = await self.state_manager.get_routine_graph(routine_id)
            value = graph if isinstance(graph, dict) else None
            self._routine_graphs[routine_id] = value
            self._routine_fingerprints[routine_id] = self._configuration_value(value)
        return self._routine_graphs[routine_id]

    async def preferences(self, *, refresh: bool = False) -> dict[str, Any]:
        """Get preferences once when a renderer actually consumes them."""
        self._preferences_used = True
        if refresh or self._preferences is None:
            preferences = await self.state_manager.get_preferences_config()
            self._preferences = preferences if isinstance(preferences, dict) else {}
        if refresh or self._preferences_fingerprint is None:
            self._preferences_fingerprint = self._configuration_value(self._preferences)
        return self._preferences

    @staticmethod
    def _configuration_value(value: dict[str, Any] | None) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    def configuration_snapshot(
        self,
    ) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...], str | None]:
        """Stable values of mutable configuration consumed by a render."""
        return (
            tuple(sorted(self._workflow_fingerprints.items())),
            tuple(sorted(self._routine_fingerprints.items())),
            self._preferences_fingerprint if self._preferences_used else None,
        )

    async def configuration_matches(
        self,
        snapshot: tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...], str | None],
        *,
        refresh: bool = False,
    ) -> bool:
        """Re-read only mutable configuration a cached render actually consumed."""
        workflows, routines, preferences = snapshot
        for identifier, expected in workflows:
            await self.workflow_definition(identifier, refresh=refresh)
            if self._workflow_fingerprints[identifier] != expected:
                return False
        for identifier, expected in routines:
            await self.routine_graph(identifier, refresh=refresh)
            if self._routine_fingerprints[identifier] != expected:
                return False
        if preferences is not None:
            await self.preferences(refresh=refresh)
            if self._preferences_fingerprint != preferences:
                return False
        return True

    async def conversation(self, thread_id: str) -> Conversation | None:
        if self.checkpointer is None or not thread_id:
            return None
        await self._track_checkpoint_thread(thread_id)
        if thread_id not in self.conversations:
            self.conversations[thread_id] = await load_conversation(self.checkpointer, thread_id)
        return self.conversations[thread_id]

    async def load_thread_events(self, thread_id: str) -> None:
        """Load full lifecycle records only when a cache miss needs their details."""
        if not thread_id:
            return
        await self.prepare_thread_events(thread_id)
        if thread_id in self.loaded_threads:
            return
        self.loaded_threads.add(thread_id)
        events = [
            event
            for event in await self.state_manager.list_routine_run_events(thread_ids=[thread_id])
            if isinstance(event, dict)
        ]
        self.routine_events = [
            event for event in self.routine_events if event.get("thread_id") != thread_id
        ]
        self.routine_events.extend(events)


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


_TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens")
_TokenTotals = dict[str, int | None]


def recorded_tokens(value: Any) -> _TokenTotals | None:
    """Normalize recorded usage, retaining each missing component as unknown."""
    if not isinstance(value, dict):
        return None
    result = {
        name: value[name] if type(value.get(name)) is int and value[name] >= 0 else None
        for name in _TOKEN_FIELDS
    }
    input_tokens, output_tokens = result["input_tokens"], result["output_tokens"]
    if (
        result["total_tokens"] is None
        and isinstance(input_tokens, int)
        and isinstance(output_tokens, int)
    ):
        result["total_tokens"] = input_tokens + output_tokens
    return result if any(component is not None for component in result.values()) else None


def add_tokens(total: _TokenTotals | None, usage: _TokenTotals | None) -> _TokenTotals | None:
    """Add totals only where every contributing record reported that component."""
    if usage is None:
        return total
    if total is None:
        return dict(usage)
    result: _TokenTotals = {}
    for name in _TOKEN_FIELDS:
        left, right = total[name], usage[name]
        result[name] = left + right if isinstance(left, int) and isinstance(right, int) else None
    return result


def _message_at(message: Any, committed_at: Any) -> datetime | None:
    message_id = getattr(message, "id", None)
    if message_id and message_id in committed_at:
        return committed_at[message_id]
    additional = getattr(message, "additional_kwargs", {}) or {}
    response = getattr(message, "response_metadata", {}) or {}
    return at(additional.get("created_at") or response.get("created_at"))


def _in_window(
    message: Any, committed_at: Any, begin: datetime | None, end: datetime | None
) -> bool:
    if begin is None and end is None:
        return True
    when = _message_at(message, committed_at)
    return when is not None and (begin is None or when >= begin) and (end is None or when < end)


def conversation_tokens(
    conversation: Conversation,
    *,
    begin: datetime | None = None,
    end: datetime | None = None,
    seen_messages: set[str] | None = None,
) -> _TokenTotals | None:
    """Count model messages once across a root conversation and all subagents."""
    total: _TokenTotals | None = None
    seen = seen_messages if seen_messages is not None else set()
    for thread in (conversation.root, *conversation.subagents.values()):
        for message in thread.messages:
            if not _in_window(message, thread.committed_at, begin, end):
                continue
            usage = recorded_tokens(extract_token_usage(message))
            if usage is None:
                continue
            message_id = getattr(message, "id", None)
            key = str(message_id) if message_id else f"object:{id(message)}"
            if key in seen:
                if total is None:
                    total = dict.fromkeys(_TOKEN_FIELDS, 0)
                continue
            seen.add(key)
            total = add_tokens(total, usage)
    return total


def _window_messages(thread: Any, begin: datetime | None, end: datetime | None) -> list[Any]:
    return [
        message
        for message in thread.messages
        if _in_window(message, thread.committed_at, begin, end)
    ]


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
    begin: datetime | None = None,
    end: datetime | None = None,
    token_totals: list[_TokenTotals | None] | None = None,
) -> bool:
    """Inline one bounded agent-step transcript; ``False`` when it has none."""
    if ctx.checkpointer is None or not thread_id:
        return False
    conversation = await ctx.conversation(thread_id)
    if conversation is None:
        return False
    root_messages = _window_messages(conversation.root, begin, end)
    subagents = {
        call_id: replace(thread, messages=_window_messages(thread, begin, end))
        for call_id, thread in conversation.subagents.items()
    }
    if not root_messages and not any(thread.messages for thread in subagents.values()):
        return False
    root = replace(
        conversation.root,
        nick=agent,
        messages=_compact_messages(root_messages, steps),
    )
    if token_totals is not None:
        token_totals.append(
            conversation_tokens(
                conversation, begin=begin, end=end, seen_messages=ctx.seen_token_messages
            )
        )
    transcript = render_transcript(root, subagents=subagents, channel=channel, user_nick=log.nick)
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
