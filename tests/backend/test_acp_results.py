"""Task-bound ACP detail and native transcript reader tests."""

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path

import pytest

from src.backend.storage.documents import PROGENA_STATE_PREFIX, InMemoryDocumentStore
from src.backend.workflows.acp_results import NativeClaudeTranscriptReader, get_acp_result
from src.progena.documents.kinds import ACP_TASK_KEY_PREFIX


@pytest.fixture
def transcript(
    tmp_path: Path,
) -> tuple[NativeClaudeTranscriptReader, str, str, dict[str, object], str]:
    session = "11111111-1111-1111-1111-111111111111"
    cwd = "/app/repos/project"
    project = tmp_path / "-app-repos-project"
    project.mkdir()
    text = "exact final response " * 40
    rows = [
        {"type": "user", "sessionId": session, "cwd": cwd, "message": {"role": "user"}},
        {
            "type": "assistant",
            "sessionId": session,
            "cwd": cwd,
            "isSidechain": False,
            "message": {"role": "assistant", "content": [{"type": "thinking"}]},
        },
        {
            "type": "assistant",
            "sessionId": session,
            "cwd": cwd,
            "isSidechain": False,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        },
    ]
    native_path = project / f"{session}.jsonl"
    reader = NativeClaudeTranscriptReader(tmp_path)
    started = reader.start_turn(session, cwd)
    assert started is not None
    native_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    evidence = reader.finish_turn(started, cwd=cwd)
    assert evidence is not None
    return reader, session, cwd, evidence, text


def _store_task(store: InMemoryDocumentStore, task_id: str, payload: dict[str, object]) -> None:
    store.save_document(PROGENA_STATE_PREFIX, f"{ACP_TASK_KEY_PREFIX}{task_id}", payload)


def test_native_reader_uses_structured_final_assistant_message(
    transcript: tuple[NativeClaudeTranscriptReader, str, str, dict[str, object], str],
) -> None:
    reader, _session, cwd, evidence, expected = transcript

    output, line = reader.final_output(evidence, cwd=cwd)

    assert output == expected
    assert line == 2


def test_result_pages_exact_output_input_plan_and_error_metadata(
    transcript: tuple[NativeClaudeTranscriptReader, str, str, dict[str, object], str],
) -> None:
    reader, _session, cwd, evidence, expected = transcript
    store = InMemoryDocumentStore()
    _store_task(
        store,
        "acp_detail",
        {
            "task_id": "acp_detail",
            "agent_name": "claude",
            "cwd": cwd,
            "turn": 2,
            "status": "failed",
            "output": expected,
            "evidence": evidence,
            "stop_reason": "error",
            "last_error": "the exact failure detail",
            "failure_kind": "error",
            "progress": {"plan": [{"content": "current plan"}]},
            "pending_input": {"id": "pending", "kind": "plan", "plan": "approve exactly"},
            "history": [
                {
                    "turn": 1,
                    "status": "completed",
                    "output": "historic exact output",
                    "stop_reason": "end_turn",
                    "last_error": None,
                    "failure_kind": None,
                    "plan": [{"content": "historic plan"}],
                }
            ],
            "input_history": [
                {
                    "turn": 1,
                    "status": "answered",
                    "request": {"id": "historic", "kind": "question", "message": "exact question"},
                    "answer": {"value": "yes"},
                }
            ],
        },
    )

    first = get_acp_result(store, "acp_detail", source="output", limit=30, transcript_reader=reader)
    second = get_acp_result(
        store,
        "acp_detail",
        source="output",
        cursor=first["next_cursor"],
        limit=8 * 1024,
        transcript_reader=reader,
    )
    assert first["last_error"] == "the exact failure detail"
    assert first["stop_reason"] == "error"
    assert first["failure_kind"] == "error"
    assert first["availability"] == "native"
    assert first["content"] + second["content"] == expected
    error = get_acp_result(store, "acp_detail", source="error", transcript_reader=reader)
    assert error["content"] == "the exact failure detail"
    legacy = get_acp_result(store, "acp_detail", source="output", turn=1, transcript_reader=reader)
    assert legacy["availability"] == "stored"
    assert legacy["content"] == "historic exact output"

    pending = get_acp_result(store, "acp_detail", source="input", transcript_reader=reader)
    assert json.loads(pending["content"])["plan"] == "approve exactly"
    historic_input = get_acp_result(
        store, "acp_detail", source="input", turn=1, transcript_reader=reader
    )
    assert json.loads(historic_input["content"])["answer"] == {"value": "yes"}
    historic_plan = get_acp_result(
        store, "acp_detail", source="plan", turn=1, transcript_reader=reader
    )
    assert json.loads(historic_plan["content"]) == [{"content": "historic plan"}]


