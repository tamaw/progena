"""Render one scheduled run: the schedule's message and the agent turn it started."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from langchain_core.messages import HumanMessage

from src.progena.runtime.ownership import schedule_run_interrupted
from src.progena.schedules.dispatch import SCHEDULED_INPUT_PREFIX
from src.progena.schedules.models import ScheduleRunStatus
from src.progena.sessions.transcript import render_transcript
from src.progena.settings.preferences import agent_display_name

from ._render import RunContext, RunLog, Steps, at, conversation_tokens, render_file
from .paths import short_id, slug
from .values import one_line

_SCHEDULER = "scheduler"


def schedule_title(record: dict[str, Any], schedules: dict[str, Any]) -> str:
    entry = schedules.get(str(record.get("schedule_id") or ""))
    name = getattr(entry, "name", None)
    if name is None and entry is not None:
        name = (getattr(entry, "model_extra", None) or {}).get("name")
    return str(name or record.get("schedule_id") or "schedule")


def _without_prefix(message: Any) -> Any:
    """The scheduled message as the schedule wrote it, without the dispatch marker."""
    if (
        isinstance(message, HumanMessage)
        and isinstance(message.content, str)
        and message.content.startswith(SCHEDULED_INPUT_PREFIX)
    ):
        return message.model_copy(
            update={"content": message.content[len(SCHEDULED_INPUT_PREFIX) :]}
        )
    return message


async def render_schedule(
    ctx: RunContext, record: dict[str, Any], *, title: str, path: str
) -> tuple[str, Steps]:
    steps = Steps(path=path)
    run_id = str(record.get("run_id") or "")
    nick = _SCHEDULER
    channel = f"{slug(title)}-{short_id(run_id)}"
    log = RunLog(nick=nick)
    status = str(record.get("status") or "queued").lower()
    if schedule_run_interrupted(record, ctx.live_instance, ctx.live_started_at):
        status = "interrupted"
    thread_id = str(record.get("thread_id") or "")
    conversation = await ctx.conversation(thread_id)
    if conversation is not None:
        preferences = await ctx.preferences()
        agent = "".join(agent_display_name(preferences).split())
        root = replace(
            conversation.root,
            nick=agent,
            messages=[_without_prefix(message) for message in conversation.root.messages],
        )
        transcript = render_transcript(
            root, subagents=conversation.subagents, channel=channel, user_nick=nick
        )
        log.lines.extend(transcript.lines)
        log.agents |= transcript.agents
    else:
        log.say(at(record.get("started_at")), nick, str(record.get("message") or ""))
    tokens = (
        conversation_tokens(
            conversation,
            begin=at(record.get("started_at") or record.get("created_at")),
            end=at(record.get("completed_at")),
        )
        if conversation is not None
        else None
    )
    ended = at(record.get("completed_at") or record.get("updated_at"))
    error = record.get("error")
    if status == ScheduleRunStatus.ERROR:
        log.event(ended, f"{nick} has quit [failed: {one_line(str(error or 'failed'))}]")
    elif status == "interrupted":
        log.event(None, f"{nick} has quit [runtime restarted; interrupted]")
    front = {
        "kind": "schedule",
        "schedule": title,
        "schedule_id": record.get("schedule_id"),
        "run_id": run_id,
        "thread_id": thread_id or None,
        "status": status,
        "result_status": record.get("result_status"),
        "started_at": record.get("started_at") or record.get("created_at"),
        "ended_at": record.get("completed_at"),
        "error": error if status == ScheduleRunStatus.ERROR else None,
        "tokens": tokens,
    }
    topic = f"{title} · {one_line(str(record.get('message') or ''))[:80]}"
    return render_file(front, log, channel=channel, topic=topic, ctx=ctx), steps
