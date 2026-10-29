"""Render one workflow run as an IRC log.

Everything comes from the ``workflow_run:`` document: ``step_audit`` gives the
order and time of each step, ``node_outputs`` the full values, and the
definition the step titles. Routine and agent steps inline their own runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.progena.sessions.transcript import Line

from ._render import (
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
from .routine import inline_child
from .values import describe_change, describe_value, one_line

_VERBS = {
    "data": "sets",
    "document_read": "reads",
    "document_query": "queries",
    "document_write": "updates",
    "document_create": "creates",
    "document_delete": "deletes",
    "unpack": "unpacks",
    "format": "formats",
    "extract": "extracts",
    "agent": "asks",
    "routine": "runs",
    "condition": "branches at",
    "approval": "asks",
}
_TERMINAL = frozenset({"completed", "failed", "cancelled"})


def _nodes(definition: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    graph = definition.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
    if isinstance(nodes, Mapping):
        return {str(key): node for key, node in nodes.items() if isinstance(node, dict)}
    if isinstance(nodes, list):
        return {str(node.get("id")): node for node in nodes if isinstance(node, dict)}
    return {}


def workflow_title(run: Mapping[str, Any], definition: Mapping[str, Any] | None) -> str:
    return str((definition or {}).get("title") or run.get("workflow_id") or "workflow")


def _topic(run: Mapping[str, Any], title: str) -> str:
    """What started the run: the document change, the cron tick, or its inputs."""
    source = str(run.get("source") or "manual")
    trigger = run.get("trigger_event")
    if isinstance(trigger, Mapping) and ("after" in trigger or "changed_fields" in trigger):
        return f"{title} · {describe_change(trigger)}"
    if source == "cron" and isinstance(trigger, Mapping):
        when = trigger.get("scheduled_for") or trigger.get("fired_at")
        return f"{title} · cron {when}" if when else f"{title} · cron"
    if isinstance(trigger, Mapping):
        inputs = {k: v for k, v in trigger.items() if not str(k).startswith("_")}
        if inputs:
            return f"{title} · started by {source} · {describe_value(inputs)}"
    return f"{title} · started by {source}"


def _last_output(run: Mapping[str, Any]) -> Any:
    """The final step's authoritative output, falling back to its display text."""
    outputs = run.get("node_outputs")
    audit = [step for step in run.get("step_audit") or [] if isinstance(step, Mapping)]
    if audit and isinstance(outputs, Mapping):
        node_id = str(audit[-1].get("node_id") or "")
        if node_id in outputs:
            return outputs[node_id]
    return run.get("last_output")


async def _child_attempts(
    ctx: RunContext, audit: Mapping[str, Any], value: Any
) -> list[dict[str, Any]]:
    """Find every causally-owned routine attempt in this workflow step."""
    audit_thread = audit.get("thread_id")
    output_thread = value.get("thread_id") if isinstance(value, Mapping) else None
    thread_id = audit_thread if isinstance(audit_thread, str) and audit_thread else output_thread
    if not isinstance(thread_id, str) or not thread_id:
        return []
    await ctx.load_thread_events(thread_id)
    begin = at(audit.get("started_at"))
    end = at(audit.get("completed_at"))
    candidates: list[dict[str, Any]] = []
    for event in ctx.routine_events:
        if (
            event.get("status") != "started"
            or event.get("thread_id") != thread_id
            or not isinstance(event.get("request_id"), str)
            or not event["request_id"]
        ):
            continue
        when = at(event.get("timestamp"))
        if (begin is not None or end is not None) and when is None:
            continue
        if begin is not None and when is not None and when < begin:
            continue
        if end is not None and when is not None and when >= end:
            continue
        candidates.append(event)
    request_id = audit.get("request_id")
    if isinstance(request_id, str) and request_id:
        anchor = next((event for event in candidates if event["request_id"] == request_id), None)
        if anchor is None:
            return []
        session_id = anchor.get("session_id")
        candidates = (
            [event for event in candidates if event.get("session_id") == session_id]
            if isinstance(session_id, str) and session_id
            else [anchor]
        )
    ordered = sorted(
        candidates,
        key=lambda event: (str(event.get("timestamp") or ""), event["request_id"]),
    )
    attempts: list[dict[str, Any]] = []
    seen_request_ids: set[str] = set()
    for event in ordered:
        request_id = event["request_id"]
        if request_id not in seen_request_ids:
            attempts.append(event)
            seen_request_ids.add(request_id)
    return attempts


