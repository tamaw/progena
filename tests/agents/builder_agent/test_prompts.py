from app.agents.builder_agent.llm.prompts import PromptTemplates


def test_builder_skill_context_loads_repo_skills():
    prompts = PromptTemplates()

    skill_context = prompts.get_builder_skill_context()

    assert "Build Agent Skill" in skill_context
    assert "Git Workflow Skill" in skill_context
    assert "---" not in skill_context


def test_planner_prompt_includes_repo_skill_context():
    prompts = PromptTemplates()

    planner_prompt = prompts.get_planner_prompt()

    assert "## Repository Skills" in planner_prompt
    assert "Build Agent Skill" in planner_prompt
