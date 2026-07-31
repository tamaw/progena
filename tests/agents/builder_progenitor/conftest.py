"""Shared fixtures for builder_progenitor tests.

Skills are Postgres-backed now (see ``postgres_skills.py``): every test in
this package gets a fake async store instead of a real database connection,
pre-populated with an installed ``builder-progenitor`` marketplace plugin and
the same skill set it provides.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.builder_progenitor.postgres_skills import PLUGINS_DOC_KEY, SKILL_KEY_PREFIX

_MARKETPLACE = "acme-marketplace"
_SEGMENT = f"{_MARKETPLACE}__builder-progenitor"

BUILDER_PROGENITOR_DOCS = {
    PLUGINS_DOC_KEY: {
        "payload": {"plugins": {f"{_MARKETPLACE}/builder-progenitor": {"source": "acme/builder-progenitor"}}}
    },
    f"{SKILL_KEY_PREFIX}{_SEGMENT}/build-agent/SKILL.md": {
        "payload": {"content": "---\nname: build-agent\n---\n\n# Build Agent Skill\n"}
    },
    f"{SKILL_KEY_PREFIX}{_SEGMENT}/git-workflow/SKILL.md": {
        "payload": {"content": "---\nname: git-workflow\n---\n\n# Git Workflow Skill\n"}
    },
    f"{SKILL_KEY_PREFIX}{_SEGMENT}/task-setup/SKILL.md": {
        "payload": {"content": "---\nname: task-setup\n---\n\n# Task Setup Skill\n"}
    },
    f"{SKILL_KEY_PREFIX}{_SEGMENT}/task-teardown/SKILL.md": {
        "payload": {"content": "---\nname: task-teardown\n---\n\n# Task Teardown Skill\n"}
    },
}


class FakeSkillStore:
    """Minimal async stand-in for AsyncPostgresStore.aget(namespace, key)."""

    def __init__(self, docs: dict[str, dict]) -> None:
        self._docs = docs

    async def aget(self, _namespace: tuple[str, ...], key: str) -> SimpleNamespace | None:
        value = self._docs.get(key)
        if value is None:
            return None
        return SimpleNamespace(value=value)


@pytest.fixture(autouse=True)
def fake_skill_store():
    store = FakeSkillStore(BUILDER_PROGENITOR_DOCS)
    with patch(
        "app.agents.builder_progenitor.postgres_skills.ensure_skill_store",
        AsyncMock(return_value=store),
    ):
        yield
