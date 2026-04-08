from pathlib import Path

from app.agents.builder_agent.config import _default_project_root


def test_default_project_root_handles_repo_layout() -> None:
    assert _default_project_root(Path("/repo/app/agents/builder_agent/config.py")) == Path("/repo")


def test_default_project_root_handles_container_layout() -> None:
    assert _default_project_root(Path("/app/agent/config.py")) == Path("/app")
