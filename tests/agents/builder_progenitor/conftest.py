"""Shared fixtures for builder_progenitor tests.

Skills are Postgres-backed now (see ``postgres_skills.py``): every test in
this package gets a fake store instead of a real database connection, pre
populated with the same skill set the installed ``builder-progenitor``
marketplace plugin provides.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.builder_progenitor.postgres_skills import SKILL_KEY_PREFIX, resolve_skill_segment

_SEGMENT = resolve_skill_segment("builder_progenitor")

BUILDER_PROGENITOR_SKILLS = {
    f"{SKILL_KEY_PREFIX}{_SEGMENT}/build-agent/SKILL.md": (
        "---\nname: build-agent\n---\n\n# Build Agent Skill\n"
    ),
    f"{SKILL_KEY_PREFIX}{_SEGMENT}/git-workflow/SKILL.md": (
        "---\nname: git-workflow\n---\n\n# Git Workflow Skill\n"
    ),
    f"{SKILL_KEY_PREFIX}{_SEGMENT}/task-setup/SKILL.md": (
        "---\nname: task-setup\n---\n\n# Task Setup Skill\n"
    ),
    f"{SKILL_KEY_PREFIX}{_SEGMENT}/task-teardown/SKILL.md": (
        "---\nname: task-teardown\n---\n\n# Task Teardown Skill\n"
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
        "app.agents.builder_progenitor.postgres_skills.ensure_skill_store",
        return_value=FakeSkillStore(BUILDER_PROGENITOR_SKILLS),
    ):
        yield
