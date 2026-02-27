from pathlib import Path

import pytest

from app.agents.builder_agent.runtime.background_build import (
    _assert_expected_output_exists,
    _assert_pr_created_if_required,
    _resolve_repo_path,
)


def test_assert_expected_output_exists_passes_when_directory_has_files(tmp_path: Path):
    project_dir = tmp_path / "app" / "agents" / "demo_agent"
    project_dir.mkdir(parents=True)
    (project_dir / "main.py").write_text("print('ok')", encoding="utf-8")
    build_state = {"build_settings": {"project_directory": "app/agents/demo_agent"}}

    _assert_expected_output_exists(tmp_path, build_state)


def test_assert_expected_output_exists_fails_when_directory_missing(tmp_path: Path):
    build_state = {"build_settings": {"project_directory": "app/agents/missing_agent"}}

    with pytest.raises(RuntimeError, match="expected output path"):
        _assert_expected_output_exists(tmp_path, build_state)


def test_resolve_repo_path_prefers_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo_root = tmp_path / "repo-root"
    repo_root.mkdir(parents=True)
    monkeypatch.setenv("PROGENITOR_PROJECT_ROOT", str(repo_root))

    resolved = _resolve_repo_path(Path("worktrees"))
    assert resolved == repo_root


def test_assert_pr_created_if_required_fails_without_publish_result():
    build_state = {"build_settings": {"build_type": "agent"}}

    with pytest.raises(RuntimeError, match="creating a GitHub PR"):
        _assert_pr_created_if_required(build_state)


def test_assert_pr_created_if_required_returns_url():
    build_state = {
        "build_settings": {"build_type": "agent"},
        "build_messages": [{"phase": "publish", "pr_status": "created", "pr_url": "https://github.com/o/r/pull/1"}],
    }

    assert _assert_pr_created_if_required(build_state) == "https://github.com/o/r/pull/1"
