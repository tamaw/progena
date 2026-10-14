import pytest

from app.progenitor.knowledge.pages import (
    check_log_append,
    classify_page_path,
    render_directory_index,
    render_index,
    stamp_generated,
    validate_concept_page,
)


def test_classify_reserved_and_concept_paths():
    assert classify_page_path("concepts/retry-policy.md") == "concept"
    assert classify_page_path("index.md") == "index"
    assert classify_page_path("log.md") == "log"
    with pytest.raises(ValueError):
        classify_page_path("INSTRUCTIONS.md")
    with pytest.raises(ValueError):
        classify_page_path("nested/log.md")
    with pytest.raises(ValueError):
        classify_page_path("Concepts/a.md")
    with pytest.raises(ValueError):
        classify_page_path("../a.md")


def test_concept_validation_and_generated_stamp():
    with pytest.raises(ValueError):
        validate_concept_page("# no front matter")
    content = "---\ntype: Concept\ntitle: Retry\ngenerated:\n  by: agent\n---\nBody\n"
    stamped = stamp_generated(None, content, "2026-10-12T00:00:00Z")
    assert "at:" in stamped and "2026-10-12T00:00:00Z" in stamped
    assert "by: agent" in stamped
    assert stamp_generated(stamped, stamped, "later") == stamped


def test_log_is_append_only():
    check_log_append("old\n", "old\nnew\n")
    with pytest.raises(ValueError):
        check_log_append("old\n", "changed\n")


def test_render_index_groups_types_and_directories():
    output = render_index(
        title="Engineering",
        dir_path="",
        pages=[
            ("b.md", {"type": "Decision", "title": "B", "description": "second"}),
            ("a.md", {"type": "Concept", "title": "A"}),
        ],
        subdirs=["notes"],
        is_base_root=True,
    )
    assert output == (
        "---\n"
        'okf_version: "0.2"\n'
        "---\n"
        "\n"
        "# Engineering\n"
        "\n"
        "## Concept\n"
        "- [A](a.md)\n"
        "\n"
        "## Decision\n"
        "- [B](b.md) — second\n"
        "\n"
        "## Directories\n"
        "- [notes/](notes/index.md)\n"
        "\n"
        "## Reserved\n"
        "- [log.md](log.md)\n"
    )


def test_directory_index_hides_deprecated_pages_and_emptied_folders():
    def page(title, status=None):
        extra = f"status: {status}\n" if status else ""
        return f"---\ntype: Concept\ntitle: {title}\n{extra}---\nBody\n"

    live = [
        ("current.md", page("Current")),
        ("old.md", page("Old", "Deprecated")),
        ("archive/gone.md", page("Gone", "deprecated")),
        ("guides/setup.md", page("Setup")),
        ("guides/legacy.md", page("Legacy", "deprecated")),
    ]
    root = render_directory_index(title="Eng", directory="", live_pages=live)
    assert "[Current](current.md)" in root
    assert "old.md" not in root
    assert "archive/" not in root
    assert "[guides/](guides/index.md)" in root
    guides = render_directory_index(title="guides", directory="guides", live_pages=live)
    assert "[Setup](setup.md)" in guides
    assert "legacy.md" not in guides
