from pathlib import Path

import pytest
from deepagents.backends import CompositeBackend
from deepagents.middleware.filesystem import FilesystemMiddleware, supports_execution

from app.progenitor.agent.object_agent_manifest import (
    ObjectAgentSource,
    parse_object_agent_manifest,
)
from app.progenitor.agent.object_agents import create_object_agent_proxy
from app.progenitor.agent.shell_backend import ShellPaths, build_shell_env
from app.progenitor.config import RuntimeConfig, StorageConfig

SHELL_SYSTEM = """---
name: shell-reviewer
description: Reviews a repository.
version: 1.0.0
kind: object
tools: []
shell: true
env_passthrough: []
---
Use observed command output.
"""


class _StateManager:
    async def get_content_document(self, _kind, _relative_path):
        return self.content

    def __init__(self, content):
        self.content = content


class _Compiled:
    async def ainvoke(self, _state):
        return {"messages": []}


def _source(tmp_path: Path, content: str) -> ObjectAgentSource:
    agent_dir = tmp_path / "shell-reviewer"
    agent_dir.mkdir()
    system_path = agent_dir / "SYSTEM.md"
    system_path.write_text(content, encoding="utf-8")
    return ObjectAgentSource(
        manifest=parse_object_agent_manifest(content, source=str(system_path)),
        source="native",
        relative_path="native/shell-reviewer/SYSTEM.md",
    )


async def _compiled_backend(monkeypatch, source, tmp_path: Path, *, workspace_root=None):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return _Compiled()

    monkeypatch.setattr("app.progenitor.agent.object_agents.create_agent", create)
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware",
        lambda **_kwargs: object(),
    )
    proxy = create_object_agent_proxy(
        source,
        state_manager=_StateManager(
            (tmp_path / "shell-reviewer" / "SYSTEM.md").read_text(encoding="utf-8")
        ),
        model="model",
        tools=[],
        backend=CompositeBackend(default=object(), routes={"/repos/": object()}),
        checkpointer=object(),
        worktree_dir=tmp_path / "repos",
        shell_paths=ShellPaths(tmp_path / "shims", tmp_path / "homes", 120, workspace_root),
    )
    await proxy["runnable"].ainvoke({"messages": []})
    filesystem = next(
        middleware
        for middleware in calls[0]["middleware"]
        if isinstance(middleware, FilesystemMiddleware)
    )
    return filesystem.backend


def test_shell_manifest_defaults():
    manifest = parse_object_agent_manifest(SHELL_SYSTEM, source="test")

    assert manifest.shell is not None
    assert manifest.shell.max_output_bytes == 100_000
    assert manifest.shell.system_path == "/usr/bin:/bin"


@pytest.mark.parametrize("shell", ["", "shell: false\n"])
def test_absent_or_disabled_shell_is_not_enabled(shell: str):
    content = SHELL_SYSTEM.replace("shell: true\nenv_passthrough: []\n", shell)
    assert parse_object_agent_manifest(content, source="test").shell is None


@pytest.mark.parametrize(
    ("shell_block", "match"),
    [
        ("shell:\n  enabled: true\nenv_passthrough: []\n", "shell.*boolean"),
        ("shell: false\nenv_passthrough: []\n", "env_passthrough requires shell"),
        ("shell: false\ngit_write: true\n", "git_write requires shell"),
        ("shell: true\ngit_write: yes please\n", "git_write.*boolean"),
        ("shell: false\ngit_publish: true\n", "git_publish requires shell"),
        ("shell: true\ngit_publish: true\n", "git_publish requires git_write"),
    ],
)
def test_shell_rejects_nested_or_disabled_environment_settings(shell_block: str, match: str):
    content = SHELL_SYSTEM.replace("shell: true\nenv_passthrough: []\n", shell_block)

    with pytest.raises(ValueError, match=match):
        parse_object_agent_manifest(content, source="test")


@pytest.mark.asyncio
async def test_shell_proxy_exposes_execute_capability(monkeypatch, tmp_path: Path):
    backend = await _compiled_backend(monkeypatch, _source(tmp_path, SHELL_SYSTEM), tmp_path)

    assert supports_execution(backend)
    assert backend.default.cwd == (tmp_path / "shell-workspaces" / "shell-reviewer")
    assert backend.default._default_timeout == 120


@pytest.mark.asyncio
async def test_shell_workspace_is_independent_of_repository_mount(monkeypatch, tmp_path: Path):
    repos = tmp_path / "repos"
    repos.mkdir()
    # The legacy location cannot contain a workspace; repos must remain untouched.
    (repos / ".object-agent-workspaces").write_text("unavailable", encoding="utf-8")
    workspace_root = tmp_path / "runtime-volume"
    backend = await _compiled_backend(
        monkeypatch, _source(tmp_path, SHELL_SYSTEM), tmp_path, workspace_root=workspace_root
    )

    assert backend.default.cwd == workspace_root / "shell-reviewer"
    result = backend.execute("printf workspace-ok > probe.txt; cat probe.txt")
    assert result.exit_code == 0
    assert "workspace-ok" in result.output
    assert (workspace_root / "shell-reviewer" / "probe.txt").read_text() == "workspace-ok"
    assert list(repos.iterdir()) == [repos / ".object-agent-workspaces"]


