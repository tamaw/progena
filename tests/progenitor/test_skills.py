"""
Tests for Phase 5: Skills system.
"""

from pathlib import Path

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
