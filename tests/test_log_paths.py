from pathlib import Path

import pytest

from app.log_paths import resolve_agent_log_dir


def test_resolve_agent_log_dir_prefers_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_NAME", "builder_agent")

    assert resolve_agent_log_dir(Path("/app/logs")).as_posix() == "/app/logs/builder_agent"


def test_resolve_agent_log_dir_uses_fallback_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_NAME", raising=False)

    assert resolve_agent_log_dir(Path("/app/logs"), fallback_agent_name="progenitor").as_posix() == "/app/logs/progenitor"


def test_resolve_agent_log_dir_requires_agent_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_NAME", raising=False)

    with pytest.raises(RuntimeError, match="AGENT_NAME must be set"):
        resolve_agent_log_dir(Path("/app/logs"))
