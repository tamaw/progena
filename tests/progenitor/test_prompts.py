"""Tests for shared prompt directory resolution and loading."""

from pathlib import Path

import pytest

from app.progenitor.prompts import DEFAULT_PROMPTS_DIR, load_prompt, resolve_prompts_dir


def test_prompt_utils_use_project_prompts_by_default():
    """Default prompt resolution uses the checked-in data/prompts/ directory."""
    prompts_dir = resolve_prompts_dir()
    assert prompts_dir == DEFAULT_PROMPTS_DIR
    assert prompts_dir.exists()
    content = load_prompt("progenitor/decide.md")
    assert content


def test_prompt_utils_invalid_env_raises(monkeypatch, tmp_path):
    """Invalid PROGENITOR_DATA_DIR raises FileNotFoundError immediately."""
    invalid_dir = tmp_path / "does-not-exist"
    monkeypatch.delenv("PROGENITOR_PROMPTS_DIR", raising=False)
    monkeypatch.setenv("PROGENITOR_DATA_DIR", str(invalid_dir))

    with pytest.raises(FileNotFoundError, match="PROGENITOR_DATA_DIR"):
        resolve_prompts_dir()


def test_prompt_utils_explicit_dir_is_honored(tmp_path):
    """Explicit prompts_dir override should always be used."""
    custom_dir = tmp_path / "data" / "prompts"
    (custom_dir / "progenitor").mkdir(parents=True)
    (custom_dir / "progenitor" / "decide.md").write_text("custom decide prompt", encoding="utf-8")

    assert resolve_prompts_dir(custom_dir) == Path(custom_dir).resolve()
    assert load_prompt("progenitor/decide.md", prompts_dir=custom_dir) == "custom decide prompt"


def test_genesis_prompt_marks_memories_as_writable():
    """Genesis prompt distinguishes writable /memories from read-only mounts."""
    content = load_prompt("progenitor/genesis.md")

    assert "/memories/ maps to persisted agent memory and is writable." in content
    assert "You may use `write_file` and `edit_file` under `/memories/`" in content
