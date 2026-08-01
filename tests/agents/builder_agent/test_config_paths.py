from pathlib import Path

from app.agents.builder_agent.config import _default_project_root


def test_default_project_root_handles_repo_layout() -> None:
    # Compared by name, not absolute equality: `.resolve()` snaps a drive-less
    # POSIX-style test path onto whatever drive the test happens to run from
    # on Windows (e.g. D:/repo), so exact-path equality against Path("/repo")
    # is only ever true on POSIX.
    result = _default_project_root(Path("/repo/app/agents/builder_agent/config.py"))
    assert result.name == "repo"


def test_default_project_root_handles_container_layout() -> None:
    result = _default_project_root(Path("/app/agent/config.py"))
    assert result.name == "app"
