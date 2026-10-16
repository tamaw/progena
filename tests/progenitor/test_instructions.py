"""Instruction files: directory resolution, front matter rules, and the boot sync."""

from pathlib import Path

import pytest

from app.progenitor.content_documents import sync_instruction_documents
from app.progenitor.instructions import (
    DEFAULT_INSTRUCTIONS_DIR,
    check_instruction_write,
    render_instruction,
    resolve_instructions_dir,
)
from app.progenitor.instructions.templates import parse_instruction_template

VALID = "---\ntitle: T\ndescription: D\n---\nHello {name}.\n"


class FakeStateManager:
    def __init__(self, docs: dict[str, str] | None = None) -> None:
        self.docs = dict(docs or {})

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        assert kind == "instruction"
        return list(self.docs.items())

    async def save_content_document(self, kind: str, path: str, content: str) -> None:
        self.docs[path] = content

    async def delete_content_document(self, kind: str, path: str) -> None:
        self.docs.pop(path, None)


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_default_directory_is_the_checked_in_one(monkeypatch):
    monkeypatch.delenv("PROGENITOR_INSTRUCTIONS_DIR", raising=False)
    monkeypatch.delenv("PROGENITOR_DATA_DIR", raising=False)
    instructions_dir = resolve_instructions_dir()
    assert instructions_dir == DEFAULT_INSTRUCTIONS_DIR
    assert (instructions_dir / "progenitor" / "genesis.md").exists()


def test_invalid_data_dir_env_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("PROGENITOR_INSTRUCTIONS_DIR", raising=False)
    monkeypatch.setenv("PROGENITOR_DATA_DIR", str(tmp_path / "does-not-exist"))
    with pytest.raises(FileNotFoundError, match="PROGENITOR_DATA_DIR"):
        resolve_instructions_dir()


def test_every_checked_in_instruction_is_valid():
    """The runtime refuses to boot on an invalid file, so catch it here first."""
    paths = sorted(DEFAULT_INSTRUCTIONS_DIR.rglob("*.md"))
    assert paths
    for path in paths:
        parse_instruction_template(path.read_text(encoding="utf-8"))


def test_render_drops_front_matter_and_fills_variables():
    assert render_instruction(VALID, name="Ada") == "Hello Ada.\n"


@pytest.mark.parametrize(
    ("path", "content", "error"),
    [
        ("progenitor/genesis.md", "Hello {other}.\n", "{name}"),
        ("progenitor/genesis.md", "Hello.\n", "{name}"),
        ("builder_agent/think.md", VALID, "bundled with builder_agent"),
        ("workflows/card.md", "---\nvariables: [x]\n---\nx\n", "unknown front matter key"),
    ],
    ids=["runtime-placeholder-renamed", "runtime-placeholder-dropped", "bundled", "unknown-key"],
)
def test_rejected_instruction_writes(path, content, error):
    with pytest.raises((ValueError, PermissionError), match=error):
        check_instruction_write(path, VALID, content)


def test_workflow_templates_may_change_placeholders():
    check_instruction_write("workflows/card.md", VALID, "Card {card.title}\n")


async def test_sync_keeps_edited_runtime_instructions_and_mirrors_bundled_ones(tmp_path):
    _write(tmp_path, "progenitor/genesis.md", VALID)
    _write(tmp_path, "progenitor/new.md", VALID)
    _write(tmp_path, "builder_agent/think.md", VALID)
    edited = VALID.replace("Hello", "Hi")
    state_manager = FakeStateManager(
        {
            "progenitor/genesis.md": edited,
            "builder_agent/think.md": edited,
            "builder_agent/removed.md": VALID,
            "workflows/card.md": VALID,
        }
    )

    await sync_instruction_documents(state_manager, tmp_path)

    assert state_manager.docs == {
        "progenitor/genesis.md": edited,
        "progenitor/new.md": VALID,
        "builder_agent/think.md": VALID,
        "workflows/card.md": VALID,
    }


async def test_sync_rejects_an_invalid_file(tmp_path):
    _write(tmp_path, "progenitor/genesis.md", "---\nunknown: 1\n---\nBody\n")
    with pytest.raises(ValueError, match="genesis.md"):
        await sync_instruction_documents(FakeStateManager(), tmp_path)
