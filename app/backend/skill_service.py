"""Skill discovery utilities for the workspace UI.

Skill content lives in Postgres now (see ``app.progenitor.content_documents``
and ``app/progenitor/state/manager.py``) — the same shared ``store`` table used
for ``mcp.json``/``schedule.json``/etc, keyed under the ``skill:`` prefix. This
is what makes a backend-side edit immediately visible to the agent's
``/skills/`` virtual mount with no extra sync step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.progenitor.content_documents import content_document_key, content_document_prefix

from .document_store import (
    PROGENITOR_STATE_PREFIX,
    BackendDocumentStore,
    get_default_document_store,
)

_SKILL_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_SKILL_KIND = "skill"


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
    """Discover and persist Postgres-backed DeepAgent skill documents."""

    def __init__(self, store: BackendDocumentStore | None = None) -> None:
        self.store = store or get_default_document_store()

    def _validate_segment(self, value: str, label: str) -> str:
        normalized = value.strip()
        if not _SKILL_SEGMENT_RE.fullmatch(normalized):
            raise ValueError(
                f"{label} must use lowercase letters, numbers, hyphens, or underscores"
            )
        return normalized

    def _skill_relative_path(self, agent: str, name: str) -> str:
        safe_agent = self._validate_segment(agent, "agent")
        safe_name = self._validate_segment(name, "skill")
        return f"{safe_agent}/{safe_name}/SKILL.md"

    def _sanitize_relative_path(self, relative_path: str) -> str:
        parts = Path(relative_path).parts
        if len(parts) != 3 or parts[2] != "SKILL.md":
            raise ValueError("Skill paths must look like <agent>/<skill>/SKILL.md")
        self._validate_segment(parts[0], "agent")
        self._validate_segment(parts[1], "skill")
        return "/".join(parts)

    def _extract_frontmatter_value(self, content: str, key: str) -> str:
        match = _FRONTMATTER_RE.match(content.replace("\r\n", "\n"))
        if not match:
            return ""
        for line in match.group("body").splitlines():
            name, separator, value = line.partition(":")
            if separator and name.strip() == key:
                return value.strip().strip("'\"")
        return ""

    def _build_record(
        self, relative_path: str, content: str, updated_at: datetime | None
    ) -> SkillRecord:
        path = Path(relative_path)
        parts = path.parts
        agent = parts[0]
        name = parts[1]
        title = self._extract_frontmatter_value(content, "name") or name
        description = self._extract_frontmatter_value(content, "description")
        preview = content.strip().replace("\r\n", "\n").replace("\n", " ")[:200]
        updated_ts = (updated_at or datetime.now(UTC)).timestamp()
        return SkillRecord(
            path=path.as_posix(),
            filename=path.name,
            directory=path.parent.as_posix(),
            agent=agent,
            name=name,
            title=title,
            description=description,
            size_bytes=len(content.encode("utf-8")),
            updated_at=updated_ts,
            preview=preview,
            content=content,
        )

    def list_skills(self, agent: str | None = None) -> list[SkillRecord]:
        rows = self.store.list_documents(
            PROGENITOR_STATE_PREFIX, content_document_prefix(_SKILL_KIND)
        )
        prefix_len = len(content_document_prefix(_SKILL_KIND))
        records = [
            self._build_record(
                key[prefix_len:], document.payload.get("content", ""), document.updated_at
            )
            for key, document in sorted(rows, key=lambda row: row[0])
            if isinstance(document.payload, dict)
            and isinstance(document.payload.get("content"), str)
        ]
        if agent is None:
            return records
        return [record for record in records if record.agent == agent]

    def get_skill(self, relative_path: str) -> SkillRecord:
        sanitized = self._sanitize_relative_path(relative_path)
        document = self.store.get_document(
            PROGENITOR_STATE_PREFIX, content_document_key(_SKILL_KIND, sanitized), None
        )
        if (
            document is None
            or not isinstance(document.payload, dict)
            or not isinstance(document.payload.get("content"), str)
        ):
            raise FileNotFoundError(relative_path)
        return self._build_record(sanitized, document.payload["content"], document.updated_at)

    def save_skill(self, relative_path: str, content: str) -> SkillRecord:
        sanitized = self._sanitize_relative_path(relative_path)
        key = content_document_key(_SKILL_KIND, sanitized)
        existing = self.store.get_document(PROGENITOR_STATE_PREFIX, key, None)
        if existing is None or not isinstance(existing.payload, dict):
            raise FileNotFoundError(relative_path)
        self.store.save_document(
            PROGENITOR_STATE_PREFIX,
            key,
            {"content": content, "updated_at": datetime.now(UTC).isoformat()},
        )
        document = self.store.get_document(PROGENITOR_STATE_PREFIX, key, None)
        assert document is not None
        return self._build_record(sanitized, content, document.updated_at)

    def create_skill(self, agent: str, name: str, content: str) -> SkillRecord:
        relative_path = self._skill_relative_path(agent, name)
        key = content_document_key(_SKILL_KIND, relative_path)
        existing = self.store.get_document(PROGENITOR_STATE_PREFIX, key, None)
        if (
            existing is not None
            and isinstance(existing.payload, dict)
            and isinstance(existing.payload.get("content"), str)
        ):
            raise FileExistsError(relative_path)
        self.store.save_document(
            PROGENITOR_STATE_PREFIX,
            key,
            {"content": content, "updated_at": datetime.now(UTC).isoformat()},
        )
        document = self.store.get_document(PROGENITOR_STATE_PREFIX, key, None)
        assert document is not None
        return self._build_record(relative_path, content, document.updated_at)
