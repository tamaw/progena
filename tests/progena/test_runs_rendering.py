from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from langchain_core.messages import AIMessage

from src.progena.runs._render import RunContext, conversation_tokens, recorded_tokens
from src.progena.runs.routine import render_routine
from src.progena.runs.schedule import render_schedule
from src.progena.runs.workflow import render_workflow
from src.progena.sessions.history import Conversation
from src.progena.sessions.transcript import ThreadMessages


class _Checkpoints:
    def __init__(self, run_meta: dict[str, dict[str, list[dict[str, Any]]]]) -> None:
        self.run_meta = run_meta

    async def aget_tuple(self, config: dict[str, Any]) -> Any:
        meta = self.run_meta.get(config["configurable"]["thread_id"])
        return (
            SimpleNamespace(checkpoint={"channel_values": {"run_meta": meta}})
            if meta is not None
            else None
        )


class _State:
    def __init__(self, events: list[dict[str, Any]], graphs: dict[str, dict[str, Any]]) -> None:
        self.events = events
        self.graphs = graphs
        self.definition: dict[str, Any] = {}

    async def get_routine_graph(self, routine_id: str) -> dict[str, Any] | None:
        return self.graphs.get(routine_id)

    async def get_workflow_definition(self, _workflow_id: str) -> dict[str, Any]:
        return self.definition

    async def get_preferences_config(self) -> dict[str, Any]:
        return {"timezone": "UTC"}

    async def list_routine_run_events(
        self, *, thread_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        if thread_ids is None:
            return list(self.events)
        return [event for event in self.events if event.get("thread_id") in thread_ids]


def _context(state: _State, checkpoints: _Checkpoints, events: list[dict[str, Any]]) -> RunContext:
    return RunContext(
        state_manager=state,
        checkpointer=checkpoints,
        revision_reader=None,
        tz=ZoneInfo("UTC"),
        tz_name="UTC",
        live_instance=None,
        live_started_at=None,
        routine_events=events,
    )


async def test_nested_routine_uses_persisted_request_not_nearest_parent_event() -> None:
    root_thread = "routine:parent:root"
    right_thread = "routine:child:right"
    wrong_thread = "routine:child:wrong"
    root = {
        "request_id": "root-request",
        "status": "started",
        "timestamp": "2026-10-30T10:00:00+00:00",
        "agent_id": "parent",
        "thread_id": root_thread,
    }
    wrong = {
        "request_id": "wrong-request",
        "status": "started",
        "timestamp": "2026-10-30T10:00:01+00:00",
        "agent_id": "child",
        "thread_id": wrong_thread,
        "parent": {"thread_id": root_thread, "node_id": "invoke"},
    }
    right = {
        "request_id": "right-request",
        "status": "started",
        "timestamp": "2026-10-30T10:00:09+00:00",
        "agent_id": "child",
        "thread_id": right_thread,
    }
    events = [
        root,
        wrong,
        right,
        {
            **right,
            "status": "completed",
            "timestamp": "2026-10-30T10:00:10+00:00",
            "output": {"message": "Right result"},
        },
    ]
    graphs = {
        "parent": {"name": "Parent", "nodes": [{"id": "invoke", "kind": "routine"}]},
        "child": {"name": "Child", "nodes": [{"id": "reply", "kind": "prompt"}]},
    }
    checkpoints = _Checkpoints(
        {
            root_thread: {
                "invoke": [
                    {
                        "step": 1,
                        "started_at": "2026-10-30T10:00:02+00:00",
                        "routine": {"request_id": "right-request", "thread_id": right_thread},
                    }
                ]
            },
            right_thread: {
                "reply": [
                    {"step": 1, "started_at": "2026-10-30T10:00:09+00:00", "output": "Right only"}
                ]
            },
            wrong_thread: {
                "reply": [
                    {
                        "step": 1,
                        "started_at": "2026-10-30T10:00:01+00:00",
                        "output": "Wrong nearest",
                    }
                ]
            },
        }
    )
    state = _State(events, graphs)

    text, _ = await render_routine(
        _context(state, checkpoints, [root, wrong]),
        root,
        path="/runs/routines/root.md",
    )

    assert "Right only" in text
    assert "Wrong nearest" not in text


async def test_failed_workflow_audit_uses_historical_kind_and_thread_without_output() -> None:
    child_thread = "routine:child:failed"
    child = {
        "request_id": "child-request",
        "status": "started",
        "timestamp": "2026-10-30T10:00:01+00:00",
        "agent_id": "child",
        "thread_id": child_thread,
    }
    events = [
        child,
        {
            **child,
            "status": "failed",
            "timestamp": "2026-10-30T10:00:02+00:00",
            "error": "schema rejected",
        },
    ]
    state = _State(
        events,
        {"child": {"name": "Child", "nodes": [{"id": "work", "kind": "prompt"}]}},
    )
    state.definition = {
        "title": "Edited workflow",
        "graph": {"nodes": {"changed": {"kind": "data", "title": "Changed now"}}},
    }
    run = {
        "run_id": "workflow-run",
        "workflow_id": "workflow",
        "status": "failed",
        "started_at": "2026-10-30T10:00:00+00:00",
        "completed_at": "2026-10-30T10:00:03+00:00",
        "last_error": "routine schema rejected",
        "step_audit": [
            {
                "node_id": "changed",
                "kind": "routine",
                "thread_id": child_thread,
                "started_at": "2026-10-30T10:00:00+00:00",
                "completed_at": "2026-10-30T10:00:03+00:00",
                "status": "failed",
                "error": "schema rejected",
            }
        ],
        "node_outputs": {},
        "tokens": {"input_tokens": 999, "output_tokens": 0, "total_tokens": 999},
    }

    text, _ = await render_workflow(
        _context(
            state,
            _Checkpoints(
                {
                    child_thread: {
                        "work": [
                            {
                                "step": 1,
                                "started_at": "2026-10-30T10:00:01+00:00",
                                "output": "Recorded failed child work",
                                "tokens": {"total_tokens": 17},
                            }
                        ]
                    }
                }
            ),
            [],
        ),
        run,
        path="/runs/workflows/workflow.md",
    )

    front = yaml.safe_load(text.split("---\n")[1])
    assert "Recorded failed child work" in text
    assert front["tokens"]["total_tokens"] == 17


def test_recorded_tokens_distinguishes_zero_from_unknown_and_scopes_nested_messages() -> None:
    before = AIMessage(
        content="before",
        id="before",
        usage_metadata={"input_tokens": 99, "output_tokens": 0, "total_tokens": 99},
    )
    shared = AIMessage(
        content="shared",
        id="shared",
        usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
    )
    nested = AIMessage(
        content="nested",
        id="nested",
        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    )
    conversation = Conversation(
        root=ThreadMessages(
            messages=[before, shared],
            committed_at={
                "before": datetime(2026, 10, 30, 9, 59, tzinfo=UTC),
                "shared": datetime(2026, 10, 30, 10, 1, tzinfo=UTC),
            },
        ),
        subagents={
            "call": ThreadMessages(
                messages=[shared, nested],
                committed_at={
                    "shared": datetime(2026, 10, 30, 10, 1, tzinfo=UTC),
                    "nested": datetime(2026, 10, 30, 10, 2, tzinfo=UTC),
                },
            )
        },
        checkpoint_id="checkpoint",
    )

    assert recorded_tokens(None) is None
    assert recorded_tokens({"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    assert recorded_tokens({"total_tokens": 17}) == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": 17,
    }
    assert conversation_tokens(conversation, begin=datetime(2026, 10, 30, 10, 0, tzinfo=UTC)) == {
        "input_tokens": 3,
        "output_tokens": 4,
        "total_tokens": 7,
    }


async def test_agent_steps_count_conversation_usage_not_just_the_last_reply() -> None:
    thread_id = "routine:owner"
    started = {
        "request_id": "owner",
        "agent_id": "owner",
        "thread_id": thread_id,
        "status": "started",
        "timestamp": "2026-10-30T10:00:00Z",
    }
    state = _State(
        [started],
        {"owner": {"name": "Owner", "nodes": [{"id": "agent", "kind": "agent"}]}},
    )
    checkpoints = _Checkpoints(
        {
            thread_id: {
                "agent": [
                    {
                        "started_at": "2026-10-30T10:00:01Z",
                        "tokens": {"total_tokens": 5},
                    },
                    {
                        "started_at": "2026-10-30T10:00:04Z",
                        "tokens": {"total_tokens": 5},
                    },
                ]
            }
        }
    )
    messages = [
        AIMessage(
            content="First reply",
            id="one",
            usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
        ),
        AIMessage(
            content="Final reply",
            id="two",
            usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        ),
    ]
    times = {
        "one": datetime(2026, 10, 30, 10, 0, 2, tzinfo=UTC),
        "two": datetime(2026, 10, 30, 10, 0, 5, tzinfo=UTC),
    }
    conversation = Conversation(
        root=ThreadMessages(messages=messages, committed_at=times),
        subagents={"shared": ThreadMessages(messages=[messages[0]], committed_at=times)},
        checkpoint_id="tip",
    )
    ctx = _context(state, checkpoints, [])
    ctx.conversations[f"routine-agent:{thread_id}:agent"] = conversation
    text, _ = await render_routine(ctx, started, path="/runs/routines/owner.md")
    front = yaml.safe_load(text.split("---\n")[1])
    assert front["tokens"] == {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}
    assert text.count("First reply") == 1
    assert text.count("Final reply") == 1

    state.definition = {"title": "Workflow", "graph": {"nodes": {}}}
    ctx = _context(state, checkpoints, [])
    ctx.conversations["agent-thread"] = conversation
    run = {
        "run_id": "workflow",
        "workflow_id": "workflow",
        "tokens": {"total_tokens": 999},
        "step_audit": [
            {
                "node_id": "agent",
                "kind": "agent",
                "thread_id": "agent-thread",
                "started_at": "2026-10-30T10:00:00Z",
                "tokens": {"total_tokens": 5},
            }
        ],
    }
    text, _ = await render_workflow(ctx, run, path="/runs/workflows/owner.md")
    assert yaml.safe_load(text.split("---\n")[1])["tokens"]["total_tokens"] == 12

    ctx = _context(state, checkpoints, [])
    ctx.conversations["agent-thread"] = conversation
    text, _ = await render_schedule(
        ctx,
        {"run_id": "schedule", "thread_id": "agent-thread", "status": "completed"},
        title="Schedule",
        path="/runs/schedules/owner.md",
    )
    assert yaml.safe_load(text.split("---\n")[1])["tokens"]["total_tokens"] == 12


async def test_agent_invocations_exclude_gap_messages_and_later_attempts() -> None:
    thread_id = "routine:bounded"
    started = {
        "request_id": "bounded",
        "agent_id": "bounded",
        "thread_id": thread_id,
        "status": "started",
        "timestamp": "2026-10-30T10:00:00Z",
    }
    next_attempt = {**started, "request_id": "resumed", "timestamp": "2026-10-30T10:00:05Z"}
    state = _State(
        [started, next_attempt],
        {"bounded": {"name": "Bounded", "nodes": [{"id": "agent", "kind": "agent"}]}},
    )
    checkpoints = _Checkpoints(
        {
            thread_id: {
                "agent": [
                    {"started_at": "2026-10-30T10:00:01Z", "duration_ms": 2000},
                    {"started_at": "2026-10-30T10:00:04Z", "duration_ms": 10000},
                ]
            }
        }
    )
    entries = [
        ("before", 0, 99),
        ("first", 2, 7),
        ("gap", 3, 99),
        ("second", 4, 5),
        ("later", 5, 99),
    ]
    conversation = Conversation(
        root=ThreadMessages(
            messages=[
                AIMessage(
                    content=f"Unique {name} answer",
                    id=name,
                    usage_metadata={
                        "input_tokens": tokens,
                        "output_tokens": 0,
                        "total_tokens": tokens,
                    },
                )
                for name, _, tokens in entries
            ],
            committed_at={
                name: datetime(2026, 10, 30, 10, 0, second, tzinfo=UTC)
                for name, second, _ in entries
            },
        ),
        subagents={},
        checkpoint_id="tip",
    )
    ctx = _context(state, checkpoints, [])
    ctx.conversations[f"routine-agent:{thread_id}:agent"] = conversation
    text, _ = await render_routine(ctx, started, path="/runs/routines/bounded.md")
    assert text.count("Unique first answer") == 1
    assert text.count("Unique second answer") == 1
    for name in ("before", "gap", "later"):
        assert f"Unique {name} answer" not in text
    assert yaml.safe_load(text.split("---\n")[1])["tokens"]["total_tokens"] == 12
