"""Consumer-visible ACP task supervisor behavior."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.backend.acp_supervisor import ACPTaskSupervisor
from app.backend.document_events import DocumentEventDispatcher, seed_document_types
from app.backend.document_store import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore
from app.progenitor.acp.runner import ACPRunResult
from app.progenitor.document_kinds import ACP_TASK_KEY_PREFIX


async def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0.01)


@dataclass
class _Runs:
    block_prompts: set[str] = field(default_factory=set)
    # Keep a cancelled prompt blocked until the test releases it.
    hold_cancelled: bool = False
    started: list[str] = field(default_factory=list)
    session_ids: list[str | None] = field(default_factory=list)
    runners: list[_FakeRunner] = field(default_factory=list)


class _FakeRunner:
    def __init__(self, runs: _Runs) -> None:
        self.runs = runs
        self.cancelled = False
        self.release = asyncio.Event()
        self.runs.runners.append(self)

    async def cancel(self, session_id: str | None = None) -> bool:
        _ = session_id
        self.cancelled = True
        if not self.runs.hold_cancelled:
            self.release.set()
        return True

    async def run(
        self,
        prompt: str,
        cwd: str,
        *,
        run_id: str,
        session_id: str | None,
        on_progress: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> ACPRunResult:
        _ = cwd
        self.runs.started.append(prompt)
        self.runs.session_ids.append(session_id)
        active_session = session_id or f"session-{len(self.runs.started)}"
        await on_progress({"kind": "session", "session_id": active_session})
        await on_progress({"kind": "message", "text": f"running {prompt}"})
        await on_progress(
            {
                "kind": "tool",
                "tool_call": {"id": run_id, "name": "edit", "status": "start"},
            }
        )
        if prompt in self.runs.block_prompts:
            await self.release.wait()
        if self.cancelled:
            return ACPRunResult(
                success=False,
                output=f"cancelled {prompt}",
                stop_reason="cancelled",
                session_id=active_session,
                cancelled=True,
            )
        return ACPRunResult(
            success=True,
            output=f"completed {prompt}",
            stop_reason="end_turn",
            session_id=active_session,
            tool_calls=[{"name": "edit", "status": "start"}],
        )


class _Harness:
    def __init__(self, monkeypatch, *, max_sessions: int = 2, max_turns: int = 20) -> None:
        monkeypatch.setenv("PROGENITOR_ACP_MAX_SESSIONS", str(max_sessions))
        monkeypatch.setenv("PROGENITOR_ACP_MAX_TURNS", str(max_turns))
        monkeypatch.setenv("PROGENITOR_ACP_PROGRESS_INTERVAL_SECONDS", "3600")
        self.store = InMemoryDocumentStore()
        seed_document_types(self.store)
        self.runs = _Runs()
        self.supervisor = ACPTaskSupervisor(
            store_getter=lambda: self.store,
            runner_factory=lambda _config, _cwd: _FakeRunner(self.runs),
        )
        self.dispatcher = DocumentEventDispatcher(store_getter=lambda: self.store)
        self.dispatcher.add_handler(self.supervisor.handle_document_event)

    async def start(self) -> None:
        await self.supervisor.start()
        await self.dispatcher.start()

    async def stop(self) -> None:
        await self.dispatcher.stop()
        await self.supervisor.stop()

    def add_task(self, task_id: str, prompt: str, **extra: Any) -> None:
        self.store.save_document(
            PROGENITOR_STATE_PREFIX,
            f"{ACP_TASK_KEY_PREFIX}{task_id}",
            {
                "task_id": task_id,
                "agent_name": "claude",
                "prompt": prompt,
                "status": "queued",
                "cancel_requested": False,
                **extra,
            },
        )

    def task(self, task_id: str) -> dict[str, Any]:
        document = self.store.get_document(
            PROGENITOR_STATE_PREFIX, f"{ACP_TASK_KEY_PREFIX}{task_id}", None
        )
        assert document is not None
        return dict(document.payload)

    def mutate(self, task_id: str, mutator: Callable[[dict[str, Any]], None]) -> None:
        def update(current: Any) -> Any:
            payload = dict(current)
            mutator(payload)
            return payload

        self.store.mutate_document(
            PROGENITOR_STATE_PREFIX, f"{ACP_TASK_KEY_PREFIX}{task_id}", update
        )


def _registry(store: InMemoryDocumentStore) -> None:
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "registry",
        {
            "agents": [
                {
                    "name": "claude",
                    "transport": "acp",
                    "acp": {"host": "fake-acp", "port": 3001},
                }
            ]
        },
    )


async def test_start_completes_and_persists_streamed_progress(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    await harness.start()
    try:
        harness.add_task("one", "build")
        await _wait_for(lambda: harness.task("one").get("status") == "completed")
        task = harness.task("one")
        assert task["session_id"] == "session-1"
        assert task["output"] == "completed build"
        assert task["progress"]["last_message"] == "running build"
        assert task["progress"]["tool_call_count"] == 1
        assert task["stalled"] is False
    finally:
        await harness.stop()


async def test_queued_continuation_resumes_the_stored_session(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    await harness.start()
    try:
        harness.add_task("one", "first")
        await _wait_for(lambda: harness.task("one").get("status") == "completed")
        harness.mutate("one", lambda task: task.update({"prompt": "second", "status": "queued"}))
        await _wait_for(
            lambda: (
                harness.task("one").get("turn") == 2
                and harness.task("one").get("status") == "completed"
            )
        )
        assert harness.runs.session_ids == [None, "session-1"]
        assert harness.task("one")["output"] == "completed second"
    finally:
        await harness.stop()


async def test_nudge_cancels_running_turn_and_preserves_new_request(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    harness.runs.block_prompts.add("first")
    await harness.start()
    try:
        harness.add_task("one", "first")
        await _wait_for(lambda: harness.runs.started == ["first"])
        harness.mutate("one", lambda task: task.update({"prompt": "second", "status": "queued"}))
        await _wait_for(lambda: harness.task("one").get("status") == "completed")
        task = harness.task("one")
        assert harness.runs.started == ["first", "second"]
        assert task["prompt"] == "second"
        assert task["history"][0]["status"] == "canceled"
    finally:
        await harness.stop()


async def test_nudge_runs_next_turn_without_a_further_document_event(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    harness.runs.block_prompts.add("first")
    harness.runs.hold_cancelled = True
    await harness.start()
    try:
        harness.add_task("one", "first")
        await _wait_for(lambda: harness.runs.started == ["first"])
        harness.mutate("one", lambda task: task.update({"prompt": "second", "status": "queued"}))
        await _wait_for(lambda: harness.runs.runners[0].cancelled)
        # Nothing else is delivered: the cancelled turn itself must start the nudge.
        await harness.dispatcher.stop()
        harness.runs.runners[0].release.set()
        await _wait_for(lambda: harness.task("one").get("status") == "completed")
        assert harness.runs.started == ["first", "second"]
    finally:
        await harness.supervisor.stop()


async def test_cancel_request_cancels_turn_and_resets_request_flag(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    harness.runs.block_prompts.add("build")
    await harness.start()
    try:
        harness.add_task("one", "build")
        await _wait_for(lambda: harness.runs.started == ["build"])
        harness.mutate("one", lambda task: task.update({"cancel_requested": True}))
        await _wait_for(lambda: harness.task("one").get("status") == "canceled")
        task = harness.task("one")
        assert task["cancel_requested"] is False
        assert task["stop_reason"] == "cancelled"
    finally:
        await harness.stop()


async def test_shutdown_marks_working_turn_interrupted_not_canceled(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    harness.runs.block_prompts.add("build")
    await harness.start()
    harness.add_task("one", "build")
    await _wait_for(lambda: harness.runs.started == ["build"])

    await harness.stop()

    assert harness.task("one")["status"] == "interrupted"


async def test_turn_limit_rejects_queued_request_before_runner_starts(monkeypatch) -> None:
    harness = _Harness(monkeypatch, max_turns=2)
    _registry(harness.store)
    await harness.start()
    try:
        harness.add_task("one", "again", turn=2, session_id="old")
        await _wait_for(lambda: harness.task("one").get("status") == "failed")
        assert "PROGENITOR_ACP_MAX_TURNS (2)" in harness.task("one")["last_error"]
        assert harness.runs.started == []
    finally:
        await harness.stop()


async def test_concurrency_cap_leaves_extra_task_queued_until_slot_frees(monkeypatch) -> None:
    harness = _Harness(monkeypatch, max_sessions=2)
    _registry(harness.store)
    harness.runs.block_prompts.update({"one", "two"})
    await harness.start()
    try:
        harness.add_task("one", "one")
        harness.add_task("two", "two")
        harness.add_task("three", "three")
        await _wait_for(lambda: len(harness.runs.started) == 2)
        assert harness.task("three")["status"] == "queued"
        for runner in list(harness.runs.runners):
            runner.release.set()
        await _wait_for(lambda: harness.task("three").get("status") == "completed")
    finally:
        await harness.stop()


async def test_startup_recovers_working_tasks_and_migrates_legacy_records(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    harness.store.save_document(
        PROGENITOR_STATE_PREFIX,
        f"{ACP_TASK_KEY_PREFIX}working",
        {"task_id": "working", "agent_name": "claude", "prompt": "old", "status": "working"},
    )
    harness.store.save_document(
        PROGENITOR_STATE_PREFIX,
        "acp_tasks",
        [
            {
                "task_id": "legacy",
                "agent_name": "claude",
                "request_text": "migrated",
                "status": "queued",
            }
        ],
    )
    await harness.start()
    try:
        await _wait_for(lambda: harness.task("legacy").get("status") == "completed")
        assert harness.task("working")["status"] == "interrupted"
        assert harness.task("legacy")["prompt"] == "migrated"
        assert harness.store.get_document(PROGENITOR_STATE_PREFIX, "acp_tasks", None) is None
    finally:
        await harness.stop()
