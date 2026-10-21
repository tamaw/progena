import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("PROGENITOR_DATA_DIR", str(Path(__file__).resolve().parents[3] / "data"))

from app.agents.builder_agent.llm.prompts import PromptTemplates
from app.agents.builder_agent.postgres_skills import PLUGINS_DOC_KEY, SKILL_KEY_PREFIX

_MARKETPLACE = "acme-marketplace"
_SEGMENT = f"{_MARKETPLACE}__builder-agent"

_DOCS = {
    PLUGINS_DOC_KEY: {
        "payload": {"plugins": {f"{_MARKETPLACE}/builder-agent": {"source": "acme/builder-agent"}}}
    },
    f"{SKILL_KEY_PREFIX}{_SEGMENT}/build-agent/SKILL.md": {
        "payload": {"content": "---\nname: build-agent\n---\n\n# Build Agent Skill\n"}
    },
    f"{SKILL_KEY_PREFIX}{_SEGMENT}/git-workflow/SKILL.md": {
        "payload": {"content": "---\nname: git-workflow\n---\n\n# Git Workflow Skill\n"}
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
    store = FakeSkillStore(_DOCS)
    with patch(
        "app.agents.builder_agent.postgres_skills.ensure_skill_store",
        AsyncMock(return_value=store),
    ):
        yield


async def test_builder_skill_context_missing_skill_raises():
    prompts = PromptTemplates()

    with pytest.raises(FileNotFoundError, match="does-not-exist/SKILL.md"):
        await prompts.get_builder_skill_context(["does-not-exist"])