@pytest.mark.asyncio
async def test_non_shell_proxy_does_not_expose_execute_capability(monkeypatch, tmp_path: Path):
    content = SHELL_SYSTEM.replace("shell: true\nenv_passthrough: []\n", "")
    source = _source(tmp_path, content)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return _Compiled()

    monkeypatch.setattr("app.progenitor.agent.object_agents.create_agent", create)
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware",
        lambda **_kwargs: object(),
    )
    proxy = create_object_agent_proxy(
        source,
        state_manager=_StateManager(content),
        model="model",
        tools=[],
        backend=CompositeBackend(default=object(), routes={}),
        checkpointer=object(),
    )
    await proxy["runnable"].ainvoke({"messages": []})

    assert not any(
        isinstance(middleware, FilesystemMiddleware) for middleware in calls[0]["middleware"]
    )


@pytest.mark.asyncio
async def test_shell_proxy_rejects_unrouted_permissions(monkeypatch, tmp_path: Path):
    content = SHELL_SYSTEM.replace(
        "tools: []\n",
        "tools: []\nmounts:\n  - path: /repos/\n    access: read\n",
    ).replace(
        "shell: true\nenv_passthrough: []\n",
        "permissions:\n  - operations: [read]\n    paths: [/nope/]\nshell: true\nenv_passthrough: []\n",
    )

    with pytest.raises(ValueError, match="outside declared mounts"):
        _source(tmp_path, content)


def test_shell_environment_is_scrubbed_and_explicit():
    manifest = parse_object_agent_manifest(
        SHELL_SYSTEM.replace("env_passthrough: []", "env_passthrough: [GH_TOKEN]"),
        source="test",
    )
    assert manifest.shell is not None

    environment = build_shell_env(
        shim_bin=Path("/shims"),
        agent_home=Path("/home/agent"),
        config=manifest.shell,
        parent_env={
            "GH_TOKEN": "token",
            "AZURE_OPENAI_API_KEY": "secret",
            "GH_HOST": "evil.com",
            "GIT_DIR": "/elsewhere/.git",
        },
    )

    assert environment["PATH"] == "/shims:/usr/bin:/bin"
    assert environment["GH_TOKEN"] == "token"
    assert "AZURE_OPENAI_API_KEY" not in environment
    assert "GH_HOST" not in environment
    assert "GIT_DIR" not in environment


def test_git_write_is_opt_in_and_exposes_the_repos_dir():
    read_only = parse_object_agent_manifest(SHELL_SYSTEM, source="test").shell
    writer = parse_object_agent_manifest(
        SHELL_SYSTEM.replace("shell: true\n", "shell: true\ngit_write: true\n"), source="test"
    ).shell
    assert read_only is not None and writer is not None

    def env(config):
        return build_shell_env(
            shim_bin=Path("/shims"),
            agent_home=Path("/home/agent"),
            config=config,
            parent_env={"PROGENITOR_SHIM_GIT_WRITE": "1", "PROGENITOR_SHIM_GIT_PUBLISH": "1"},
            repos_dir=Path("/data/repos"),
        )

    publisher = parse_object_agent_manifest(
        SHELL_SYSTEM.replace("shell: true\n", "shell: true\ngit_write: true\ngit_publish: true\n"),
        source="test",
    ).shell
    assert publisher is not None

    # The parent's values never leak in; only the manifest grants write/publish.
    assert "PROGENITOR_SHIM_GIT_WRITE" not in env(read_only)
    assert env(writer)["PROGENITOR_SHIM_GIT_WRITE"] == "1"
    assert "PROGENITOR_SHIM_GIT_PUBLISH" not in env(writer)
    assert env(publisher)["PROGENITOR_SHIM_GIT_PUBLISH"] == "1"
    assert env(read_only)["PROGENITOR_REPOS_DIR"] == "/data/repos"


def test_git_write_is_reserved_for_native_agents():
    content = SHELL_SYSTEM.replace("shell: true\nenv_passthrough: []\n", "git_write: true\n")
    with pytest.raises(ValueError, match="only allowed for native agents"):
        parse_object_agent_manifest(content, source="test", source_kind="user")


def test_shell_timeout_comes_from_environment(monkeypatch):
    monkeypatch.delenv("PROGENITOR_SHELL_TIMEOUT_SECONDS", raising=False)
    assert RuntimeConfig.from_env().shell_timeout_seconds == 120

    monkeypatch.setenv("PROGENITOR_SHELL_TIMEOUT_SECONDS", "90")
    assert RuntimeConfig.from_env().shell_timeout_seconds == 90


def test_shell_workspace_configuration(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PROGENITOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("PROGENITOR_SHELL_WORKSPACE_DIR", raising=False)
    assert StorageConfig.from_env().shell_workspace_dir == tmp_path / "data" / "shell-workspaces"
    monkeypatch.setenv("PROGENITOR_SHELL_WORKSPACE_DIR", str(tmp_path / "volume"))
    assert StorageConfig.from_env().shell_workspace_dir == tmp_path / "volume"
