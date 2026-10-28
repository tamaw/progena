"""Render one workflow run as an IRC log.

Everything comes from the ``workflow_run:`` document: ``step_audit`` gives the
order and time of each step, ``node_outputs`` the full values, and the
definition the step titles. Routine and agent steps inline their own runs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from src.progena.sessions.transcript import Line

from ._render import RunContext, RunLog, Steps, agent_transcript, at, render_file, step_result
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
    """The final step's full output; ``last_output`` is stored already shortened."""
    outputs = run.get("node_outputs")
    audit = [step for step in run.get("step_audit") or [] if isinstance(step, Mapping)]
    if audit and isinstance(outputs, Mapping):
        last = outputs.get(str(audit[-1].get("node_id") or ""))
        if last is not None:
            return last
    raw = run.get("last_output")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _child_attempt(ctx: RunContext, thread_id: str | None) -> dict[str, Any] | None:
    if not thread_id:
        return None
    return next(
        (
            event
            for event in ctx.routine_events
            if event.get("status") == "started" and event.get("thread_id") == thread_id
        ),
        None,
    )


async def render_workflow(
    ctx: RunContext, run: Mapping[str, Any], *, path: str
) -> tuple[str, Steps]:
    """The whole file for one workflow run."""
    steps = Steps(path=path)
    workflow_id = str(run.get("workflow_id") or "")
    definition = await ctx.state_manager.get_workflow_definition(workflow_id) or {}
    title = workflow_title(run, definition)
    nick = slug(title)
    run_id = str(run.get("run_id") or "")
    channel = f"{nick}-{short_id(run_id)}"
    nodes = _nodes(definition)
    outputs = run.get("node_outputs") if isinstance(run.get("node_outputs"), Mapping) else {}
    log = RunLog(nick=nick)

    for audit in run.get("step_audit") or []:
        if not isinstance(audit, Mapping):
            continue
        node_id = str(audit.get("node_id") or "")
        node = nodes.get(node_id, {})
        kind = str(node.get("kind") or audit.get("kind") or "step")
        label = str(node.get("title") or node_id or kind)
        when = at(audit.get("completed_at"))
        value = outputs.get(node_id, audit.get("output")) if isinstance(outputs, Mapping) else None
        if kind == "routine":
            thread_id = value.get("thread_id") if isinstance(value, Mapping) else None
            child = _child_attempt(ctx, thread_id if isinstance(thread_id, str) else None)
            if child is not None:
                await inline_child(ctx, log, child, steps=steps, depth=0, channel=channel)
                continue
            shown = (
                {k: v for k, v in value.items() if k != "thread_id"}
                if isinstance(value, Mapping)
                else value
            )
            log.act(when, f"runs {label} → {step_result(shown, steps)}")
            continue
        if kind == "agent" and isinstance(value, Mapping):
            agent = str(node.get("subagent") or "progena")
            thread_id = value.get("thread_id")
            if isinstance(thread_id, str) and await agent_transcript(
                ctx, log, thread_id, agent=agent, channel=channel, when=when
            ):
                continue
        if kind == "condition" and isinstance(value, Mapping):
            branch = next((str(port) for port, taken in value.items() if taken is True), None)
            if branch is not None:
                log.act(when, f"branches at {label} → {branch}")
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
        "tokens": run.get("tokens"),
    }
    return render_file(front, log, channel=channel, topic=_topic(run, title), ctx=ctx), steps
