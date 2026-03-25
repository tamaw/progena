from app.agents.builder_progenitor import prompts
from app.agents.builder_progenitor.prompts import load_prompt, load_skill_context


def _clear_prompt_caches() -> None:
    prompts.get_prompts_dir.cache_clear()


def test_execution_prompt_template_exists():
    prompt = load_prompt(
        "execution_template.md",
        request_text="req",
        context_json="{}",
        task_name="Task",
        work_item_id="task-id",
        branch_name="progenitor/task-id",
        description="desc",
        copilot_goal="goal",
        success_criteria="- one",
        validation_targets="- pytest",
        reasoning="because",
        planned_at_utc="2026-03-23T16:00:00Z",
        skill_context="skills",
    )

    assert "render_copilot_prompt" in prompt
    assert "create_pull_request" in prompt


def test_load_skill_context_reads_repo_skills():
    skill_context = load_skill_context()

    assert "Build Agent Skill" in skill_context


def test_load_prompt_seeds_missing_file_from_bundled_dir(tmp_path, monkeypatch):
    prompts_root = tmp_path / "mounted-prompts"
    bundled_root = tmp_path / "bundled-prompts"
    (prompts_root / "builder_progenitor").mkdir(parents=True)
    (bundled_root / "builder_progenitor").mkdir(parents=True)
    bundled_prompt = bundled_root / "builder_progenitor" / "hook_system.md"
    bundled_prompt.write_text("bundled hook prompt", encoding="utf-8")

    monkeypatch.setenv("PROGENITOR_PROMPTS_DIR", str(prompts_root))
    monkeypatch.setenv("PROGENITOR_BUNDLED_PROMPTS_DIR", str(bundled_root))
    _clear_prompt_caches()

    try:
        assert load_prompt("hook_system.md") == "bundled hook prompt"
        assert (prompts_root / "builder_progenitor" / "hook_system.md").read_text(
            encoding="utf-8"
        ) == "bundled hook prompt"
    finally:
        _clear_prompt_caches()


def test_load_prompt_falls_back_to_system_prompt(tmp_path, monkeypatch):
    prompts_root = tmp_path / "mounted-prompts"
    (prompts_root / "builder_progenitor").mkdir(parents=True)
    (prompts_root / "builder_progenitor" / "system.md").write_text(
        "system fallback",
        encoding="utf-8",
    )

    monkeypatch.setenv("PROGENITOR_PROMPTS_DIR", str(prompts_root))
    monkeypatch.delenv("PROGENITOR_BUNDLED_PROMPTS_DIR", raising=False)
    _clear_prompt_caches()

    try:
        assert (
            load_prompt("hook_system.md", fallback_filename="system.md")
            == "system fallback"
        )
    finally:
        _clear_prompt_caches()
