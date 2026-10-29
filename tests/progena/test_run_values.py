"""How run steps' outputs are summarised on one transcript line.

Shapes mirror real ``node_outputs`` from production workflow runs.
"""

from __future__ import annotations

import yaml

from src.progena.runs.values import describe_value, full_text, head_tail


def test_long_text_keeps_both_ends_its_size_and_where_to_read_it() -> None:
    text = "I've written the regression tests first. " + "ü" * 2000 + " All 6 tests now pass."
    line = head_tail(text, pointer="/runs/workflows/x.md#2")
    assert line.startswith("\"I've written the regression tests first.")
    assert line.endswith('All 6 tests now pass."')
    # UTF-8 size, not characters: 2000 two-byte characters.
    assert "⟨4 KB · /runs/workflows/x.md#2⟩" in line


def test_text_that_fits_is_shown_whole_on_one_line() -> None:
    assert head_tail("line one\n  line two") == '"line one line two"'
    assert len(head_tail("x" * 150)) == 152  # 150 characters kept, plus quotes


def test_a_document_change_shows_only_what_changed() -> None:
    trigger = {
        "key": "kanban_card:card_f34cf82c490b",
        "op": "updated",
        "after": {"title": "Writable TMPDIR", "column_id": "validate", "status": "idle"},
        "before": {"title": "Writable TMPDIR", "column_id": "backlog", "status": "idle"},
        "changed_fields": ["column_id", "updated_at"],
    }
    assert describe_value(trigger) == (
        '/tasks/card_f34cf82c490b.md "Writable TMPDIR" column_id backlog → validate'
    )


def test_a_change_to_many_fields_names_three_and_counts_the_rest() -> None:
    fields = ["a", "b", "c", "d", "e"]
    trigger = {
        "key": "acp_task:cb1e884205d046e5",
        "after": dict.fromkeys(fields, 2),
        "before": dict.fromkeys(fields, 1),
        "changed_fields": fields,
    }
    assert describe_value(trigger) == "acp_task cb1e8842 a 1 → 2, b 1 → 2, c 1 → 2 +2 more"


def test_documents_point_at_their_mount_path_with_title() -> None:
    created = {
        "id": "de317823",
        "key": "alert:de317823",
        "document": {"title": "Self-build build failure", "message": "x" * 5000},
    }
    assert describe_value(created) == '/alerts/de317823.json "Self-build build failure"'
    card = {"key": "kanban_card:card_1", "found": True, "document": {"project_id": "web"}}
    assert describe_value(card) == "/tasks/web/card_1.md"
    assert describe_value({"key": "inbox_message:m1", "found": False}) == "/inbox/m1.md not found"


def test_lists_and_query_results_are_counted() -> None:
    assert describe_value({"keys": [], "count": 0, "items": [], "total": 0}) == "0 items"
    assert describe_value([1, 2, 3]) == "3 items"


def test_a_taken_branch_is_named() -> None:
    assert describe_value({"false": True}) == "false"


def test_wrapped_values_are_unwrapped() -> None:
    assert describe_value({"text": "card_159f7448a57e"}) == "card_159f7448a57e"
    assert describe_value({"value": {"status": "running", "column_id": "build"}}) == (
        "status: running, column_id: build"
    )


def test_a_large_structure_shows_its_shape_and_hides_internal_fields() -> None:
    task = {
        "_invocation": {"source": "cron"},
        "cwd": "/app/repos/progena.worktrees/self-build-card_f34cf82c490b",
        "mode": "bypassPermissions",
        "turn": 1,
        "stalled": False,
        "head_sha": "",
        "output": "I've written the regression tests first. " * 120,
        "history": [{"turn": 1}],
        "progress": {"done": 3},
        "card_id": "card_f34cf82c490b",
    }
    line = describe_value(task, pointer="/runs/workflows/x.md#3")
    assert line == (
        "{cwd: /app/repos/progena.worktrees/self-build-card_f34cf82c490b, mode: bypassPermissions, "
        'turn: 1, stalled: false, head_sha: "", output: 5 KB text, +3 more} '
        "⟨/runs/workflows/x.md#3⟩"
    )


def test_the_full_value_reads_back_as_the_original_data() -> None:
    value = {"pr_url": "https://github.com/tamaw/progena/pull/450", "body": "line\n" * 40}
    text = full_text(value)
    assert "body: |" in text  # long text is a literal block, readable as written
    assert yaml.safe_load(text) == value
