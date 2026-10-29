"""Render one routine attempt as an IRC log.

Steps come from the attempt's latest checkpoint ``run_meta`` (one read):
non-agent steps are actions by the routine, agent steps inline the agent's own
thread, and routine-node children inline their own run.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from src.progena.runtime.ownership import routine_attempt_interrupted
from src.progena.sessions.transcript import Line

from ._render import (
    MAX_CHILD_DEPTH,
    RunContext,
    RunLog,
    Steps,
    add_tokens,
    agent_transcript,
    at,
    recorded_tokens,
    render_file,
    step_result,
)
from .paths import short_id, slug
from .values import describe_value, head_tail, one_line

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "stopped"})

_VERBS = {
    "eval": "evaluates",
    "code": "runs code",
    "prompt": "prompts",
    "condition": "branches at",
    "switch": "branches at",
    "classify": "selects at",
    "split": "forks at",
    "foreach": "loops over",
    "reduce": "reduces",
    "loop": "loops at",
    "human": "asks",
    "a2a": "asks",
    "agent": "asks",
    "routine": "runs",
    "noop": "passes",
}
_ROUTED = frozenset({"condition", "switch", "classify"})


def attempt_events(ctx: RunContext, request_id: str) -> list[dict[str, Any]]:
    return [event for event in ctx.routine_events if event.get("request_id") == request_id]


def attempt_status(ctx: RunContext, events: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Latest lifecycle status, with ``interrupted`` derived as everywhere else."""
    latest = max(events, key=lambda event: str(event.get("timestamp") or ""))
    started = next((event for event in events if event.get("status") == "started"), None)
    status = str(latest.get("status") or "queued")
    if routine_attempt_interrupted(status, started, ctx.live_instance, ctx.live_started_at):
        status = "interrupted"
    return status, latest


def _routine_id(started: dict[str, Any]) -> str:
    return str(started.get("agent_id") or started.get("routine_id") or "routine")


async def _graph(ctx: RunContext, started: dict[str, Any]) -> dict[str, Any] | None:
    """The graph this attempt ran: its pinned revision, else the current graph."""
    routine_id = _routine_id(started)
    revision_id = started.get("revision_id")
    if isinstance(revision_id, str) and revision_id and ctx.revision_reader is not None:
        try:
            pinned = await ctx.revision_reader.fetch(routine_id, revision_id)
        except Exception:
            pinned = None
        if isinstance(pinned, dict):
            return pinned
    current = await ctx.state_manager.get_routine_graph(routine_id)
    return current if isinstance(current, dict) else None


def _compact(value: Any, steps: Steps) -> Any:
    """Front-matter value: as stored when small, otherwise a description."""
    if value is None:
        return None
    if len(json.dumps(value, ensure_ascii=False, default=str)) <= 300:
        return value
    return describe_value(value, pointer=steps.add(value))


def _attempt_window(
    ctx: RunContext, started: dict[str, Any]
) -> tuple[datetime | None, datetime | None]:
    """Continue resumes on the same thread, so a thread's steps belong to the
    attempt that was running when they started."""
    begin = at(started.get("timestamp"))
    thread_id = started.get("thread_id")
    later = sorted(
        moment
        for event in ctx.routine_events
        if event.get("status") == "started"
        and event.get("thread_id") == thread_id
        and event.get("request_id") != started.get("request_id")
        and (moment := at(event.get("timestamp"))) is not None
        and begin is not None
        and moment > begin
    )
    return begin, (later[0] if later else None)


async def _run_meta(ctx: RunContext, thread_id: str) -> list[tuple[str, dict[str, Any]]]:
    if ctx.checkpointer is None or not thread_id:
        return []
    tip = await ctx.checkpointer.aget_tuple(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    )
    if tip is None:
        return []
    values = tip.checkpoint.get("channel_values", {})
    meta = values.get("run_meta") if isinstance(values, dict) else None
    entries: list[tuple[str, dict[str, Any]]] = []
    if isinstance(meta, dict):
        for node_id, runs in meta.items():
            if isinstance(runs, list):
                entries.extend((str(node_id), run) for run in runs if isinstance(run, dict))
    return sorted(entries, key=lambda pair: pair[1].get("step") or 0)


def _step_output(entry: dict[str, Any], node: dict[str, Any], node_id: str) -> Any:
    """A node's own output value from its ``run_meta`` data update."""
    output = entry.get("output")
    if isinstance(output, dict):
        key = str(node.get("output_key") or node_id)
        if key in output and len(output) == 1:
            return output[key]
    return output


