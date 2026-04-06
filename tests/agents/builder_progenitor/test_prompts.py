import pytest

from app.agents.builder_progenitor.prompts import load_prompt, load_skill_context


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


def test_load_skill_context_missing_skill_raises():
    with pytest.raises(FileNotFoundError, match="does-not-exist/SKILL.md"):
        load_skill_context(["does-not-exist"])
