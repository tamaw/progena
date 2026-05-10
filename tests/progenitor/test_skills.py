"""
Tests for Phase 5: Skills system.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

SKILLS_DIR = Path("data/skills/progenitor")


class TestSkillFiles:
    """Verify SKILL.md files are properly formatted."""

    def _get_skill_dirs(self):
        """Get all skill directories."""
        if not SKILLS_DIR.is_dir():
            pytest.skip("data/skills/progenitor/ not found")
        return [d for d in SKILLS_DIR.iterdir() if d.is_dir()]

    def test_skills_directory_exists(self):
        assert SKILLS_DIR.is_dir(), "data/skills/progenitor/ directory should exist"

    def test_all_skills_have_skill_md(self):
        for skill_dir in self._get_skill_dirs():
            skill_md = skill_dir / "SKILL.md"
            assert skill_md.exists(), f"Missing SKILL.md in {skill_dir.name}"

    def test_all_skills_have_valid_frontmatter(self):
        for skill_dir in self._get_skill_dirs():
            content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            assert content.startswith("---"), f"{skill_dir.name}: must start with ---"
            # Extract frontmatter
            parts = content.split("---", 2)
            assert len(parts) >= 3, f"{skill_dir.name}: missing closing ---"
            frontmatter = yaml.safe_load(parts[1])
            assert isinstance(frontmatter, dict), f"{skill_dir.name}: frontmatter not a dict"
            assert "name" in frontmatter, f"{skill_dir.name}: missing name"
            assert "description" in frontmatter, f"{skill_dir.name}: missing description"

    def test_skill_names_match_directories(self):
        for skill_dir in self._get_skill_dirs():
            content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            parts = content.split("---", 2)
            frontmatter = yaml.safe_load(parts[1])
            assert frontmatter["name"] == skill_dir.name, (
                f"Skill name '{frontmatter['name']}' doesn't match directory '{skill_dir.name}'"
            )

    def test_skill_descriptions_not_empty(self):
        for skill_dir in self._get_skill_dirs():
            content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            parts = content.split("---", 2)
            frontmatter = yaml.safe_load(parts[1])
            desc = frontmatter.get("description", "")
            assert len(desc) > 10, f"{skill_dir.name}: description too short"

    def test_skill_names_valid_format(self):
        """Skill names must be lowercase alphanumeric with hyphens."""
        import re

        for skill_dir in self._get_skill_dirs():
            content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            parts = content.split("---", 2)
            frontmatter = yaml.safe_load(parts[1])
            name = frontmatter["name"]
            assert re.match(r"^[a-z0-9][a-z0-9-]*$", name), (
                f"Invalid skill name '{name}' — must be lowercase with hyphens"
            )

    def test_expected_skills_exist(self):
        """All planned skills should be present."""
        expected = {
            "build-agent",
            "scheduling",
            "git-workflow",
            "self-improvement",
            "agent-architecture",
        }
        actual = {d.name for d in self._get_skill_dirs()}
        missing = expected - actual
        assert not missing, f"Missing expected skills: {missing}"


class TestSkillsWiring:
    """Test that skills are wired into the DeepAgent factory."""

    def test_factory_passes_skills_to_create_deep_agent(self):
        fake_config = SimpleNamespace(
            llm=SimpleNamespace(model_default="test-model"),
            storage=SimpleNamespace(
                state_dir=Path("data/state"),
                prompts_dir=Path("data/prompts"),
            ),
        )
        with (
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_create.return_value = MagicMock()
            mock_backend.return_value = MagicMock()
            with patch("app.progenitor.agent.agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()
                from app.progenitor.agent.agent import create_progenitor_agent

                create_progenitor_agent(
                    config=fake_config,
                    checkpointer=MagicMock(),
                    skills_dirs=["data/skills/progenitor"],
                )

                call_kwargs = mock_create.call_args.kwargs
                assert "skills" in call_kwargs
                assert call_kwargs["skills"] == ["data/skills/progenitor"]

    def test_factory_auto_discovers_skills_dir(self):
        """When no skills_dirs given, auto-discovers the backend-mounted skill source."""
        fake_config = SimpleNamespace(
            llm=SimpleNamespace(model_default="test-model"),
            storage=SimpleNamespace(
                state_dir=Path("data/state"),
                prompts_dir=Path("data/prompts"),
            ),
        )
        with (
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_create.return_value = MagicMock()
            mock_backend.return_value = MagicMock()
            with patch("app.progenitor.agent.agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()
                from app.progenitor.agent.agent import create_progenitor_agent

                # Use project root (which has data/skills/progenitor/)
                create_progenitor_agent(
                    config=fake_config,
                    checkpointer=MagicMock(),
                    project_root=".",
                )

                call_kwargs = mock_create.call_args.kwargs
                assert call_kwargs.get("skills") == ["/data/skills/progenitor"]