def test_transcript_pagination_reconstructs_selected_turn_only(
    transcript: tuple[NativeClaudeTranscriptReader, str, str, dict[str, object], str],
) -> None:
    reader, _session, cwd, evidence, _expected = transcript
    store = InMemoryDocumentStore()
    _store_task(
        store,
        "acp_transcript",
        {
            "task_id": "acp_transcript",
            "agent_name": "claude",
            "cwd": cwd,
            "turn": 1,
            "status": "completed",
            "output": "stored final",
            "evidence": evidence,
        },
    )
    # A later resumed turn may append to this same session file. Its sealed
    # line_end must prevent that future evidence bleeding into turn 1.
    native_path = reader._root / "-app-repos-project" / "11111111-1111-1111-1111-111111111111.jsonl"  # noqa: SLF001
    with native_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "11111111-1111-1111-1111-111111111111",
                    "cwd": cwd,
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "future resumed turn"}],
                    },
                }
            )
            + "\n"
        )

    cursor = None
    pages: list[str] = []
    while True:
        page = get_acp_result(
            store,
            "acp_transcript",
            source="transcript",
            cursor=cursor,
            limit=17,
            transcript_reader=reader,
        )
        pages.append(page["content"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    exact = "".join(pages)
    assert '"sessionId": "11111111-1111-1111-1111-111111111111"' in exact
    assert '"text": "exact final response' in exact
    assert "future resumed turn" not in exact


def test_result_reader_rejects_non_task_identifiers() -> None:
    with pytest.raises(LookupError):
        get_acp_result(InMemoryDocumentStore(), "../registry", source="output")


def test_queued_turn_zero_reports_unavailable_detail() -> None:
    store = InMemoryDocumentStore()
    _store_task(
        store,
        "acp_queued",
        {"task_id": "acp_queued", "agent_name": "claude", "turn": 0, "status": "queued"},
    )

    detail = get_acp_result(store, "acp_queued", turn=0, source="output")

    assert detail["turn"] == 0
    assert detail["availability"] == "unavailable"
    assert detail["content"] == ""


def test_native_reader_requires_matching_structured_cwd(tmp_path: Path) -> None:
    session = "33333333-3333-3333-3333-333333333333"
    project = tmp_path / "-app-repos-project"
    project.mkdir()
    (project / f"{session}.jsonl").write_text(
        json.dumps({"type": "user", "sessionId": session, "cwd": "/app/repos/other"}) + "\n",
        encoding="utf-8",
    )
    reader = NativeClaudeTranscriptReader(tmp_path)

    assert (
        reader.finish_turn(
            {"provider": "claude_native", "session_id": session, "line_start": 0},
            cwd="/app/repos/project",
        )
        is None
    )


def test_native_reader_rejects_cross_task_cwd_and_symlink(tmp_path: Path) -> None:
    session = "22222222-2222-2222-2222-222222222222"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / f"{session}.jsonl").write_text(
        json.dumps({"type": "user", "sessionId": session, "cwd": "/app/repos/other"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "-app-repos-project").symlink_to(outside, target_is_directory=True)
    reader = NativeClaudeTranscriptReader(tmp_path)

    assert (
        reader.finish_turn(
            {"provider": "claude_native", "session_id": session, "line_start": 0},
            cwd="/app/repos/project",
        )
        is None
    )


def test_missing_native_root_cannot_enable_bounded_collection(tmp_path: Path) -> None:
    reader = NativeClaudeTranscriptReader(tmp_path / "not-mounted")
    assert reader.start_turn("44444444-4444-4444-4444-444444444444", "/app/repos/project") is None


def test_native_metadata_cannot_combine_two_different_sessions(tmp_path: Path) -> None:
    session = "44444444-4444-4444-4444-444444444444"
    project = tmp_path / "-app-repos-project"
    project.mkdir()
    rows = [
        {"sessionId": session, "cwd": "/app/repos/other"},
        {"sessionId": "55555555-5555-5555-5555-555555555555", "cwd": "/app/repos/project"},
    ]
    (project / f"{session}.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    reader = NativeClaudeTranscriptReader(tmp_path)
    assert (
        reader.finish_turn(
            {"provider": "claude_native", "session_id": session, "line_start": 0},
            cwd="/app/repos/project",
        )
        is None
    )


def test_native_final_output_combines_final_message_blocks_without_sidechain_reset(
    tmp_path: Path,
) -> None:
    session = "66666666-6666-6666-6666-666666666666"
    cwd = "/app/repos/project"
    project = tmp_path / "-app-repos-project"
    project.mkdir()
    reader = NativeClaudeTranscriptReader(tmp_path)
    started = reader.start_turn(session, cwd)
    assert started is not None
    rows = [
        {"type": "user", "sessionId": session, "cwd": cwd, "message": {"role": "user"}},
        {
            "type": "assistant",
            "sessionId": session,
            "cwd": cwd,
            "isSidechain": False,
            "message": {
                "role": "assistant",
                "id": "final-message",
                "content": [{"type": "text", "text": "Commit/checks then "}],
            },
        },
        {
            "type": "assistant",
            "sessionId": session,
            "cwd": cwd,
            "isSidechain": True,
            "message": {
                "role": "assistant",
                "id": "sidechain",
                "content": [{"type": "text", "text": "do not include"}],
            },
        },
        {
            "type": "assistant",
            "sessionId": session,
            "cwd": cwd,
            "isSidechain": False,
            "message": {
                "role": "assistant",
                "id": "final-message",
                "content": [
                    {"type": "thinking", "thinking": "done"},
                    {"type": "tool_use", "name": "git"},
                    {"type": "text", "text": "PR"},
                ],
            },
        },
    ]
    native = project / f"{session}.jsonl"
    native.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    evidence = reader.finish_turn(started, cwd=cwd)
    with native.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "assistant",
                    "isSidechain": False,
                    "message": {
                        "role": "assistant",
                        "id": "resumed-turn",
                        "content": [{"type": "text", "text": "future turn"}],
                    },
                }
            )
            + "\n"
        )

    assert evidence is not None
    assert reader.final_output(evidence, cwd=cwd) == ("Commit/checks then PR", 3)


def test_native_reader_matches_each_punctuation_and_long_project_prefix_after_metadata(
    tmp_path: Path,
) -> None:
    session = "77777777-7777-7777-7777-777777777777"
    short_cwd = "/app/repos/.worktrees/task"
    short = tmp_path / "-app-repos--worktrees-task"
    short.mkdir()
    (short / f"{session}.jsonl").write_text(
        json.dumps({"sessionId": session, "cwd": short_cwd}) + "\n", encoding="utf-8"
    )
    reader = NativeClaudeTranscriptReader(tmp_path)
    assert (
        reader.finish_turn(
            {"provider": "claude_native", "session_id": session, "line_start": 0},
            cwd=short_cwd,
        )
        is not None
    )

    long_session = "88888888-8888-8888-8888-888888888888"
    long_cwd = f"/app/{'a' * 193}😀/task"
    prefix = f"-app-{'a' * 193}--"
    conflicting = tmp_path / f"{prefix}-other-hash"
    selected = tmp_path / f"{prefix}-producer-hash"
    conflicting.mkdir()
    selected.mkdir()
    (conflicting / f"{long_session}.jsonl").write_text(
        json.dumps({"sessionId": long_session, "cwd": "/app/repos/other"}) + "\n",
        encoding="utf-8",
    )
    (selected / f"{long_session}.jsonl").write_text(
        json.dumps({"sessionId": long_session, "cwd": long_cwd}) + "\n", encoding="utf-8"
    )
    resumed = reader.start_turn(long_session, long_cwd)
    assert resumed is not None
    assert resumed["line_start"] == 1

    assert (
        reader.finish_turn(
            {"provider": "claude_native", "session_id": long_session, "line_start": 0},
            cwd=long_cwd,
        )
        is not None
    )


def test_native_transcript_page_streams_huge_metadata_and_unicode_cursor_exactly(
    tmp_path: Path,
) -> None:
    session = "99999999-9999-9999-9999-999999999999"
    cwd = '/app/repos/emoji😀"{}\\project'
    project = tmp_path / "-app-repos-emoji------project"
    project.mkdir()
    reader = NativeClaudeTranscriptReader(tmp_path)
    started = reader.start_turn(session, cwd)
    assert started is not None
    huge = "x" * (2 * 1024 * 1024)
    before_metadata = {
        "sessionId": session,
        "cwd": cwd,
        "message": {"role": "user", "nested": {"blob": huge}},
    }
    row = {
        "type": "user",
        "message": {
            "role": "user",
            "nested": {"sessionId": "wrong", "cwd": "/wrong", "blob": huge + "😀"},
        },
        "sessionId": session,
        "cwd": cwd,
    }
    encoded_row = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    emoji_start = encoded_row.rindex("😀".encode())
    padding = (64 * 1024 - 1 - emoji_start) % (64 * 1024)
    row["message"]["nested"]["blob"] = huge + "x" * padding + "😀"
    second = {"type": "assistant", "message": {"role": "assistant", "content": "끝"}}
    native = project / f"{session}.jsonl"
    native.write_text(
        json.dumps(before_metadata, ensure_ascii=False, separators=(",", ":"))
        + "\n"
        + json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        + "\n"
        + json.dumps(second, ensure_ascii=False, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    evidence = reader.finish_turn(started, cwd=cwd)
    assert evidence is not None

    tracemalloc.start()
    first = reader.transcript_page(evidence, cwd=cwd, cursor=None, limit=16)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert first is not None
    assert first["content"] == native.read_text(encoding="utf-8")[:16]
    assert peak < 1_000_000

    text = native.read_text(encoding="utf-8")
    emoji = text.rindex("😀")
    emoji_line = text[:emoji].count("\n")
    emoji_offset = emoji - text.rfind("\n", 0, emoji) - 1
    before = reader.transcript_page(
        evidence, cwd=cwd, cursor=f"transcript:{emoji_line}:{emoji_offset - 1}", limit=3
    )
    assert before is not None
    assert before["content"] == text[emoji - 1 : emoji + 2]
    after = reader.transcript_page(evidence, cwd=cwd, cursor=before["next_cursor"], limit=8)
    assert after is not None
    assert text[emoji - 1 : emoji + 2] + after["content"] == text[emoji - 1 : emoji + 10]
