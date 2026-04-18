from unittest.mock import patch

import pytest

from app.agents.builder.prompts import load_prompt, load_skill_context


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
    assert "teardown phase" in prompt.lower()


def test_task_setup_prompt_template_exists():
    prompt = load_prompt(
        "task_setup.md",
        request_text="req",
        context_json="{}",
        task_name="Task",
        work_item_id="task-id",
        branch_name="progenitor/task-id",
        description="desc",
        planned_at_utc="2026-03-23T16:00:00Z",
        reasoning="because",
        skill_context="skills",
    )

    assert "create_worktree" in prompt
    assert "origin/main" in prompt
    assert "Do not call `copilot_build`" in prompt


def test_task_teardown_prompt_template_exists():
    prompt = load_prompt(
        "task_teardown.md",
        request_text="req",
        context_json="{}",
        task_name="Task",
        work_item_id="task-id",
        branch_name="progenitor/task-id",
        description="desc",
        planned_at_utc="2026-03-23T16:00:00Z",
        reasoning="because",
        skill_context="skills",
    )

    assert "create_pull_request" in prompt
    assert "remove_worktree" in prompt
    assert "Do not run `copilot_build`" in prompt


def test_load_skill_context_reads_repo_skills():
    skill_context = load_skill_context()

    assert "Build Agent Skill" in skill_context
    assert "Task Setup Skill" in skill_context
    assert "Task Teardown Skill" in skill_context


def test_load_skill_context_uses_builder_skills_only():
    with patch("app.agents.builder.prompts.load_repo_skill_context") as mock_load:
        mock_load.return_value = "skills"

        result = load_skill_context()

        assert result == "skills"
        assert mock_load.call_args.kwargs["agent_name"] == "builder"
        assert mock_load.call_args.kwargs.get("fallback_agents", ()) == ()


def test_load_skill_context_missing_skill_raises():
    with pytest.raises(FileNotFoundError, match="does-not-exist/SKILL.md"):
        load_skill_context(["does-not-exist"])
