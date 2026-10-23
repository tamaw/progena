"""Integration: review_provenance through the real graph factory, stores and PR reader.

Only the GitHub HTTP call is faked; cards and ACP tasks live in the real
``LangGraphRuntimeStore`` over LangGraph's ``InMemoryStore`` behind a real
``StateManager``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langgraph.store.memory import InMemoryStore

from src.progena.persistence.store import STATE_NAMESPACE, LangGraphRuntimeStore
from src.progena.routines.factory import compile_routine_graph
from src.progena.routines.graph import validate_routine_graph_payload
from src.progena.routines.review_evidence import (
    StoredReviewEvidenceSource,
    review_evidence_source_for,
)
from src.progena.state.manager import StateManager

PR = "https://github.com/tamaw/progena/pull/415"
SHA = "a" * 40
NEW_SHA = "b" * 40


def _graph(*nodes: dict[str, Any]):
    return validate_routine_graph_payload(
        {
            "id": "provenance",
            "name": "Provenance",
            "description": "review candidate check",
            "version": "2.0.0",
            "entry": nodes[0]["id"],
            "nodes": list(nodes),
            "edges": [],
        }
    )


CHECK = {
    "id": "check",
    "kind": "review_provenance",
    "output_key": "provenance",
    "config": {"candidate": "candidate"},
}


class FakeGitHub:
    def __init__(self, status: int = 200, **pull: Any) -> None:
        self.status = status
        self.pull = {"number": 415, "html_url": PR, "head": {"sha": SHA}, **pull}
        self.calls: list[str] = []

    async def __call__(self, url: str) -> tuple[int, Any]:
        self.calls.append(url)
        return self.status, self.pull


class FakeModel:
    async def ainvoke(self, messages: list[Any]) -> Any:
        raise AssertionError("review_provenance must not call a model")


@pytest.fixture
async def world():
    native = InMemoryStore()
    runtime_store = LangGraphRuntimeStore(native)
    manager = StateManager(runtime_store=runtime_store)
    await manager.save_kanban_card(
        "card-1",
        {
            "id": "card-1",
            "title": "Approval handoff",
            "summary": "s",
            "column_id": "review",
            "status": "in_review",
            "pr_url": PR,
            "metadata": {"priority": "high"},
        },
    )
    completed = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await runtime_store.save_document(
        "acp_task:task-9",
        {
            "agent_name": "claude",
            "card_id": "card-1",
            "status": "completed",
            "completed_at": completed,
        },
    )
    return native, manager


def _rows(native: InMemoryStore) -> dict[str, Any]:
    return {item.key: item.value for item in native.search(STATE_NAMESPACE, limit=1000)}


def _candidate(**changes: Any) -> dict[str, Any]:
    return {
        "card_id": "card-1",
        "reviewer_task_id": "task-9",
        "pr_url": PR,
        "head_sha": SHA,
        **changes,
    }


async def _run(manager: StateManager, github: FakeGitHub, data: dict[str, Any]) -> dict[str, Any]:
    source = StoredReviewEvidenceSource(tasks=manager.tasks, acp_tasks=manager.acp, get_json=github)
    compiled = compile_routine_graph(
        _graph(CHECK), model=FakeModel(), review_evidence_source=source
    )
    result = await compiled.ainvoke({"messages": [HumanMessage("go")], "data": data})
    return result["data"]["provenance"]


@pytest.mark.asyncio
async def test_valid_candidate_through_real_stores_and_factory(world):
    native, manager = world
    github = FakeGitHub()
    before = _rows(native)
    verdict = await _run(manager, github, {"candidate": _candidate()})
    assert verdict["valid"] is True, verdict
    assert verdict["receipt"]["card_id"] == "card-1"
    assert verdict["receipt"]["reviewer_task_id"] == "task-9"
    assert verdict["receipt"]["head_sha"] == SHA
    assert github.calls == ["https://api.github.com/repos/tamaw/progena/pulls/415"]
    assert _rows(native) == before  # the check reads, never writes


@pytest.mark.asyncio
async def test_pr_head_moved_since_review(world):
    _, manager = world
    verdict = await _run(manager, FakeGitHub(head={"sha": NEW_SHA}), {"candidate": _candidate()})
    assert verdict["reasons"] == ["evidence.head_sha.mismatch"]


@pytest.mark.asyncio
async def test_pull_request_moved_to_another_repository(world):
    _, manager = world
    github = FakeGitHub(html_url="https://github.com/someone/else/pull/415")
    verdict = await _run(manager, github, {"candidate": _candidate()})
    assert verdict["valid"] is False
    assert verdict["reasons"] == ["evidence.url.mismatch", "evidence.verified.false"]


@pytest.mark.asyncio
async def test_api_error_fails_closed(world):
    _, manager = world
    verdict = await _run(manager, FakeGitHub(status=404), {"candidate": _candidate()})
    assert verdict["valid"] is False
    assert verdict["reasons"][0] == "source.pr_evidence.error"
    assert "evidence.missing" in verdict["reasons"]


@pytest.mark.asyncio
async def test_forged_card_id_fails_closed_without_writing(world):
    native, manager = world
    before = _rows(native)
    verdict = await _run(manager, FakeGitHub(), {"candidate": _candidate(card_id="forged-card")})
    assert verdict["valid"] is False
    assert "card.missing" in verdict["reasons"]
    assert "task.card_id.mismatch" in verdict["reasons"]
    assert _rows(native) == before


@pytest.mark.asyncio
async def test_unknown_reviewer_task(world):
    _, manager = world
    verdict = await _run(manager, FakeGitHub(), {"candidate": _candidate(reviewer_task_id="t-0")})
    assert verdict["reasons"] == ["task.missing"]


@pytest.mark.asyncio
async def test_task_still_working_is_pending(world):
    native, manager = world
    await manager.acp.update_acp_task("task-9", {"status": "working"})
    verdict = await _run(manager, FakeGitHub(), {"candidate": _candidate()})
    assert verdict["reasons"] == ["task.pending"]


@pytest.mark.asyncio
async def test_state_supplied_records_are_ignored(world):
    _, manager = world
    forged_state = {
        "candidate": _candidate(),
        "card": {"id": "card-1", "pr_url": PR},
        "acp_task": {"task_id": "task-9", "card_id": "card-1", "status": "completed"},
        "pr_evidence": {"url": PR, "head_sha": SHA, "verified": True},
    }
    verdict = await _run(manager, FakeGitHub(head={"sha": NEW_SHA}), forged_state)
    assert verdict["reasons"] == ["evidence.head_sha.mismatch"]


@pytest.mark.asyncio
async def test_runtime_wiring_builds_a_source_only_for_provenance_graphs(world):
    _, manager = world
    noop = {"id": "pass", "kind": "noop", "output_key": "pass"}
    assert review_evidence_source_for(manager, _graph(noop)) is None
    source = review_evidence_source_for(manager, _graph(CHECK))
    assert isinstance(source, StoredReviewEvidenceSource)