async def _child_attempt(ctx: RunContext, entry: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve a nested routine only through its persisted invocation identity."""
    invocation = entry.get("routine")
    if not isinstance(invocation, dict):
        return None
    request_id = invocation.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return None
    thread_id = invocation.get("thread_id")
    if isinstance(thread_id, str) and thread_id:
        await ctx.load_thread_events(thread_id)
    return next(
        (
            event
            for event in ctx.routine_events
            if event.get("status") == "started" and event.get("request_id") == request_id
        ),
        None,
    )


async def routine_log(
    ctx: RunContext,
    started: dict[str, Any],
    *,
    steps: Steps,
    depth: int = 0,
    channel: str | None = None,
) -> tuple[dict[str, Any], RunLog, str]:
    """Front matter, log lines and channel of one attempt (children inlined)."""
    request_id = str(started.get("request_id") or "")
    thread_id = str(started.get("thread_id") or "")
    await ctx.load_thread_events(thread_id)
    events = attempt_events(ctx, request_id) or [started]
    status, latest = attempt_status(ctx, events)
    graph = await _graph(ctx, started)
    routine_id = _routine_id(started)
    title = str((graph or {}).get("name") or started.get("name") or routine_id)
    nick = slug(title)
    channel = channel or f"{nick}-{short_id(request_id)}"
    nodes = {
        str(node.get("id")): node
        for node in (graph or {}).get("nodes", [])
        if isinstance(node, dict)
    }
    log = RunLog(nick=nick)
    begin, end = _attempt_window(ctx, started)
    tokens: dict[str, int | None] | None = None
    last_label: str | None = None
    for node_id, entry in await _run_meta(ctx, thread_id):
        when = at(entry.get("started_at"))
        if begin is not None and when is not None and when < begin:
            continue
        if end is not None and when is not None and when >= end:
            continue
        node = nodes.get(node_id, {})
        kind = str(node.get("kind") or "step")
        label = str(node.get("label") or node_id)
        raw_config = node.get("config")
        config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
        last_label = label
        value = _step_output(entry, node, node_id)
        if kind == "routine" and depth < MAX_CHILD_DEPTH:
            child = await _child_attempt(ctx, entry)
            if child is not None:
                child_front = await inline_child(
                    ctx, log, child, steps=steps, depth=depth, channel=channel
                )
                tokens = add_tokens(
                    tokens, child_front.get("tokens") or recorded_tokens(entry.get("tokens"))
                )
                continue
        if kind != "agent":
            tokens = add_tokens(tokens, recorded_tokens(entry.get("tokens")))
        if kind == "agent":
            agent = str(config.get("agent_name") or label)
            transcript_tokens: list[dict[str, int | None] | None] = []
            if await agent_transcript(
                ctx,
                log,
                f"routine-agent:{thread_id}:{node_id}",
                agent=agent,
                channel=channel,
                when=when,
                steps=steps,
                begin=begin,
                end=end,
                token_totals=transcript_tokens,
            ):
                usage = transcript_tokens[0] if transcript_tokens else None
                tokens = add_tokens(tokens, usage or recorded_tokens(entry.get("tokens")))
                continue
            tokens = add_tokens(tokens, recorded_tokens(entry.get("tokens")))
            log.act(when, f"asks {agent} → {step_result(value, steps)}")
            continue
        if kind == "prompt" and isinstance(value, str):
            speaker = f"@{slug(label)}"
            log.agents.add(slug(label))
            log.say(when, speaker, value)
            continue
        verb = _VERBS.get(kind, kind)
        if kind in _ROUTED and entry.get("route"):
            log.act(when, f"{verb} {label} → {entry['route']}")
            continue
        if kind == "a2a":
            verb = f"asks {config.get('agent_name') or label}"
            log.act(when, f"{verb} → {step_result(value, steps)}")
            continue
        log.act(when, f"{verb} {label} → {step_result(value, steps)}")

    closed = at(latest.get("timestamp"))
    error = latest.get("error")
    if status == "failed":
        reason = one_line(str(error or "failed"))
        log.event(
            closed, f"{nick} has quit [failed{f' at {last_label}' if last_label else ''}: {reason}]"
        )
    elif status == "interrupted":
        where = f" after {last_label}" if last_label else ""
        log.event(None, f"{nick} has quit [runtime restarted{where}; interrupted]")
    elif status in {"cancelled", "stopped"}:
        log.event(closed, f"{nick} has quit [{status}]")
    elif status == "paused":
        choices = " / ".join(str(choice) for choice in latest.get("allowed_responses") or [])
        question = one_line(str(latest.get("question") or "Waiting for a response"))
        log.lines.append(_notice(closed, nick, question, choices))

    output = latest.get("output") if status == "completed" else None
    front = {
        "kind": "routine",
        "routine": title,
        "routine_id": routine_id,
        "request_id": request_id,
        "thread_id": thread_id or None,
        "revision_id": started.get("revision_id"),
        "status": status,
        "started_at": started.get("timestamp"),
        "ended_at": latest.get("timestamp") if status in TERMINAL_STATUSES else None,
        "queued_at": next(
            (event.get("timestamp") for event in events if event.get("status") == "enqueued"),
            None,
        ),
        "triggered_by": _trigger(started),
        "inputs": _compact(started.get("inputs"), steps),
        "output": _compact(output, steps),
        "error": error if status == "failed" else None,
        "attempts": _attempts(ctx, started),
        "tokens": tokens,
    }
    return front, log, channel


def _notice(when: datetime | None, nick: str, question: str, choices: str) -> Line:
    return Line(when, f"-{nick}- {question}" + (f" [{choices}]" if choices else ""))


def _trigger(started: dict[str, Any]) -> str | None:
    session_id = str(started.get("session_id") or "")
    if session_id.startswith("workflow_run_"):
        return f"workflow {session_id.rsplit('_', 1)[0]}"
    parent = started.get("parent")
    if isinstance(parent, dict) and parent.get("thread_id"):
        return f"routine {parent['thread_id']}"
    return None


def _attempts(ctx: RunContext, started: dict[str, Any]) -> dict[str, str] | None:
    """Other attempts on the same thread (Continue resumes there)."""
    thread_id = started.get("thread_id")
    begin = str(started.get("timestamp") or "")
    others = sorted(
        (str(event.get("timestamp") or ""), str(event.get("request_id") or ""))
        for event in ctx.routine_events
        if event.get("status") == "started"
        and event.get("thread_id") == thread_id
        and event.get("request_id") != started.get("request_id")
    )
    previous = [rid for moment, rid in others if moment < begin]
    following = [rid for moment, rid in others if moment > begin]
    result = {}
    if previous:
        result["previous"] = short_id(previous[-1])
    if following:
        result["next"] = short_id(following[0])
    return result or None


async def inline_child(
    ctx: RunContext,
    log: RunLog,
    started: dict[str, Any],
    *,
    steps: Steps,
    depth: int,
    channel: str,
) -> dict[str, Any]:
    """A child routine run between join and part, ending with its result."""
    front, child, _ = await routine_log(ctx, started, steps=steps, depth=depth + 1, channel=channel)
    first = next((line.at for line in child.lines if line.at), at(started.get("timestamp")))
    log.event(first, f"{child.nick} [routine] has joined #{channel}")
    log.merge(child)
    last = next((line.at for line in reversed(child.lines) if line.at), first)
    # The front matter holds a summary; the result line needs the stored value.
    completed = next(
        (
            event
            for event in attempt_events(ctx, str(started.get("request_id") or ""))
            if event.get("status") == "completed"
        ),
        None,
    )
    output = completed.get("output") if completed else None
    if output is not None:
        value = (
            output.get("message")
            if isinstance(output, dict) and set(output) == {"message"}
            else output
        )
        if isinstance(value, str) and child.last_speech and value.strip() == child.last_speech:
            log.lines.append(_say_line(last, child.nick, "(the reply above)"))
        elif isinstance(value, str):
            log.lines.append(
                _say_line(last, child.nick, head_tail(value, pointer=steps.add(value)))
            )
        else:
            log.lines.append(
                _say_line(last, child.nick, describe_value(value, pointer=steps.add(value)))
            )
    status = front.get("status")
    reason = "" if status == "completed" else f" [{status}]"
    log.event(last, f"{child.nick} [routine] has left #{channel}{reason}")
    return front


def _say_line(when: datetime | None, nick: str, text: str) -> Line:
    return Line(when, f"<{nick}> {text}")


async def render_routine(
    ctx: RunContext, started: dict[str, Any], *, path: str
) -> tuple[str, Steps]:
    """The whole file for one top-level routine attempt."""
    steps = Steps(path=path)
    front, log, channel = await routine_log(ctx, started, steps=steps)
    inputs = started.get("inputs")
    topic = f"{front['routine']}" + (
        f" · {describe_value(inputs)}" if isinstance(inputs, dict) and inputs else ""
    )
    return render_file(front, log, channel=channel, topic=topic, ctx=ctx), steps
