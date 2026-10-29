"""Consumer-visible ACP task supervisor behavior."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from src.backend.events.documents import DocumentEventDispatcher, seed_document_types
from src.backend.storage.documents import PROGENA_STATE_PREFIX, InMemoryDocumentStore
from src.backend.workflows.acp_results import NativeClaudeTranscriptReader
from src.backend.workflows.acp_supervisor import ACPTaskSupervisor
from src.progena.acp.runner import ACPRunResult
from src.progena.documents.kinds import ACP_TASK_KEY_PREFIX


async def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0.01)


@dataclass
class _Runs:
    block_prompts: set[str] = field(default_factory=set)
    blocked: asyncio.Event = field(default_factory=asyncio.Event)
    # Keep a cancelled prompt blocked until the test releases it.
    hold_cancelled: bool = False
    started: list[str] = field(default_factory=list)
    session_ids: list[str | None] = field(default_factory=list)
    steered: list[tuple[str, str]] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    runners: list[_FakeRunner] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)


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

    async def steer(self, session_id: str, text: str) -> str:
        self.runs.steered.append((session_id, text))
        return "injected"

    async def set_mode(self, mode: str) -> bool:
        self.runs.modes.append(mode)
        return True

    async def run(
        self,
        prompt: str,
        cwd: str,
        *,
        run_id: str,
        session_id: str | None,
        mode: str | None,
        on_progress: Callable[[dict[str, Any]], Awaitable[None]],
        on_input: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> ACPRunResult:
        _ = cwd
        self.runs.started.append(prompt)
        self.runs.session_ids.append(session_id)
        active_session = session_id or f"session-{len(self.runs.started)}"
        await on_progress({"kind": "session", "session_id": active_session})
        if prompt in {
            "native-finalization-fails",
            "native-source-unavailable",
            "native-partial",
        }:
            await on_progress(
                {
                    "kind": "native_session",
                    "session_id": "11111111-1111-1111-1111-111111111111",
                }
            )
        await on_progress({"kind": "message", "text": f"running {prompt}"})
        await on_progress(
            {
                "kind": "tool",
                "tool_call": {"id": run_id, "name": "edit", "status": "start"},
            }
        )
        if mode:
            self.runs.modes.append(mode)
        if prompt in {"question", "plan-approve", "plan-reject"}:
            kind = "question" if prompt == "question" else "plan"
            answer = await on_input(
                {
                    "id": f"input-{prompt}",
                    "kind": kind,
                    "message": f"{kind} requested",
                    **({"fields": {"value": {"type": "string"}}} if kind == "question" else {}),
                    **({"plan": "proposed steps"} if kind == "plan" else {}),
                }
            )
            return ACPRunResult(
                success=True,
                output=str(answer),
                stop_reason="end_turn",
                session_id=active_session,
            )
        if prompt in self.runs.failures:
            return ACPRunResult(
                success=False,
                output="",
                stop_reason="error",
                session_id=active_session,
                error=self.runs.failures[prompt],
            )
        if prompt in self.runs.block_prompts:
            self.runs.blocked.set()
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
            output_truncated=prompt == "native-partial",
            stop_reason="end_turn",
            session_id=active_session,
            tool_calls=[{"name": "edit", "status": "start"}],
        )


class _Harness:
    def __init__(
        self,
        monkeypatch,
        *,
        max_sessions: int = 2,
        max_turns: int = 20,
        transcript_reader: Any | None = None,
    ) -> None:
        monkeypatch.setenv("PROGENA_ACP_MAX_SESSIONS", str(max_sessions))
        monkeypatch.setenv("PROGENA_ACP_MAX_TURNS", str(max_turns))
        monkeypatch.setenv("PROGENA_ACP_PROGRESS_INTERVAL_SECONDS", "3600")
        self.store = InMemoryDocumentStore()
        seed_document_types(self.store)
        self.runs = _Runs()
        self.supervisor = ACPTaskSupervisor(
            store_getter=lambda: self.store,
            runner_factory=lambda _config, _cwd: _FakeRunner(self.runs),
            **({"transcript_reader": transcript_reader} if transcript_reader is not None else {}),
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
            PROGENA_STATE_PREFIX,
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
            PROGENA_STATE_PREFIX, f"{ACP_TASK_KEY_PREFIX}{task_id}", None
        )
        assert document is not None
        return dict(document.payload)

    def mutate(self, task_id: str, mutator: Callable[[dict[str, Any]], None]) -> None:
        def update(current: Any) -> Any:
            payload = dict(current)
            mutator(payload)
            return payload

        self.store.mutate_document(PROGENA_STATE_PREFIX, f"{ACP_TASK_KEY_PREFIX}{task_id}", update)


def _registry(store: InMemoryDocumentStore) -> None:
    store.save_document(
        PROGENA_STATE_PREFIX,
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


async def test_public_acp_session_never_selects_an_unmapped_native_transcript(monkeypatch) -> None:
    class _UnexpectedNativeReader:
        def start_turn(self, _session_id: str, _cwd: str) -> None:
            raise AssertionError("public ACP session ID was treated as a native SDK session")

    harness = _Harness(monkeypatch, transcript_reader=_UnexpectedNativeReader())
    _registry(harness.store)
    await harness.start()
    try:
        harness.add_task("one", "implementation")
        await _wait_for(lambda: harness.task("one").get("status") == "completed")
        assert harness.task("one")["output"] == "completed implementation"
    finally:
        await harness.stop()


async def test_native_finalization_error_terminalizes_the_turn(monkeypatch) -> None:
    class _BrokenNativeReader:
        def __init__(self) -> None:
            self.finish_calls = 0

        def start_new_turn(self, _session_id: str, _cwd: str) -> dict[str, str]:
            return {"provider": "claude_native"}

        def finish_turn(self, reference: dict[str, str], *, cwd: str) -> dict[str, str]:
            _ = cwd
            self.finish_calls += 1
            if self.finish_calls > 1:
                raise UnicodeDecodeError("utf-8", b"\xf0\x9f", 0, 2, "unexpected end")
            return reference

        def final_output(self, _reference: object, *, cwd: str) -> tuple[None, None]:
            _ = cwd
            return None, None

    harness = _Harness(monkeypatch, transcript_reader=_BrokenNativeReader())
    _registry(harness.store)
    await harness.start()
    try:
        harness.add_task("one", "native-finalization-fails")
        await _wait_for(lambda: harness.task("one").get("status") == "failed")
        task = harness.task("one")
        assert task["output"] == "completed native-finalization-fails"
        assert "Native ACP evidence finalization failed" in task["last_error"]
        assert task["history"][-1]["status"] == "failed"
    finally:
        await harness.stop()


async def test_native_unavailability_uses_exact_capture_or_fails_partial(monkeypatch) -> None:
    class _UnavailableNativeReader:
        def start_new_turn(self, _session_id: str, _cwd: str) -> dict[str, str]:
            return {"provider": "claude_native"}

        def finish_turn(self, reference: dict[str, str], *, cwd: str) -> dict[str, str]:
            _ = cwd
            return reference

        def final_output(self, _reference: object, *, cwd: str) -> tuple[None, None]:
            _ = cwd
            return None, None

    harness = _Harness(monkeypatch, transcript_reader=_UnavailableNativeReader())
    _registry(harness.store)
    await harness.start()
    try:
        harness.add_task("exact", "native-source-unavailable")
        harness.add_task("partial", "native-partial")
        await _wait_for(lambda: harness.task("exact").get("status") == "completed")
        await _wait_for(lambda: harness.task("partial").get("status") == "failed")
        assert harness.task("exact")["output"] == "completed native-source-unavailable"
        assert harness.task("partial")["output"] == "completed native-partial"
        assert "retained output is partial" in harness.task("partial")["last_error"]
    finally:
        await harness.stop()


async def test_delayed_native_file_is_recovered_after_identity_notification(
    monkeypatch, tmp_path
) -> None:
    session_id = "11111111-1111-1111-1111-111111111111"
    project = tmp_path / "-workspace"
    project.mkdir()
    prompt = "native-source-unavailable"
    harness = _Harness(monkeypatch, transcript_reader=NativeClaudeTranscriptReader(tmp_path))
    harness.runs.block_prompts.add(prompt)
    _registry(harness.store)
    await harness.start()
    try:
        harness.add_task("delayed", prompt, cwd="/workspace")
        await asyncio.wait_for(harness.runs.blocked.wait(), timeout=2)
        row = {
            "sessionId": session_id,
            "cwd": "/workspace",
            "type": "assistant",
            "message": {
                "id": "final-message",
                "role": "assistant",
                "content": [{"type": "text", "text": "delayed final evidence"}],
            },
        }
        (project / f"{session_id}.jsonl").write_text(json.dumps(row) + "\n")
        harness.runs.runners[0].release.set()
        await _wait_for(lambda: harness.task("delayed").get("status") == "completed")
        task = harness.task("delayed")
        assert task["output"] == "delayed final evidence"
        assert task["evidence"]["final_output_available"]
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


async def test_failed_turn_records_whether_the_agent_hit_a_usage_limit(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    harness.runs.failures = {
        "limited": "Internal error: You've hit your session limit · resets 12:50am (UTC)",
        "broken": "Internal error: tool crashed while editing rate_limiter.py",
    }
    await harness.start()
    try:
        harness.add_task("limited", "limited")
        harness.add_task("broken", "broken")
        await _wait_for(lambda: harness.task("limited").get("status") == "failed")
        await _wait_for(lambda: harness.task("broken").get("status") == "failed")
        assert harness.task("limited")["failure_kind"] == "usage_limit"
        assert harness.task("limited")["history"][-1]["failure_kind"] == "usage_limit"
        assert harness.task("broken")["failure_kind"] == "error"

        harness.mutate("limited", lambda task: task.update({"prompt": "retry", "status": "queued"}))
        await _wait_for(
            lambda: (
                harness.task("limited").get("turn") == 2
                and harness.task("limited").get("status") == "completed"
            )
        )
        assert harness.task("limited")["failure_kind"] is None
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
        assert "PROGENA_ACP_MAX_TURNS (2)" in harness.task("one")["last_error"]
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


async def test_message_steers_a_running_turn(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    harness.runs.block_prompts.add("build")
    await harness.start()
    try:
        harness.add_task("one", "build")
        await _wait_for(lambda: harness.runs.started == ["build"])
        harness.mutate(
            "one",
            lambda task: task.update(
                {"messages": [{"id": "message-1", "text": "change direction", "status": "pending"}]}
            ),
        )
        await _wait_for(lambda: harness.task("one")["messages"][0]["status"] == "injected")
        assert harness.runs.steered == [("session-1", "change direction")]
        harness.runs.runners[0].release.set()
        await _wait_for(lambda: harness.task("one").get("status") == "completed")
    finally:
        await harness.stop()


async def test_idle_message_starts_a_turn_on_the_existing_session(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    await harness.start()
    try:
        harness.add_task("one", "first")
        await _wait_for(lambda: harness.task("one").get("status") == "completed")
        harness.mutate(
            "one",
            lambda task: task.update(
                {"messages": [{"id": "message-1", "text": "second", "status": "pending"}]}
            ),
        )
        await _wait_for(lambda: harness.task("one").get("turn") == 2)
        await _wait_for(lambda: harness.task("one").get("status") == "completed")
        task = harness.task("one")
        assert task["messages"][0]["status"] == "started_turn"
        assert harness.runs.session_ids == [None, "session-1"]
        assert harness.runs.started == ["first", "second"]
    finally:
        await harness.stop()


async def test_question_answer_round_trip_clears_pending_input(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    await harness.start()
    try:
        harness.add_task("one", "question")
        await _wait_for(lambda: harness.task("one").get("pending_input") is not None)
        pending = harness.task("one")["pending_input"]
        assert pending["kind"] == "question"
        assert pending["fields"] == {"value": {"type": "string"}}
        harness.mutate(
            "one", lambda task: task.update({"answers": {pending["id"]: {"value": "yes"}}})
        )
        await _wait_for(lambda: harness.task("one").get("status") == "completed")
        task = harness.task("one")
        assert task["pending_input"] is None
        assert task["answers"] == {}
        assert task["output"] == "{'value': 'yes'}"
    finally:
        await harness.stop()


async def test_plan_answers_drive_approval_and_rejection(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    await harness.start()
    try:
        harness.add_task("approve", "plan-approve")
        await _wait_for(lambda: harness.task("approve").get("pending_input") is not None)
        approve_id = harness.task("approve")["pending_input"]["id"]
        harness.mutate(
            "approve",
            lambda task: task.update({"answers": {approve_id: {"approve": True}}}),
        )
        await _wait_for(lambda: harness.task("approve").get("status") == "completed")
        assert harness.task("approve")["output"] == "{'approve': True}"

        harness.add_task("reject", "plan-reject")
        await _wait_for(lambda: harness.task("reject").get("pending_input") is not None)
        reject_id = harness.task("reject")["pending_input"]["id"]
        harness.mutate(
            "reject",
            lambda task: task.update(
                {"answers": {reject_id: {"approve": False, "feedback": "use smaller steps"}}}
            ),
        )
        await _wait_for(lambda: harness.task("reject").get("status") == "completed")
        assert (
            harness.task("reject")["output"]
            == "{'approve': False, 'feedback': 'use smaller steps'}"
        )
    finally:
        await harness.stop()


async def test_mode_change_applies_to_the_active_session(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    harness.runs.block_prompts.add("build")
    await harness.start()
    try:
        harness.add_task("one", "build", mode="plan")
        await _wait_for(lambda: harness.runs.started == ["build"])
        await _wait_for(lambda: harness.runs.modes == ["plan"])
        harness.mutate("one", lambda task: task.update({"mode": "acceptEdits"}))
        await _wait_for(lambda: harness.runs.modes == ["plan", "acceptEdits"])
        harness.runs.runners[0].release.set()
        await _wait_for(lambda: harness.task("one").get("status") == "completed")
    finally:
        await harness.stop()


async def test_late_answer_is_discarded_after_turn_end(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    await harness.start()
    try:
        harness.add_task(
            "one", "first", pending_input={"id": "old", "kind": "question", "message": "old"}
        )
        await _wait_for(lambda: harness.task("one").get("status") == "completed")
        harness.mutate("one", lambda task: task.update({"answers": {"old": {"value": "late"}}}))
        await _wait_for(lambda: harness.task("one").get("answers") == {})
        assert harness.task("one")["pending_input"] is None
    finally:
        await harness.stop()


async def test_startup_recovers_working_tasks_and_migrates_legacy_records(monkeypatch) -> None:
    harness = _Harness(monkeypatch)
    _registry(harness.store)
    harness.store.save_document(
        PROGENA_STATE_PREFIX,
        f"{ACP_TASK_KEY_PREFIX}working",
        {"task_id": "working", "agent_name": "claude", "prompt": "old", "status": "working"},
    )
    harness.store.save_document(
        PROGENA_STATE_PREFIX,
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
        assert harness.store.get_document(PROGENA_STATE_PREFIX, "acp_tasks", None) is None
    finally:
        await harness.stop()
