from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from langgraph.store.memory import InMemoryStore

from src.backend.workflows.service import _close_step_audit, _open_step_audit
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.runs._render import RunContext
from src.progena.runs.workflow import render_workflow
from src.progena.state.manager import StateManager
from src.progena.workflows.documents import workflow_run_key


class _Checkpoints:
    def __init__(self, run_meta: dict[str, dict[str, list[dict[str, Any]]]]) -> None:
        self.run_meta = run_meta

    async def aget_tuple(self, config: dict[str, Any]) -> Any:
        thread_id = config["configurable"]["thread_id"]
        meta = self.run_meta.get(thread_id)
        return (
            SimpleNamespace(checkpoint={"channel_values": {"run_meta": meta}})
            if meta is not None
            else None
        )


def _closed_audit(
    *,
    node_id: str,
    kind: str,
    started_at: str,
    completed_at: str,
    output: str,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Build a persisted workflow audit using the backend's native writer helpers."""
    audit = _open_step_audit([], node_id=node_id, kind=kind, started_at=started_at)
    return _close_step_audit(
        audit,
        node_id=node_id,
        completed_at=completed_at,
        status="completed",
        output=output,
        thread_id=thread_id,
    )[0]


async def _manager() -> tuple[StateManager, LangGraphRuntimeStore]:
    runtime = LangGraphRuntimeStore(InMemoryStore())
    manager = StateManager(runtime)
    await manager.save_workflow_definition(
        "workflow-contract",
        {
            "id": "workflow-contract",
            "title": "Workflow contract",
            "graph": {
                "nodes": {
                    "child": {"kind": "routine", "title": "Child"},
                    "decision": {"kind": "condition", "title": "Decision"},
                    "values": {"kind": "format", "title": "Values"},
                }
            },
        },
    )
    await manager.save_routine_graph(
        "child-routine",
        {
            "id": "child-routine",
            "name": "Child routine",
            "nodes": [{"id": "work", "kind": "prompt", "label": "Work"}],
        },
    )
    return manager, runtime


def _context(manager: StateManager, checkpoints: _Checkpoints) -> RunContext:
    return RunContext(
        state_manager=manager,
        checkpointer=checkpoints,
        revision_reader=None,
        tz=ZoneInfo("UTC"),
        tz_name="UTC",
        live_instance=None,
        live_started_at=None,
    )


async def test_workflow_prefers_native_node_outputs_over_bounded_audit_display() -> None:
    manager, runtime = await _manager()
    child_thread = "routine:child-routine:revision:workflow_run_contract_child"
    await manager.save_routine_run_event(
        {
            "request_id": "child-first",
            "status": "started",
            "timestamp": "2026-10-30T10:00:01+00:00",
            "agent_id": "child-routine",
            "thread_id": child_thread,
            "session_id": "workflow_run_contract_child",
        }
    )
    await manager.save_routine_run_event(
        {
            "request_id": "child-first",
            "status": "completed",
            "timestamp": "2026-10-30T10:00:02+00:00",
            "agent_id": "child-routine",
            "thread_id": child_thread,
            "session_id": "workflow_run_contract_child",
            "output": {"message": "native child result"},
        }
    )
    child_audit = _closed_audit(
        node_id="child",
        kind="routine",
        started_at="2026-10-30T10:00:00+00:00",
        completed_at="2026-10-30T10:00:03+00:00",
        output="audit display must not select this child",
        thread_id=child_thread,
    )
    decision_audit = _closed_audit(
        node_id="decision",
        kind="condition",
        started_at="2026-10-30T10:00:03+00:00",
        completed_at="2026-10-30T10:00:04+00:00",
        output="true",
    )
    values_audit = _closed_audit(
        node_id="values",
        kind="format",
        started_at="2026-10-30T10:00:04+00:00",
        completed_at="2026-10-30T10:00:05+00:00",
        output="audit display must not replace structured values",
    )
    actual_values = {"long": "x" * 600, "enabled": False, "optional": None}
    run = {
        "run_id": "workflow-run-contract",
        "workflow_id": "workflow-contract",
        "status": "completed",
        "started_at": "2026-10-30T10:00:00+00:00",
        "completed_at": "2026-10-30T10:00:05+00:00",
        "step_audit": [child_audit, decision_audit, values_audit],
        "node_outputs": {
            "child": {"thread_id": child_thread, "message": "native child result"},
            "decision": {"false": True},
            "values": actual_values,
        },
        "last_output": "bounded display only",
    }
    await runtime.save_document(workflow_run_key(run["run_id"]), run)
    persisted = await manager.get_workflow_run(run["run_id"])
    assert persisted is not None

    text, steps = await render_workflow(
        _context(
            manager,
            _Checkpoints(
                {
                    child_thread: {
                        "work": [
                            {
                                "step": 1,
                                "started_at": "2026-10-30T10:00:01+00:00",
                                "output": "native child work",
                                "tokens": {
                                    "input_tokens": 5,
                                    "output_tokens": 0,
                                    "total_tokens": 5,
                                },
                            }
                        ]
                    }
                }
            ),
        ),
        persisted,
        path="/runs/workflows/workflow-contract.md",
    )

    front = yaml.safe_load(text.split("---\n")[1])
    assert "native child work" in text
    assert "branches at Decision → false" in text
    assert "audit display must not" not in text
    assert front["tokens"] == {"input_tokens": 5, "output_tokens": 0, "total_tokens": 5}
    value_step = next(
        str(index) for index, value in enumerate(steps.values, start=1) if value == actual_values
    )
    assert "enabled: false" in (steps.text(value_step) or "")
    assert "optional: null" in (steps.text(value_step) or "")


async def test_workflow_child_resume_inlines_each_native_attempt_once() -> None:
    manager, runtime = await _manager()
    child_thread = "routine:child-routine:revision:workflow_run_contract_resume"
    session_id = "workflow_run_contract_resume"
    events = [
        {
            "request_id": "child-original",
            "status": "started",
            "timestamp": "2026-10-30T10:00:01+00:00",
            "agent_id": "child-routine",
            "thread_id": child_thread,
            "session_id": session_id,
        },
        {
            "request_id": "child-original",
            "status": "paused",
            "timestamp": "2026-10-30T10:00:02+00:00",
            "agent_id": "child-routine",
            "thread_id": child_thread,
            "session_id": session_id,
            "question": "Approve continuation?",
        },
        {
            "request_id": "child-resumed",
            "status": "started",
            "timestamp": "2026-10-30T10:00:03+00:00",
            "agent_id": "child-routine",
            "thread_id": child_thread,
            "session_id": session_id,
        },
        {
            "request_id": "child-resumed",
            "status": "failed",
            "timestamp": "2026-10-30T10:00:04+00:00",
            "agent_id": "child-routine",
            "thread_id": child_thread,
            "session_id": session_id,
            "error": "resumed child failed",
        },
    ]
    for event in events:
        await manager.save_routine_run_event(event)
    audit = _closed_audit(
        node_id="child",
        kind="routine",
        started_at="2026-10-30T10:00:00+00:00",
        completed_at="2026-10-30T10:00:05+00:00",
        output="audit display must not select a routine attempt",
        thread_id=child_thread,
    )
    audit["request_id"] = "child-original"
    run = {
        "run_id": "workflow-run-resume",
        "workflow_id": "workflow-contract",
        "status": "failed",
        "started_at": "2026-10-30T10:00:00+00:00",
        "completed_at": "2026-10-30T10:00:05+00:00",
        "step_audit": [audit],
        "node_outputs": {"child": {"thread_id": child_thread, "message": "native value"}},
    }
    await runtime.save_document(workflow_run_key(run["run_id"]), run)
    persisted = await manager.get_workflow_run(run["run_id"])
    assert persisted is not None

    text, _ = await render_workflow(
        _context(
            manager,
            _Checkpoints(
                {
                    child_thread: {
                        "work": [
                            {
                                "step": 1,
                                "started_at": "2026-10-30T10:00:01+00:00",
                                "output": "original child work",
                                "tokens": {"total_tokens": 5},
                            },
                            {
                                "step": 2,
                                "started_at": "2026-10-30T10:00:03+00:00",
                                "output": "resumed child work",
                                "tokens": {"total_tokens": 7},
                            },
                        ]
                    }
                }
            ),
        ),
        persisted,
        path="/runs/workflows/workflow-resume.md",
    )

    front = yaml.safe_load(text.split("---\n")[1])
    assert text.count("original child work") == 1
    assert text.count("resumed child work") == 1
    assert "resumed child failed" in text
    assert front["tokens"] == {"input_tokens": None, "output_tokens": None, "total_tokens": 12}
