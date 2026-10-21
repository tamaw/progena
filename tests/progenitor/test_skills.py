"""
Tests for Phase 5: Skills system.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

SKILLS_DIR = Path("data/skills")


class TestSkillFiles:
    """Verify SKILL.md files are properly formatted."""

    def _get_skill_dirs(self):
        """Get all skill directories."""
        return [d for d in SKILLS_DIR.iterdir() if d.is_dir()]

    def test_skills_directory_exists(self):
        assert SKILLS_DIR.is_dir(), "data/skills/ directory should exist"

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

    def test_allowed_tools_is_space_separated_string(self):
        # The DeepAgents skills middleware only honours `allowed-tools` as a
        # space-separated string; a YAML list is silently ignored (the skill then
        # runs unrestricted). Enforce the string form so that can't regress.
        for skill_dir in self._get_skill_dirs():
            content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            frontmatter = yaml.safe_load(content.split("---", 2)[1])
            if "allowed-tools" not in frontmatter:
                continue
            assert isinstance(frontmatter["allowed-tools"], str), (
                f"{skill_dir.name}: 'allowed-tools' must be a space-separated string, "
                f"not a YAML list (lists are silently ignored by the skills middleware)"
            )

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
            "managing-mcp",
            "managing-plugins",
            "managing-schedules",
            "git-workflow",
            "self-improvement",
            "agent-architecture",
        }
        actual = {d.name for d in self._get_skill_dirs()}
        missing = expected - actual
        assert not missing, f"Missing expected skills: {missing}"


class FakeContentStateManager:
    """Minimal async state_manager stub for get/save/list content documents."""

    def __init__(self, *, prompt: str = "GENESIS") -> None:
        self._docs: dict[str, dict[str, str]] = {
            "instruction": {
                "progenitor/genesis.md": f"---\ntitle: Genesis\ndescription: Test\n---\n{prompt}"
            },
            "skill": {},
            "agent": {},
            "memory": {},
        }

    async def get_content_document(self, kind: str, relative_path: str) -> str | None:
        return self._docs[kind].get(relative_path)

    async def save_content_document(self, kind: str, relative_path: str, content: str) -> None:
        self._docs[kind][relative_path] = content

    async def delete_content_document(self, kind: str, relative_path: str) -> None:
        self._docs[kind].pop(relative_path, None)

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        return list(self._docs[kind].items())

    async def list_routine_graphs(self) -> list[dict]:
        return []


class TestSkillsWiring:
    """Test that skills are wired into the DeepAgent factory."""

    @pytest.mark.asyncio
    async def test_factory_passes_skills_to_create_deep_agent(self):
        fake_config = SimpleNamespace(
            llm=SimpleNamespace(model_default="test-model"),
            storage=SimpleNamespace(
                state_dir=Path("data/state"),
                instructions_dir=Path("data/instructions"),
                shim_bin_dir=Path("data/shims/bin"),
                shell_home_dir=Path("data/shell-home"),
                shell_workspace_dir=Path("data/shell-workspaces"),
            ),
            runtime=SimpleNamespace(shell_timeout_seconds=120),
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

                await create_progenitor_agent(
                    config=fake_config,
                    checkpointer=MagicMock(),
                    state_manager=FakeContentStateManager(),
                    skills_dirs=["data/skills"],
                )

                call_kwargs = mock_create.call_args.kwargs
                assert "skills" in call_kwargs
                assert call_kwargs["skills"] == ["data/skills"]

    @pytest.mark.asyncio
    async def test_factory_auto_discovers_skills_dir(self):
        """When no skills_dirs given, auto-discovers from Postgres skill documents."""
        fake_config = SimpleNamespace(
            llm=SimpleNamespace(model_default="test-model"),
            storage=SimpleNamespace(
                state_dir=Path("data/state"),
                instructions_dir=Path("data/instructions"),
                shim_bin_dir=Path("data/shims/bin"),
                shell_home_dir=Path("data/shell-home"),
                shell_workspace_dir=Path("data/shell-workspaces"),
            ),
            runtime=SimpleNamespace(shell_timeout_seconds=120),
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

                state_manager = FakeContentStateManager()
                state_manager._docs["skill"]["example/SKILL.md"] = "---\nname: example\n---\n"
                await create_progenitor_agent(
                    config=fake_config,
                    checkpointer=MagicMock(),
                    state_manager=state_manager,
                    project_root=".",
                )

                call_kwargs = mock_create.call_args.kwargs
                assert call_kwargs.get("skills") == ["/skills"]

    @pytest.mark.asyncio
    async def test_factory_discovers_native_and_plugin_skill_sources_native_first(self):
        """Auto-discovery emits /skills (native) first, then one source per
        installed marketplace plugin segment, sorted."""
        fake_config = SimpleNamespace(
            llm=SimpleNamespace(model_default="test-model"),
            storage=SimpleNamespace(
                state_dir=Path("data/state"),
                instructions_dir=Path("data/instructions"),
                shim_bin_dir=Path("data/shims/bin"),
                shell_home_dir=Path("data/shell-home"),
                shell_workspace_dir=Path("data/shell-workspaces"),
            ),
            runtime=SimpleNamespace(shell_timeout_seconds=120),
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

                state_manager = FakeContentStateManager()
                state_manager._docs["skill"]["example/SKILL.md"] = "---\nname: example\n---\n"
                state_manager._docs["skill"]["zebra-marketplace__formatter/lint/SKILL.md"] = (
                    "---\nname: lint\n---\n"
                )
                state_manager._docs["skill"]["acme-marketplace__formatter/quality/SKILL.md"] = (
                    "---\nname: quality\n---\n"
                )
                await create_progenitor_agent(
                    config=fake_config,
                    checkpointer=MagicMock(),
                    state_manager=state_manager,
                    project_root=".",
                )

                call_kwargs = mock_create.call_args.kwargs
                assert call_kwargs.get("skills") == [
                    "/skills",
                    "/skills/acme-marketplace__formatter",
                    "/skills/zebra-marketplace__formatter",
                ]

    @pytest.mark.asyncio
    async def test_factory_skips_skills_when_no_progenitor_skill_docs(self, tmp_path):
        """Auto-discovery leaves skills_dirs unset when there's no native skills
        dir on disk and no installed marketplace-plugin skill documents."""
        fake_config = SimpleNamespace(
            llm=SimpleNamespace(model_default="test-model"),
            storage=SimpleNamespace(
                state_dir=Path("data/state"),
                instructions_dir=Path("data/instructions"),
                shim_bin_dir=Path("data/shims/bin"),
                shell_home_dir=Path("data/shell-home"),
                shell_workspace_dir=Path("data/shell-workspaces"),
            ),
            runtime=SimpleNamespace(shell_timeout_seconds=120),
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

                # Empty project root: data/skills/ doesn't exist on disk (no
                # native skills) and no marketplace-plugin skill documents exist.
                await create_progenitor_agent(
                    config=fake_config,
                    checkpointer=MagicMock(),
                    state_manager=FakeContentStateManager(),
                    project_root=str(tmp_path),
                )

                call_kwargs = mock_create.call_args.kwargs
                assert call_kwargs.get("skills") is None
