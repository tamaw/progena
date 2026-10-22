"""Instruction files: directory resolution, front matter rules, and the boot sync."""

from pathlib import Path

import pytest

from app.progenitor.documents.content import sync_instruction_documents
from app.progenitor.documents.types import BUILTIN_DOCUMENT_TYPES
from app.progenitor.instructions import (
    DEFAULT_INSTRUCTIONS_DIR,
    check_instruction_write,
    render_instruction,
    resolve_instructions_dir,
)
from app.progenitor.instructions.templates import parse_instruction_template

VALID = "---\ntitle: T\ndescription: D\n---\nHello {name}.\n"
TYPES = {item.id: item for item in BUILTIN_DOCUMENT_TYPES}


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


def test_invalid_data_dir_env_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("PROGENITOR_INSTRUCTIONS_DIR", raising=False)
    monkeypatch.setenv("PROGENITOR_DATA_DIR", str(tmp_path / "does-not-exist"))
    with pytest.raises(FileNotFoundError, match="PROGENITOR_DATA_DIR"):
        resolve_instructions_dir()


def test_every_checked_in_instruction_is_valid():
    """The runtime refuses to boot on an invalid file, so catch it here first."""
    paths = sorted(DEFAULT_INSTRUCTIONS_DIR.rglob("*.md"))
    for path in paths:
        content = path.read_text(encoding="utf-8")
        template = parse_instruction_template(content)
        assert set(template.inputs) == set(template.roots), (
            f"{path}: declare a typed input for every placeholder root"
        )
        check_instruction_write(
            path.relative_to(DEFAULT_INSTRUCTIONS_DIR).as_posix(), None, content, TYPES
        )


def test_render_drops_front_matter_and_fills_variables():
    assert render_instruction(VALID, name="Ada") == "Hello Ada.\n"


@pytest.mark.parametrize(
    ("path", "content", "error"),
    [
        ("progenitor/genesis.md", "Hello {other}.\n", "{name}"),
        ("progenitor/genesis.md", "Hello.\n", "{name}"),
        ("workflows/card.md", "---\nvariables: [x]\n---\nx\n", "unknown front matter key"),
        # A bare JSON type name is not a type ref: rejected at write, naming the input.
        (
            "workflows/fix.md",
            "---\ninputs:\n  fix_list: string\n---\n{fix_list}\n",
            r"inputs 'fix_list': unknown type ref: 'string'.*\{type: string\}",
        ),
        (
            "workflows/fix.md",
            "---\noutput_schema: no_such_type\n---\nx\n",
            "output_schema: unknown type ref: 'no_such_type'",
        ),
    ],
    ids=[
        "runtime-placeholder-renamed",
        "runtime-placeholder-dropped",
        "unknown-key",
        "bare-input-type",
        "unknown-output-schema",
    ],
)
def test_rejected_instruction_writes(path, content, error):
    with pytest.raises((ValueError, PermissionError), match=error):
        check_instruction_write(path, VALID, content, TYPES)


def test_workflow_templates_may_change_placeholders():
    check_instruction_write("workflows/card.md", VALID, "Card {card.title}\n", TYPES)


@pytest.mark.parametrize("grouped", [False, True])
async def test_sync_seeds_missing_instructions_without_overwriting_saved_edits(tmp_path, grouped):
    prefix = "progenitor/" if grouped else ""
    _write(tmp_path, f"{prefix}genesis.md", VALID)
    _write(tmp_path, f"{prefix}new.md", VALID)
    _write(tmp_path, "external_agent/think.md", VALID)
    edited = VALID.replace("Hello", "Hi")
    state_manager = FakeStateManager(
        {
            "progenitor/genesis.md": edited,
            "external_agent/think.md": edited,
            "external_agent/removed.md": VALID,
            "workflows/card.md": VALID,
        }
    )

    await sync_instruction_documents(state_manager, tmp_path)

    assert state_manager.docs == {
        "progenitor/genesis.md": edited,
        "progenitor/new.md": VALID,
        "external_agent/think.md": edited,
        "external_agent/removed.md": VALID,
        "workflows/card.md": VALID,
    }


async def test_sync_rejects_an_invalid_file(tmp_path):
    _write(tmp_path, "progenitor/genesis.md", "---\nunknown: 1\n---\nBody\n")
    with pytest.raises(ValueError, match="genesis.md"):
        await sync_instruction_documents(FakeStateManager(), tmp_path)


async def test_shipped_flat_instructions_seed_existing_runtime_document_paths():
    files = sorted(DEFAULT_INSTRUCTIONS_DIR.glob("*.md"))
    assert files
    state_manager = FakeStateManager()
    await sync_instruction_documents(state_manager, DEFAULT_INSTRUCTIONS_DIR)
    assert state_manager.docs == {
        f"progenitor/{path.name}": path.read_text(encoding="utf-8") for path in files
    }


async def test_sync_rejects_flat_and_grouped_sources_with_the_same_document_path(tmp_path):
    _write(tmp_path, "genesis.md", VALID)
    _write(tmp_path, "progenitor/genesis.md", VALID.replace("Hello", "Hi"))
    state_manager = FakeStateManager()
    with pytest.raises(ValueError, match="Duplicate instruction source.*progenitor/genesis.md"):
        await sync_instruction_documents(state_manager, tmp_path)
    assert state_manager.docs == {}