async def render_workflow(
    ctx: RunContext, run: Mapping[str, Any], *, path: str
) -> tuple[str, Steps]:
    """The whole file for one workflow run."""
    steps = Steps(path=path)
    workflow_id = str(run.get("workflow_id") or "")
    definition = await ctx.workflow_definition(workflow_id) or {}
    title = workflow_title(run, definition)
    nick = slug(title)
    run_id = str(run.get("run_id") or "")
    channel = f"{nick}-{short_id(run_id)}"
    nodes = _nodes(definition)
    raw_outputs = run.get("node_outputs")
    outputs = raw_outputs if isinstance(raw_outputs, Mapping) else {}
    log = RunLog(nick=nick)

    tokens: dict[str, int | None] | None = None
    for audit in run.get("step_audit") or []:
        if not isinstance(audit, Mapping):
            continue
        node_id = str(audit.get("node_id") or "")
        node = nodes.get(node_id, {})
        kind = str(audit.get("kind") or node.get("kind") or "step")
        label = str(node.get("title") or node_id or kind)
        when = at(audit.get("completed_at") or audit.get("started_at"))
        value = outputs[node_id] if node_id in outputs else audit.get("output")
        audit_tokens = recorded_tokens(audit.get("tokens"))
        if kind == "routine":
            children = await _child_attempts(ctx, audit, value)
            if children:
                child_tokens: dict[str, int | None] | None = None
                for child in children:
                    child_front = await inline_child(
                        ctx, log, child, steps=steps, depth=0, channel=channel
                    )
                    child_tokens = add_tokens(child_tokens, child_front.get("tokens"))
                tokens = add_tokens(
                    tokens, child_tokens if child_tokens is not None else audit_tokens
                )
                continue
        if kind not in {"agent", "extract"}:
            tokens = add_tokens(tokens, audit_tokens)
        audit_thread = audit.get("thread_id")
        output_thread = value.get("thread_id") if isinstance(value, Mapping) else None
        thread_id = (
            audit_thread if isinstance(audit_thread, str) and audit_thread else output_thread
        )
        if kind in {"agent", "extract"} and isinstance(thread_id, str) and thread_id:
            agent = str(node.get("subagent") or "progena")
            transcript_tokens: list[dict[str, int | None] | None] = []
            if await agent_transcript(
                ctx,
                log,
                thread_id,
                agent=agent,
                channel=channel,
                when=when,
                steps=steps,
                begin=at(audit.get("started_at")),
                end=at(audit.get("completed_at")),
                token_totals=transcript_tokens,
            ):
                usage = transcript_tokens[0] if transcript_tokens else None
                tokens = add_tokens(tokens, usage or audit_tokens)
                continue
        if kind in {"agent", "extract"}:
            tokens = add_tokens(tokens, audit_tokens)
        if kind == "condition" and isinstance(value, Mapping):
            branch = next((str(port) for port, taken in value.items() if taken is True), None)
            if branch is not None:
                log.act(when, f"branches at {label} → {branch}")
                continue
        failure = audit.get("error")
        if isinstance(failure, str) and failure:
            log.act(when, f"{_VERBS.get(kind, kind)} {label} → failed: {one_line(failure)}")
            continue
        verb = _VERBS.get(kind, kind)
        log.act(when, f"{verb} {label} → {step_result(value, steps)}")
    status = str(run.get("status") or "queued")
    pending = run.get("pending_approval")
    if isinstance(pending, Mapping):
        asked = at(pending.get("requested_at"))
        message = one_line(str(pending.get("message") or pending.get("title") or "Approve?"))
        log.lines.append(Line(asked, f"-{nick}- {message} [approve / reject]"))
        if pending.get("approved") is not None:
            answer = "approve" if pending.get("approved") else "reject"
            log.say(at(pending.get("resolved_at")), "user", answer)
    ended = at(run.get("completed_at") or run.get("updated_at"))
    error = run.get("last_error")
    if status == "failed":
        log.event(ended, f"{nick} has quit [failed: {one_line(str(error or 'failed'))}]")
    elif status == "cancelled":
        log.event(ended, f"{nick} has quit [cancelled]")
    elif status == "running" and isinstance(run.get("pending_routine"), Mapping):
        waiting = run["pending_routine"]
        log.event(None, f"{nick} is waiting on routine {waiting.get('routine_id')}")

    output = _last_output(run) if status == "completed" else None
    front = {
        "kind": "workflow",
        "workflow": title,
        "workflow_id": workflow_id or None,
        "run_id": run_id,
        "status": status,
        "started_at": run.get("started_at") or run.get("queued_at"),
        "ended_at": (run.get("completed_at") or run.get("updated_at"))
        if status in _TERMINAL
        else None,
        "queued_at": run.get("queued_at"),
        "trigger": {
            key: value
            for key, value in {
                "source": run.get("source"),
                "key": (run.get("trigger_event") or {}).get("key")
                if isinstance(run.get("trigger_event"), Mapping)
                else None,
                "op": (run.get("trigger_event") or {}).get("op")
                if isinstance(run.get("trigger_event"), Mapping)
                else None,
            }.items()
            if value
        },
        "waiting_on": run.get("waiting_node_id"),
        "output": describe_value(output, pointer=steps.add(output)) if output is not None else None,
        "error": error if status == "failed" else None,
        "tokens": tokens,
    }
    return render_file(front, log, channel=channel, topic=_topic(run, title), ctx=ctx), steps
