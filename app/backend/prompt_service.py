"""Prompt discovery utilities for the workspace UI."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.progenitor.llm.prompts import resolve_project_root, resolve_prompts_dir

from .state_reader import StateReader, get_state_reader

_PROMPT_LITERAL_RE = re.compile(r"['\"]([A-Za-z0-9_./-]+\.md)['\"]")


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _expand_filter_names(value: str) -> set[str]:
    normalized = _normalize_name(value)
    if not normalized:
        return set()

    names = {normalized}
    if normalized.endswith("copilot"):
        names.add(f"{normalized.removesuffix('copilot')}agent")
    if normalized.endswith("agent"):
        names.add(f"{normalized.removesuffix('agent')}copilot")
    if normalized == "progenitor":
        names.add("builderprogenitor")
    if normalized == "builderprogenitor":
        names.add("progenitor")
    return names


@dataclass(frozen=True)
class PromptRecord:
    """Workspace-facing prompt metadata."""

    path: str
    filename: str
    directory: str
    category: str | None
    primary_agent: str | None
    associated_agents: list[str]
    size_bytes: int
    updated_at: float
    preview: str
    content: str


class PromptService:
    """Discover prompt files and infer the agents that use them."""

    def __init__(
        self,
        prompts_dir: Path | str | None = None,
        state_reader: StateReader | None = None,
    ) -> None:
        self.prompts_dir = resolve_prompts_dir(prompts_dir)
        self.state_reader = state_reader or get_state_reader()
        self._known_agents = self._load_known_agents()
        self._usage_index = self._build_usage_index()

    def _load_known_agents(self) -> dict[str, str]:
        agents, _ = self.state_reader.get_registry()
        known_agents = {"progenitor": "progenitor"}
        for agent in agents:
            if not agent.name:
                continue
            known_agents[_normalize_name(agent.name)] = agent.name
        return known_agents

    def _build_usage_index(self) -> dict[str, set[str]]:
        usage_index: dict[str, set[str]] = {}
        search_root = resolve_project_root(self.prompts_dir) / "app"
        if not search_root.exists():
            return usage_index

        for source_path in search_root.rglob("*.py"):
            agent_name = self._infer_agent_from_source(source_path)
            if agent_name is None:
                continue
            try:
                content = source_path.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in _PROMPT_LITERAL_RE.findall(content):
                if not match.endswith(".md"):
                    continue
                usage_index.setdefault(match, set()).add(agent_name)
        return usage_index

    def _infer_agent_from_source(self, source_path: Path) -> str | None:
        parts = [_normalize_name(part) for part in source_path.parts]
        if "agents" in parts:
            agent_index = parts.index("agents") + 1
            if agent_index < len(source_path.parts):
                candidate = source_path.parts[agent_index]
                return self._known_agents.get(_normalize_name(candidate), candidate)
        if "progenitor" in parts:
            return "progenitor"
        return None

    def _sanitize_relative_path(self, relative_path: str) -> Path:
        candidate = (self.prompts_dir / relative_path).resolve()
        candidate.relative_to(self.prompts_dir.resolve())
        if candidate.suffix.lower() != ".md":
            raise ValueError("Only markdown prompt files can be accessed")
        if not candidate.is_file():
            raise FileNotFoundError(relative_path)
        return candidate

    def _infer_agents(self, relative_path: Path) -> tuple[str | None, list[str]]:
        agents: set[str] = set(self._usage_index.get(relative_path.as_posix(), set()))

        parts = relative_path.parts
        category = parts[0] if parts else None
        if category:
            if category == "skills":
                skill_agent = parts[1] if len(parts) > 1 else "progenitor"
                agents.add(self._known_agents.get(_normalize_name(skill_agent), skill_agent))
            elif category == "progenitor":
                agents.add("progenitor")
            else:
                agents.add(self._known_agents.get(_normalize_name(category), category))

        ordered_agents = sorted(agent for agent in agents if agent)
        primary_agent = ordered_agents[0] if ordered_agents else None
        if category:
            if category == "skills":
                skill_agent = parts[1] if len(parts) > 1 else "progenitor"
                category_agent = self._known_agents.get(_normalize_name(skill_agent), skill_agent)
            else:
                category_agent = self._known_agents.get(_normalize_name(category), category)
            if category_agent in ordered_agents:
                primary_agent = category_agent

        return primary_agent, ordered_agents

    def _build_record(self, prompt_file: Path) -> PromptRecord:
        relative_path = prompt_file.relative_to(self.prompts_dir)
        content = prompt_file.read_text(encoding="utf-8")
        stat = prompt_file.stat()
        primary_agent, associated_agents = self._infer_agents(relative_path)
        preview = content.strip().replace("\r\n", "\n").replace("\n", " ")[:200]
        directory = relative_path.parent.as_posix()
        category = relative_path.parts[0] if relative_path.parts else None
        return PromptRecord(
            path=relative_path.as_posix(),
            filename=prompt_file.name,
            directory="" if directory == "." else directory,
            category=category,
            primary_agent=primary_agent,
            associated_agents=associated_agents,
            size_bytes=stat.st_size,
            updated_at=stat.st_mtime,
            preview=preview,
            content=content,
        )

    def list_prompts(self, agent: str | None = None) -> list[PromptRecord]:
        filter_names = _expand_filter_names(agent) if agent else None
        prompts = [self._build_record(path) for path in sorted(self.prompts_dir.rglob("*.md"))]
        if filter_names is None:
            return prompts
        return [
            prompt
            for prompt in prompts
            if any(_normalize_name(name) in filter_names for name in prompt.associated_agents)
            or _normalize_name(prompt.primary_agent or "") in filter_names
        ]

    def get_prompt(self, relative_path: str) -> PromptRecord:
        prompt_file = self._sanitize_relative_path(relative_path)
        return self._build_record(prompt_file)

    def save_prompt(self, relative_path: str, content: str) -> PromptRecord:
        prompt_file = self._sanitize_relative_path(relative_path)
        prompt_file.write_text(content, encoding="utf-8")
        return self._build_record(prompt_file)
