"""Generated shell homes follow live discovery without disrupting active calls."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deepagents.backends import CompositeBackend

from app.progenitor.agent.object_agent_manifest import ObjectAgentShellConfig
from app.progenitor.agent.object_agent_registry import ObjectAgentRegistry
from app.progenitor.agent.object_agents import create_object_agent_proxy
from app.progenitor.agent.shell_backend import (
    ProgenitorShellBackend,
    ShellHomeLifecycle,
    ShellPaths,
)


def definition(root, name="helper", *, shell=True):
    path = root / name / "SYSTEM.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: Help.\nversion: 1.0.0\nkind: object\n"
        f"tools: []\nshell: {str(shell).lower()}\n---\nHelp.\n"
    )
    return path


def registry(root, homes, proxy=None):
    return ObjectAgentRegistry(
        state_manager=SimpleNamespace(list_content_documents=AsyncMock(return_value=[])),
        proxy_factory=proxy
        or (
            lambda source: {
                "name": source.name,
                "description": source.manifest.description,
                "runnable": object(),
            }
        ),
        native_root=root,
        shell_homes=homes,
    )


async def test_refresh_prunes_orphans_and_tracks_rename_and_shell_disable(tmp_path):
    root = tmp_path / "native"
    path = definition(root)
    homes = ShellHomeLifecycle(tmp_path / "homes")
    live = registry(root, homes)
    await live.refresh()
    assert not homes.root.exists()  # Discovery does not eagerly create homes.

    helper = homes.root / "helper"
    helper.mkdir(parents=True)
    (helper / "keep").write_text("agent state")
    stale = homes.root / "e2e-gated-old"
    stale.mkdir()
    (stale / ".gitconfig").write_text("generated")
    workspace = tmp_path / "workspaces" / "e2e-gated-old"
    workspace.mkdir(parents=True)
    (workspace / "work").write_text("persistent scratch")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("outside")
    (homes.root / "linked").symlink_to(outside, target_is_directory=True)
    (homes.root / "unmanaged-file").write_text("keep")

    await live.refresh()
    assert (helper / "keep").read_text() == "agent state"
    assert not stale.exists()
    assert (workspace / "work").read_text() == "persistent scratch"
    assert (outside / "keep").read_text() == "outside"
    assert (homes.root / "linked").is_symlink()
    assert (homes.root / "unmanaged-file").exists()

    path.write_text(path.read_text().replace("name: helper", "name: renamed"))
    await live.refresh()
    assert not helper.exists()
    renamed = homes.root / "renamed"
    renamed.mkdir()
    path.write_text(path.read_text().replace("shell: true", "shell: false"))
    await live.refresh()
    assert not renamed.exists()


def test_multiple_calls_and_recreated_agent_keep_home_until_last_user_exits(tmp_path):
    homes = ShellHomeLifecycle(tmp_path)
    home = tmp_path / "helper"
    home.mkdir()
    homes.reconcile(["helper"])
    with homes.use("helper"):
        with homes.use("helper"):
            homes.reconcile([])
            assert home.exists()
        assert home.exists()
        homes.reconcile(["helper"])
    assert home.exists()
    homes.reconcile([])
    assert not home.exists()


async def test_cancelled_async_shell_command_keeps_home_until_process_exits(tmp_path):
    homes = ShellHomeLifecycle(tmp_path / "homes")
    homes.reconcile(["helper"])
    workspace = tmp_path / "workspace"
    home = homes.root / "helper"
    backend = ProgenitorShellBackend(
        workspace=workspace,
        timeout=5,
        config=ObjectAgentShellConfig(
            max_output_bytes=100_000, env_passthrough=(), system_path="/usr/bin:/bin"
        ),
        shim_bin=tmp_path / "shims",
        agent_home=home,
        home_lifecycle=homes,
    )
    command = asyncio.create_task(
        backend.aexecute(
            "touch started; while [ ! -f release ]; do sleep 0.01; done; "
            'if [ -f "$HOME/.gitconfig" ]; then touch preserved; fi'
        )
    )
    try:
        async with asyncio.timeout(2):
            while not (workspace / "started").exists():
                await asyncio.sleep(0.01)
        command.cancel()
        with pytest.raises(asyncio.CancelledError):
            await command
        homes.reconcile([])
        assert home.exists()
    finally:
        (workspace / "release").touch()
        if not command.done():
            command.cancel()
        await asyncio.gather(command, return_exceptions=True)
    async with asyncio.timeout(2):
        while home.exists():
            await asyncio.sleep(0.01)
    assert (workspace / "preserved").exists()


async def test_failed_refresh_does_not_remove_previous_generations_home(tmp_path):
    path = definition(tmp_path / "native")
    homes = ShellHomeLifecycle(tmp_path / "homes")
    live = registry(tmp_path / "native", homes)
    await live.refresh()
    home = homes.root / "helper"
    home.mkdir(parents=True)
    path.unlink()
    definition(tmp_path / "native", "replacement")

    def fail(_source):
        raise RuntimeError("cannot build replacement")

    live._proxy_factory = fail
    with pytest.raises(RuntimeError, match="cannot build replacement"):
        await live.refresh()
    assert home.exists()
    assert "helper" in live.callables


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
async def test_proxy_retains_deleted_agents_home_until_call_finishes(
    tmp_path, monkeypatch, outcome
):
    root = tmp_path / "native"
    path = definition(root)
    homes = ShellHomeLifecycle(tmp_path / "homes")
    started, proceed = asyncio.Event(), asyncio.Event()
    home = homes.root / "helper"

    class Compiled:
        async def ainvoke(self, _state):
            assert (home / ".gitconfig").exists()
            started.set()
            await proceed.wait()
            if outcome == "failure":
                raise RuntimeError("agent failed")
            return {"messages": []}

    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.create_agent", lambda **_kwargs: Compiled()
    )
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware", lambda **_kwargs: object()
    )

    def proxy(source):
        return create_object_agent_proxy(
            source,
            state_manager=SimpleNamespace(),
            model="model",
            tools=[],
            backend=CompositeBackend(default=object(), routes={}),
            checkpointer=object(),
            worktree_dir=tmp_path / "repos",
            shell_paths=ShellPaths(
                tmp_path / "shims", homes.root, 120, tmp_path / "workspaces", homes
            ),
        )

    live = registry(root, homes, proxy)
    await live.refresh()
    assert not home.exists()
    active = asyncio.create_task(live.callables["helper"].ainvoke({"messages": []}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        path.unlink()
        await live.refresh()
        assert not live.callables
        assert home.exists()
        if outcome == "cancel":
            active.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active
        else:
            proceed.set()
            if outcome == "failure":
                with pytest.raises(RuntimeError, match="agent failed"):
                    await asyncio.wait_for(active, 2)
            else:
                await asyncio.wait_for(active, 2)
        assert not home.exists()
        assert (tmp_path / "workspaces" / "helper").is_dir()
    finally:
        if not active.done():
            active.cancel()
        await asyncio.gather(active, return_exceptions=True)
