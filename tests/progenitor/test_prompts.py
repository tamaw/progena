"""Tests for prompt directory resolution and loading."""

from pathlib import Path

import pytest

from app.progenitor.llm.prompts import DEFAULT_PROMPTS_DIR, PromptTemplates


def test_prompt_templates_uses_project_prompts_by_default():
    """Default prompt resolution uses the checked-in data/prompts/ directory."""
    prompts = PromptTemplates()
    assert prompts.prompts_dir == DEFAULT_PROMPTS_DIR
    assert prompts.prompts_dir.exists()
    content = prompts.get("progenitor/decide.md")
    assert content


def test_prompt_templates_invalid_env_raises(monkeypatch, tmp_path):
    """Invalid PROGENITOR_DATA_DIR raises FileNotFoundError immediately."""
    invalid_dir = tmp_path / "does-not-exist"
    monkeypatch.delenv("PROGENITOR_PROMPTS_DIR", raising=False)
    monkeypatch.setenv("PROGENITOR_DATA_DIR", str(invalid_dir))

    with pytest.raises(FileNotFoundError, match="PROGENITOR_DATA_DIR"):
        PromptTemplates()


def test_prompt_templates_explicit_dir_is_honored(tmp_path):
    """Explicit prompts_dir override should always be used."""
    custom_dir = tmp_path / "data" / "prompts"
    (custom_dir / "progenitor").mkdir(parents=True)
    (custom_dir / "progenitor" / "decide.md").write_text("custom decide prompt", encoding="utf-8")

    prompts = PromptTemplates(prompts_dir=custom_dir)
    assert prompts.prompts_dir == Path(custom_dir).resolve()
    assert prompts.get("progenitor/decide.md") == "custom decide prompt"
