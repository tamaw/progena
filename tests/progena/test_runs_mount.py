"""The /runs/ mount: which runs are listed, how each reads, and step lookups.

Documents mirror the real shapes: immutable ``routine_run_event:`` records with
``run_meta`` in the routine checkpoint, ``workflow_run:`` documents, and the
``schedule_runs`` list.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from langgraph.store.memory import InMemoryStore

from src.progena.agent.filesystem.mounts import create_progena_backend
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.runtime.instance import RUNTIME_INSTANCE_ID
from src.progena.state.manager import StateManager

_NS = ("progena", "state")
_THREAD = "routine:smoke:rev1:9150394e"
_GRAPH = {
    "id": "smoke",
    "name": "smoke-interrupt",
    "nodes": [
        {"id": "fetch", "kind": "eval", "label": "Fetch", "output_key": "fetch"},
        {"id": "work", "kind": "prompt", "label": "Slow work", "output_key": "work"},
        {"id": "report", "kind": "eval", "label": "Report", "output_key": "report"},
    ],
}


class _Checkpoints:
    """Latest root checkpoint per thread; no message history."""

    def __init__(self) -> None:
        self.run_meta: dict[str, dict[str, list[dict[str, Any]]]] = {}

    async def aget_tuple(self, config: dict[str, Any]) -> Any:
        meta = self.run_meta.get(config["configurable"]["thread_id"])
        if meta is None:
            return None
        return SimpleNamespace(checkpoint={"channel_values": {"run_meta": meta}})

    async def alist(self, config: dict[str, Any]):
        return
        yield


def _step(step: int, at: str, output: dict[str, Any]) -> dict[str, Any]:
    return {"step": step, "started_at": at, "duration_ms": 5.0, "output": output}


class _Runs:
    def __init__(self) -> None:
        self.store = InMemoryStore()
        self.state_manager = StateManager(LangGraphRuntimeStore(self.store))
        self.checkpoints = _Checkpoints()
        self.fs = create_progena_backend(
            state_manager=self.state_manager, checkpointer=self.checkpoints
        )

    async def put(self, key: str, payload: Any) -> None:
        await self.store.aput(_NS, key, {"payload": payload})

    async def event(self, request_id: str, status: str, at: str, **fields: Any) -> None:
        payload = {"request_id": request_id, "status": status, "timestamp": at, **fields}
        await self.put(f"routine_run_event:{request_id}:{status}", payload)

    async def names(self, directory: str) -> list[str]:
        listing = await self.fs.als(f"/runs/{directory}/")
        return [entry["path"] for entry in listing.entries or []]

    async def text(self, path: str) -> str:
        result = await self.fs.aread(path, 0, 10_000)
        assert result.error is None, result.error
        return result.file_data["content"]


@pytest.fixture
async def runs() -> _Runs:
    runs = _Runs()
    await runs.put("routine:smoke", _GRAPH)
    return runs


async def _continued_run(runs: _Runs) -> None:
    """Attempt 9bb9157f stopped after Fetch; Continue resumed it as 9fd7eb88 on the same thread."""
    shared = {"agent_id": "smoke", "thread_id": _THREAD, "runtime_instance": "old"}
    await runs.event(
        "9bb9157f-1", "started", "2026-10-28T02:54:57+00:00", **shared, inputs={"topic": "bitcoin"}
    )
    await runs.event("9bb9157f-1", "stopped", "2026-10-28T02:56:10+00:00", **shared)
    await runs.event("9fd7eb88-2", "started", "2026-10-28T02:56:24+00:00", **shared)
    await runs.event(
        "9fd7eb88-2",
        "completed",
        "2026-10-28T02:56:25+00:00",
        **shared,
        output={"message": "Done."},
    )
    runs.checkpoints.run_meta[_THREAD] = {
        "fetch": [_step(1, "2026-10-28T02:54:57+00:00", {"fetch": "bitcoin"})],
        "work": [_step(2, "2026-10-28T02:56:24+00:00", {"work": "Done."})],
        "report": [_step(3, "2026-10-28T02:56:25+00:00", {"report": "reported"})],
    }


async def test_each_attempt_on_a_continued_thread_shows_only_its_own_steps(runs: _Runs) -> None:
    await _continued_run(runs)
    assert await runs.names("routines") == [
        "/runs/routines/2026-10-28_smoke-interrupt_9fd7eb88-2.md",
        "/runs/routines/2026-10-28_smoke-interrupt_9bb9157f-1.md",
    ]

    stopped = await runs.text("/runs/routines/2026-10-28_smoke-interrupt_9bb9157f-1.md")
    assert "* smoke-interrupt evaluates Fetch → bitcoin" in stopped
    assert "Done." not in stopped
    assert "-!- smoke-interrupt has quit [stopped]" in stopped
    assert "attempts:\n  next: 9fd7eb88" in stopped

    resumed = await runs.text("/runs/routines/2026-10-28_smoke-interrupt_9fd7eb88-2.md")
    assert "Fetch" not in resumed
    assert "<@slow-work> Done." in resumed
    assert "* smoke-interrupt evaluates Report → reported" in resumed
    assert "status: completed" in resumed


@pytest.mark.parametrize(
    ("owner", "status"),
    [("gone-runtime", "interrupted"), (RUNTIME_INSTANCE_ID, "started")],
    ids=["gone-runtime", "current-runtime"],
)
async def test_a_run_still_executing_is_interrupted_only_when_its_runtime_is_gone(
    runs: _Runs, owner: str, status: str
) -> None:
    await runs.event(
        "37fbe485-3",
        "started",
        "2026-10-28T06:46:45+00:00",
        agent_id="smoke",
        thread_id="routine:smoke:rev1:ad9d",
        runtime_instance=owner,
    )
    runs.checkpoints.run_meta["routine:smoke:rev1:ad9d"] = {
        "fetch": [_step(1, "2026-10-28T06:46:45+00:00", {"fetch": "refactor"})]
    }
    text = await runs.text("/runs/routines/2026-10-28_smoke-interrupt_37fbe485-3.md")
    assert f"status: {status}" in text
    restarted = "has quit [runtime restarted after Fetch; interrupted]" in text
    assert restarted is (status == "interrupted")


async def test_a_failed_routine_names_the_step_and_keeps_the_whole_error(runs: _Runs) -> None:
    error = "Node 'work' exceeded its run timeout of 60.000s (elapsed: 60.001s)."
    await runs.event(
        "aa11bb22-4",
        "started",
        "2026-10-28T03:00:00+00:00",
        agent_id="smoke",
        thread_id="routine:smoke:rev1:f1",
    )
    await runs.event(
        "aa11bb22-4", "failed", "2026-10-28T03:01:00+00:00", agent_id="smoke", error=error
    )
    runs.checkpoints.run_meta["routine:smoke:rev1:f1"] = {
        "fetch": [_step(1, "2026-10-28T03:00:00+00:00", {"fetch": "x"})]
    }
    text = await runs.text("/runs/routines/2026-10-28_smoke-interrupt_aa11bb22-4.md")
    assert f"has quit [failed at Fetch: {error}]" in text
    front = yaml.safe_load(text.split("---\n")[1])
    assert front["status"] == "failed"
    assert front["error"] == error


async def _workflow_with_routine_step(runs: _Runs) -> None:
    child_thread = "routine:smoke:rev1:workflow_run_dd6aba924948_9ee7e198"
    await runs.put(
        "workflow:workflow_d5b1",
        {
            "id": "workflow_d5b1",
            "title": "Self-build · Build result",
            "graph": {
                "nodes": {
                    "read": {"kind": "document_read", "title": "Read publication card"},
                    "ensure": {"kind": "routine", "title": "Ensure build PR", "routine": "smoke"},
                    "ok": {"kind": "condition", "title": "Publication succeeded"},
                }
            },
        },
    )
    await runs.put(
        "workflow_run:workflow_run_dd6aba924948",
        {
            "run_id": "workflow_run_dd6aba924948",
            "workflow_id": "workflow_d5b1",
            "status": "completed",
            "source": "document",
            "queued_at": "2026-10-27T22:48:12+00:00",
            "started_at": "2026-10-27T22:48:13+00:00",
            "completed_at": "2026-10-27T22:48:27+00:00",
            "trigger_event": {
                "key": "acp_task:cb1e884205d046e5",
                "op": "updated",
                "before": {"status": "running"},
                "after": {"status": "completed"},
                "changed_fields": ["status"],
            },
            "step_audit": [
                {
                    "node_id": "read",
                    "kind": "document_read",
                    "completed_at": "2026-10-27T22:48:13+00:00",
                },
                {
                    "node_id": "ensure",
                    "kind": "routine",
                    "completed_at": "2026-10-27T22:48:27+00:00",
                },
                {"node_id": "ok", "kind": "condition", "completed_at": "2026-10-27T22:48:27+00:00"},
            ],
            "node_outputs": {
                "read": {
                    "found": True,
                    "key": "kanban_card:card_159f",
                    "document": {"title": "PR comments"},
                },
                "ensure": {"ok": False, "thread_id": child_thread},
                "ok": {"false": True},
            },
        },
    )
    child = {
        "agent_id": "smoke",
        "thread_id": child_thread,
        "session_id": "workflow_run_dd6aba924948_9ee7e198",
    }
    await runs.event("workflow-x", "started", "2026-10-27T22:48:14+00:00", **child)
    await runs.event(
        "workflow-x", "completed", "2026-10-27T22:48:26+00:00", **child, output={"message": "Done."}
    )
    runs.checkpoints.run_meta[child_thread] = {
        "work": [_step(1, "2026-10-27T22:48:15+00:00", {"work": "Done."})],
    }


async def test_a_workflow_reads_as_its_trigger_steps_and_inlined_routine(runs: _Runs) -> None:
    await _workflow_with_routine_step(runs)
    # The routine the workflow started lives inside the workflow, not on its own.
    assert await runs.names("routines") == []
    path = "/runs/workflows/2026-10-27_self-build-build-result_workflow_run_dd6aba924948.md"
    assert await runs.names("workflows") == [path]

    text = await runs.text(path)
    assert "Self-build · Build result · acp_task cb1e8842 status running → completed" in text
    lines = [line[9:] for line in text.splitlines() if line[:2].isdigit()]
    channel = "#self-build-build-result-dd6aba92"
    assert lines == [
        '* self-build-build-result reads Read publication card → /tasks/card_159f.md "PR comments"',
        f"-!- smoke-interrupt [routine] has joined {channel}",
        "<@slow-work> Done.",
        "<smoke-interrupt> (the reply above)",
        f"-!- smoke-interrupt [routine] has left {channel}",
        "* self-build-build-result branches at Publication succeeded → false",
    ]


async def test_a_step_reference_reads_that_steps_whole_value(runs: _Runs) -> None:
    await _workflow_with_routine_step(runs)
    path = "/runs/workflows/2026-10-27_self-build-build-result_workflow_run_dd6aba924948.md"
    await runs.text(path)
    first = await runs.text(f"{path}#1")
    assert "key: kanban_card:card_159f" in first
    missing = await runs.fs.aread(f"{path}#99", 0, 100)
    assert missing.error is not None and "it has" in missing.error


async def test_runs_cannot_be_changed(runs: _Runs) -> None:
    await _continued_run(runs)
    path = "/runs/routines/2026-10-28_smoke-interrupt_9fd7eb88-2.md"
    assert (await runs.fs.awrite(path, "x")).error
    assert (await runs.fs.aedit(path, "Done.", "Nope.")).error


async def test_a_scheduled_run_is_its_message_and_is_interrupted_when_its_runtime_is_gone(
    runs: _Runs,
) -> None:
    await runs.put(
        "schedule",
        {
            "smoke-sched": {
                "id": "smoke-sched",
                "message": "Scheduled check",
                "name": "Nightly check",
                "created_at": "2026-10-28T03:01:48Z",
                "updated_at": "2026-10-28T03:01:48Z",
            }
        },
    )
    await runs.put(
        "schedule_runs",
        [
            {
                "run_id": "schedrun_f9b99b926a01",
                "schedule_id": "smoke-sched",
                "thread_id": "smoke_sched_c9719c43",
                "message": "Scheduled check",
                "status": "running",
                "created_at": "2026-10-28T03:02:08Z",
                "started_at": "2026-10-28T03:02:09Z",
                "updated_at": "2026-10-28T03:02:09Z",
                "runtime_instance": "gone-runtime",
            }
        ],
    )
    path = "/runs/schedules/2026-10-28_nightly-check_schedrun_f9b99b926a01.md"
    assert await runs.names("schedules") == [path]
    text = await runs.text(path)
    assert "<scheduler> Scheduled check" in text
    assert "status: interrupted" in text


def test_agent_requests_and_json_replies_are_summarised_but_speech_is_not() -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    from src.progena.runs._render import Steps, _compact_messages

    steps = Steps(path="/runs/workflows/x.md")
    request = "TEMPORARY-STORAGE CONTRACT: create no scratch files. " * 40
    messages = [
        HumanMessage(content=request),
        AIMessage(content='{"success": true, "branch": "self-build/card_1"}'),
        AIMessage(content="All 4 checks passed."),
    ]
    shown = [m.content for m in _compact_messages(messages, steps)]
    assert (
        shown[0].startswith('"TEMPORARY-STORAGE CONTRACT')
        and "⟨2 KB · /runs/workflows/x.md#1⟩" in shown[0]
    )
    assert shown[1] == "success: true, branch: self-build/card_1"
    assert shown[2] == "All 4 checks passed."
    assert steps.text("1") == request


async def test_same_prefix_runs_remain_distinct_and_equal_time_order_is_stable(runs: _Runs) -> None:
    for request_id in ("abcdef00-a", "abcdef00-z"):
        await runs.event(
            request_id,
            "started",
            "2026-10-28T04:00:00Z",
            agent_id="smoke",
            thread_id=f"routine:smoke:rev1:{request_id}",
        )
    names = await runs.names("routines")
    assert names == [
        "/runs/routines/2026-10-28_smoke-interrupt_abcdef00-z.md",
        "/runs/routines/2026-10-28_smoke-interrupt_abcdef00-a.md",
    ]
    fronts = [yaml.safe_load((await runs.text(path)).split("---\n")[1]) for path in names]
    assert [front["request_id"] for front in fronts] == ["abcdef00-z", "abcdef00-a"]
    assert (await runs.fs.aread("/runs/routines/2026-10-28_smoke-interrupt_abcdef00.md")).error


async def test_interrupted_file_updates_its_attempt_links_after_continue(runs: _Runs) -> None:
    thread_id = "routine:smoke:rev1:continued"
    await runs.event(
        "original",
        "started",
        "2026-10-28T04:00:00Z",
        agent_id="smoke",
        thread_id=thread_id,
        runtime_instance="gone-runtime",
    )
    path = "/runs/routines/2026-10-28_smoke-interrupt_original.md"
    first = yaml.safe_load((await runs.text(path)).split("---\n")[1])
    assert first["status"] == "interrupted"
    assert first["ended_at"] is None
    assert first["attempts"] is None
    await runs.event(
        "continued",
        "started",
        "2026-10-28T04:01:00Z",
        agent_id="smoke",
        thread_id=thread_id,
        runtime_instance=RUNTIME_INSTANCE_ID,
    )
    second = yaml.safe_load((await runs.text(path)).split("---\n")[1])
    assert second["status"] == "interrupted"
    assert second["ended_at"] is None
    assert second["attempts"]["next"] == "continue"


@pytest.mark.parametrize("kind", ["routine", "workflow", "schedule"])
async def test_full_id_paths_survive_title_and_timezone_changes(runs: _Runs, kind: str) -> None:
    if kind == "routine":
        await _continued_run(runs)
        path = "/runs/routines/2026-10-28_smoke-interrupt_9fd7eb88-2.md"
        expected_id = "9fd7eb88-2"
        id_field, title_field = "request_id", "routine"
    elif kind == "workflow":
        await _workflow_with_routine_step(runs)
        path = "/runs/workflows/2026-10-27_self-build-build-result_workflow_run_dd6aba924948.md"
        expected_id = "workflow_run_dd6aba924948"
        id_field, title_field = "run_id", "workflow"
    else:
        await runs.put(
            "schedule",
            {
                "identity": {
                    "id": "identity",
                    "name": "Original",
                    "message": "Run identity",
                    "created_at": "2026-10-28T23:00:00Z",
                    "updated_at": "2026-10-28T23:00:00Z",
                }
            },
        )
        await runs.put(
            "schedule_runs",
            [
                {
                    "run_id": "schedrun_identity",
                    "schedule_id": "identity",
                    "thread_id": "schedule:identity",
                    "message": "Run identity",
                    "status": "completed",
                    "created_at": "2026-10-28T23:00:00Z",
                    "updated_at": "2026-10-28T23:01:00Z",
                    "started_at": "2026-10-28T23:00:00Z",
                    "completed_at": "2026-10-28T23:01:00Z",
                }
            ],
        )
        path = "/runs/schedules/2026-10-28_original_schedrun_identity.md"
        expected_id = "schedrun_identity"
        id_field, title_field = "run_id", "schedule"

    before = yaml.safe_load((await runs.text(path)).split("---\n")[1])
    assert before[id_field] == expected_id
    if kind == "routine":
        await runs.state_manager.save_routine_graph("smoke", {**_GRAPH, "name": "Renamed"})
    elif kind == "workflow":
        await runs.state_manager.save_workflow_definition(
            "workflow_d5b1", {"id": "workflow_d5b1", "title": "Renamed", "graph": {"nodes": {}}}
        )
    else:
        await runs.state_manager.update_schedule_entry("identity", {"name": "Renamed"})
    await runs.state_manager.save_preferences_config({"timezone": "Pacific/Kiritimati"})

    after = yaml.safe_load((await runs.text(path)).split("---\n")[1])
    assert after[id_field] == expected_id
    assert after[title_field] == "Renamed"
    directory = {"routine": "routines", "workflow": "workflows", "schedule": "schedules"}[kind]
    assert path not in await runs.names(directory)
    if kind == "workflow":
        assert "kanban_card:card_159f" in await runs.text(f"{path}#1")


async def test_queued_workflow_path_remains_readable_after_starting_on_next_day(
    runs: _Runs,
) -> None:
    await runs.put("workflow:midnight", {"id": "midnight", "title": "Midnight", "graph": {}})
    await runs.put(
        "workflow_run:workflow_run_midnight",
        {
            "run_id": "workflow_run_midnight",
            "workflow_id": "midnight",
            "status": "running",
            "queued_at": "2026-10-28T23:59:59Z",
            "started_at": "2026-10-29T00:00:01Z",
        },
    )
    path = "/runs/workflows/2026-10-28_midnight_workflow_run_midnight.md"
    front = yaml.safe_load((await runs.text(path)).split("---\n")[1])
    assert front["run_id"] == "workflow_run_midnight"
    assert front["started_at"] == "2026-10-29T00:00:01Z"
