import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("PROGENITOR_DATA_DIR", str(Path(__file__).resolve().parents[3] / "data"))

from app.agents.builder_agent.llm.prompts import PromptTemplates
from app.agents.builder_agent.postgres_skills import SKILL_KEY_PREFIX, resolve_skill_segment

_SEGMENT = resolve_skill_segment("builder_agent")

_SKILLS = {
    f"{SKILL_KEY_PREFIX}{_SEGMENT}/build-agent/SKILL.md": (
        "---\nname: build-agent\n---\n\n# Build Agent Skill\n"
    ),
    f"{SKILL_KEY_PREFIX}{_SEGMENT}/git-workflow/SKILL.md": (
        "---\nname: git-workflow\n---\n\n# Git Workflow Skill\n"
    ),
}


class FakeSkillStore:
    """Minimal stand-in for PostgresStore.get(namespace, key)."""

    def __init__(self, skills: dict[str, str]) -> None:
        self._skills = skills

    def get(self, _namespace: tuple[str, ...], key: str) -> SimpleNamespace | None:
        content = self._skills.get(key)
        if content is None:
            return None
        return SimpleNamespace(value={"payload": {"content": content}})


@pytest.fixture(autouse=True)
def fake_skill_store():
    with patch(
        "app.agents.builder_agent.postgres_skills.ensure_skill_store",
        return_value=FakeSkillStore(_SKILLS),
    ):
        yield


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


def test_builder_skill_context_missing_skill_raises():
    prompts = PromptTemplates()

    with pytest.raises(FileNotFoundError, match="does-not-exist/SKILL.md"):
        prompts.get_builder_skill_context(["does-not-exist"])
