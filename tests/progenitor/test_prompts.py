"""Tests for prompt directory resolution and loading."""

from pathlib import Path

from app.progenitor.llm.prompts import DEFAULT_PROMPTS_DIR, PromptTemplates


def test_prompt_templates_uses_project_prompts_by_default():
    """Default prompt resolution uses top-level prompts/ directory."""
    prompts = PromptTemplates()
    assert prompts.prompts_dir == DEFAULT_PROMPTS_DIR
    assert prompts.prompts_dir.exists()
    content = prompts.get("progenitor/decide.md")
    assert content


def test_prompt_templates_invalid_env_falls_back_to_default(monkeypatch, tmp_path):
    """Invalid PROGENITOR_PROMPTS_DIR should not break prompt loading."""
    invalid_dir = tmp_path / "does-not-exist"
    monkeypatch.setenv("PROGENITOR_PROMPTS_DIR", str(invalid_dir))

    prompts = PromptTemplates()
    assert prompts.prompts_dir == DEFAULT_PROMPTS_DIR
    assert prompts.get("progenitor/decide.md")


def test_prompt_templates_explicit_dir_is_honored(tmp_path):
    """Explicit prompts_dir override should always be used."""
    custom_dir = tmp_path / "prompts"
    (custom_dir / "progenitor").mkdir(parents=True)
    (custom_dir / "progenitor" / "decide.md").write_text("custom decide prompt", encoding="utf-8")

    prompts = PromptTemplates(prompts_dir=custom_dir)
    assert prompts.prompts_dir == Path(custom_dir).resolve()
    assert prompts.get("progenitor/decide.md") == "custom decide prompt"
