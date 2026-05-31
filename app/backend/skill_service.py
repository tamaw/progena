"""Skill discovery utilities for the workspace UI."""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from app.progenitor.prompts import resolve_skills_root

_SKILL_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


@dataclass(frozen=True)
class SkillRecord:
    """Workspace-facing skill metadata."""

    path: str
    filename: str
    directory: str
    agent: str
    name: str
    title: str
    description: str
    size_bytes: int
    updated_at: float
    preview: str
    content: str


class SkillService:
    """Discover and persist DeepAgent skill files."""

    def __init__(self, skills_root: Path | str | None = None) -> None:
        self.skills_root = (
            Path(skills_root).expanduser().resolve() if skills_root else resolve_skills_root()
        )

    def _validate_segment(self, value: str, label: str) -> str:
        normalized = value.strip()
        if not _SKILL_SEGMENT_RE.fullmatch(normalized):
            raise ValueError(
                f"{label} must use lowercase letters, numbers, hyphens, or underscores"
            )
        return normalized

    def _skill_path(self, agent: str, name: str) -> Path:
        safe_agent = self._validate_segment(agent, "agent")
        safe_name = self._validate_segment(name, "skill")
        candidate = (self.skills_root / safe_agent / safe_name / "SKILL.md").resolve()
        candidate.relative_to(self.skills_root.resolve())
        return candidate

    def _sanitize_relative_path(self, relative_path: str, *, must_exist: bool = True) -> Path:
        parts = Path(relative_path).parts
        if len(parts) != 3 or parts[2] != "SKILL.md":
            raise ValueError("Skill paths must look like <agent>/<skill>/SKILL.md")

        candidate = self._skill_path(parts[0], parts[1])
        if must_exist and not candidate.is_file():
            raise FileNotFoundError(relative_path)
        return candidate

    def _extract_frontmatter_value(self, content: str, key: str) -> str:
        match = _FRONTMATTER_RE.match(content.replace("\r\n", "\n"))
        if not match:
            return ""
        for line in match.group("body").splitlines():
            name, separator, value = line.partition(":")
            if separator and name.strip() == key:
                return value.strip().strip("'\"")
        return ""

    def _build_record(self, skill_file: Path) -> SkillRecord:
        relative_path = skill_file.relative_to(self.skills_root)
        parts = relative_path.parts
        content = skill_file.read_text(encoding="utf-8")
        stat = skill_file.stat()
        agent = parts[0]
        name = parts[1]
        title = self._extract_frontmatter_value(content, "name") or name
        description = self._extract_frontmatter_value(content, "description")
        preview = content.strip().replace("\r\n", "\n").replace("\n", " ")[:200]
        return SkillRecord(
            path=relative_path.as_posix(),
            filename=skill_file.name,
            directory=relative_path.parent.as_posix(),
            agent=agent,
            name=name,
            title=title,
            description=description,
            size_bytes=stat.st_size,
            updated_at=stat.st_mtime,
            preview=preview,
            content=content,
        )

    def list_skills(self, agent: str | None = None) -> list[SkillRecord]:
        if not self.skills_root.exists():
            return []

        filter_agent = self._validate_segment(agent, "agent") if agent else None
        records = []
        for skill_file in sorted(self.skills_root.rglob("SKILL.md")):
            try:
                relative_path = skill_file.relative_to(self.skills_root)
            except ValueError:
                continue
            if len(relative_path.parts) != 3:
                continue
            if filter_agent and relative_path.parts[0] != filter_agent:
                continue
            records.append(self._build_record(skill_file))
        return records

    def get_skill(self, relative_path: str) -> SkillRecord:
        return self._build_record(self._sanitize_relative_path(relative_path))

    def save_skill(self, relative_path: str, content: str) -> SkillRecord:
        skill_file = self._sanitize_relative_path(relative_path)
        skill_file.write_text(content, encoding="utf-8")
        return self._build_record(skill_file)

    def create_skill(self, agent: str, name: str, content: str) -> SkillRecord:
        skill_file = self._skill_path(agent, name)
        if skill_file.exists():
            raise FileExistsError(skill_file.relative_to(self.skills_root).as_posix())

        skill_file.parent.mkdir(parents=True, exist_ok=False)
        try:
            with skill_file.open("x", encoding="utf-8") as handle:
                handle.write(content)
        except Exception:
            if skill_file.exists():
                skill_file.unlink()
            with suppress(OSError):
                skill_file.parent.rmdir()
            raise
        return self._build_record(skill_file)
