"""Tests for AgentStore's SKILL.md resolve/update path.

Constructs AgentStore via object.__new__ to skip the Postgres connection pool
entirely -- resolve_skill/update_skill only ever touch self._document_store
(and, for native skills, the ``_NATIVE_SKILLS_ROOT`` mount, redirected to a
tmp dir for write tests so they never touch the real ``data/skills/`` tree).
"""

from __future__ import annotations

import pytest

from app.backend.agent_store import AgentStore
from app.backend.document_store import InMemoryDocumentStore
from app.progenitor.content_documents import content_document_key


def _make_store() -> AgentStore:
    store = object.__new__(AgentStore)
    store._document_store = InMemoryDocumentStore()  # noqa: SLF001
    return store


def test_resolve_skill_reads_native_skill_from_disk() -> None:
    store = _make_store()

    skill = store.resolve_skill("/skills/managing-agents")

    assert skill is not None
    assert skill["path"] == "/skills/managing-agents"
    assert skill["name"] == "managing-agents"
    assert skill["source"] == "native"
    assert skill["editable"] is True
    assert skill["front_matter"]["name"] == "managing-agents"


def test_resolve_skill_returns_none_for_missing_native_skill() -> None:
    store = _make_store()

    assert store.resolve_skill("/skills/does-not-exist") is None


def test_resolve_skill_reads_marketplace_skill_from_document_store() -> None:
    store = _make_store()
    content = (
        "---\nname: pr-summarizer-skill\ndescription: Summarizes PRs.\n---\nSummarize the diff.\n"
    )
    store._document_store.save_document(  # noqa: SLF001
        "progenitor.state",
        content_document_key("skill", "acme__toolkit/pr-summarizer-skill/SKILL.md"),
        {"content": content, "updated_at": "2026-08-01T00:00:00Z"},
    )

    skill = store.resolve_skill("/skills/acme__toolkit/pr-summarizer-skill")

    assert skill is not None
    assert skill["source"] == "marketplace"
    assert skill["editable"] is True
    assert skill["location"] == "acme__toolkit/pr-summarizer-skill/SKILL.md"
    assert skill["front_matter"]["description"] == "Summarizes PRs."


def test_resolve_skill_returns_none_for_missing_marketplace_skill() -> None:
    store = _make_store()

    assert store.resolve_skill("/skills/acme__toolkit/missing-skill") is None


def test_update_skill_persists_marketplace_skill_content() -> None:
    store = _make_store()
    original = "---\nname: pr-summarizer-skill\ndescription: Old.\n---\nOld body.\n"
    store._document_store.save_document(  # noqa: SLF001
        "progenitor.state",
        content_document_key("skill", "acme__toolkit/pr-summarizer-skill/SKILL.md"),
        {"content": original, "updated_at": "2026-08-01T00:00:00Z"},
    )
    updated_content = "---\nname: pr-summarizer-skill\ndescription: New.\n---\nNew body.\n"

    updated = store.update_skill("/skills/acme__toolkit/pr-summarizer-skill", updated_content)

    assert updated["front_matter"]["description"] == "New."
    reloaded = store.resolve_skill("/skills/acme__toolkit/pr-summarizer-skill")
    assert reloaded is not None
    assert reloaded["raw_content"] == updated_content


def test_update_skill_writes_native_skill_to_disk(tmp_path, monkeypatch) -> None:
    store = _make_store()
    skill_dir = tmp_path / "managing-agents"
    skill_dir.mkdir()
    original = "---\nname: managing-agents\ndescription: Old.\n---\nOld body.\n"
    (skill_dir / "SKILL.md").write_text(original, encoding="utf-8")
    monkeypatch.setattr("app.backend.agent_store._NATIVE_SKILLS_ROOT", tmp_path)

    updated_content = "---\nname: managing-agents\ndescription: New.\n---\nNew body.\n"
    updated = store.update_skill("/skills/managing-agents", updated_content)

    assert updated["source"] == "native"
    assert updated["front_matter"]["description"] == "New."
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == updated_content


def test_update_skill_rejects_rename() -> None:
    store = _make_store()

    with pytest.raises(ValueError, match="renaming"):
        store.update_skill("/skills/managing-agents", "---\nname: renamed\n---\nx\n")


def test_update_skill_raises_for_unknown_skill() -> None:
    store = _make_store()

    with pytest.raises(LookupError):
        store.update_skill("/skills/acme__toolkit/missing-skill", "---\nname: x\n---\nx\n")


@pytest.mark.parametrize(
    "path", ["/skills/..", "/skills/../etc", "/skills/foo/..", "/skills/", "/skills/."]
)
def test_resolve_skill_rejects_traversal(path: str) -> None:
    store = _make_store()
    assert store.resolve_skill(path) is None


@pytest.mark.parametrize(
    "path", ["/skills/..", "/skills/../etc", "/skills/foo/..", "/skills/", "/skills/."]
)
def test_update_skill_rejects_traversal(path: str) -> None:
    store = _make_store()
    with pytest.raises(ValueError, match="invalid segment"):
        store.update_skill(path, "---\nname: x\n---\nx\n")
