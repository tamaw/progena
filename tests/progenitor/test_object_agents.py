from pathlib import Path

import pytest

from app.progenitor.agent.object_agents import (
    OBJECT_AGENT_SYSTEM_FILE,
    SelfManifestBackend,
    create_object_agent_proxy,
    discover_object_agents,
    parse_object_agent_manifest,
)

VALID_SYSTEM = """---
name: reviewer
description: Reviews requested work.
version: 1.0.0
kind: object
tools: [current_time]
skills: [/skills/review/]
permissions:
  - operations: [read]
    paths: [/repos/]
interrupt_on:
  current_time: true
unknown_for_future_schema: preserved_for_authoring
---
You are a concise reviewer.
"""


class _StateManager:
    def __init__(self, documents=None):
        self.documents = documents or {}
        self.touched = []

    async def list_content_documents(self, kind):
        assert kind == "agent"
        return list(self.documents.items())

    async def get_content_document(self, kind, relative_path):
        assert kind == "agent"
        return self.documents.get(relative_path)

    async def save_content_document(self, kind, relative_path, content):
        assert kind == "agent"
        self.documents[relative_path] = content

    async def touch_object_agent(self, name):
        self.touched.append(name)


def test_manifest_filters_unknown_keys_and_parses_supported_contract():
    manifest = parse_object_agent_manifest(VALID_SYSTEM, source="test")

    assert manifest.name == "reviewer"
    assert manifest.tools == ("current_time",)
    assert manifest.skills == ("/skills/review/",)
    assert manifest.permissions[0].operations == ["read"]
    assert manifest.interrupt_on == {"current_time": True}
    assert manifest.system_prompt == "You are a concise reviewer."


@pytest.mark.parametrize(
    "replacement, expected",
    [
        ("kind: object", "kind: graph"),
        ("tools: [current_time]", "tools: current_time"),
        ("skills: [/skills/review/]", "skills: [/repos/review/]"),
    ],
)
def test_manifest_rejects_invalid_supported_fields(replacement, expected):
    with pytest.raises(ValueError):
        parse_object_agent_manifest(VALID_SYSTEM.replace(replacement, expected), source="test")


@pytest.mark.asyncio
async def test_discovery_prefers_native_name_over_installed_document(tmp_path: Path):
    native = tmp_path / "native" / "reviewer"
    native.mkdir(parents=True)
    (native / OBJECT_AGENT_SYSTEM_FILE).write_text(VALID_SYSTEM, encoding="utf-8")
    installed = VALID_SYSTEM.replace("You are a concise reviewer.", "Installed prompt.")
    sources = await discover_object_agents(
        _StateManager({"marketplace__demo/reviewer/SYSTEM.md": installed}),
        native_root=tmp_path / "native",
    )

    assert [(source.name, source.source, source.manifest.system_prompt) for source in sources] == [
        ("reviewer", "native", "You are a concise reviewer."),
    ]


@pytest.mark.asyncio
async def test_lazy_proxy_resolves_only_declared_parent_tools(monkeypatch, tmp_path: Path):
    agent_dir = tmp_path / "reviewer"
    agent_dir.mkdir()
    (agent_dir / "SYSTEM.md").write_text(VALID_SYSTEM, encoding="utf-8")
    source = (await discover_object_agents(_StateManager(), native_root=tmp_path))[0]
    calls = []

    class _Tool:
        def __init__(self, name):
            self.name = name

    class _Compiled:
        async def ainvoke(self, state):
            calls.append(state)
            return {"messages": []}

    def _create(**kwargs):
        calls.append(kwargs)
        return _Compiled()

    monkeypatch.setattr("app.progenitor.agent.object_agents.create_agent", _create)
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware",
        lambda **_kwargs: object(),
    )
    proxy = create_object_agent_proxy(
        source,
        state_manager=_StateManager(),
        model="parent-model",
        tools=[_Tool("current_time"), _Tool("unrequested")],
        backend=object(),
        checkpointer=object(),
        agent_specs=lambda _caller: [],
    )
    await proxy["runnable"].ainvoke({"messages": []})

    create_kwargs = calls[0]
    assert create_kwargs["model"] == "parent-model"
    assert [tool.name for tool in create_kwargs["tools"]] == ["current_time"]


@pytest.mark.asyncio
async def test_self_manifest_backend_reads_and_writes_native_source(tmp_path: Path):
    agent_dir = tmp_path / "reviewer"
    agent_dir.mkdir()
    system_path = agent_dir / OBJECT_AGENT_SYSTEM_FILE
    system_path.write_text(VALID_SYSTEM, encoding="utf-8")
    source = (await discover_object_agents(_StateManager(), native_root=tmp_path))[0]
    sm = _StateManager()
    mount = SelfManifestBackend(source, state_manager=sm)

    read = await mount.aread("/SYSTEM.md")
    assert read.error is None
    assert read.file_data["content"] == VALID_SYSTEM

    updated = VALID_SYSTEM.replace(
        "description: Reviews requested work.", "description: Reviews requested work, twice."
    )
    write = await mount.awrite("/SYSTEM.md", updated)
    assert write.error is None
    assert system_path.read_text(encoding="utf-8") == updated
    # Native writes never touch the store table on their own -- the
    # backend's cross-process NOTIFY watcher needs this marker to know
    # anything changed.
    assert sm.touched == ["reviewer"]


@pytest.mark.asyncio
async def test_self_manifest_backend_writes_installed_document_via_state_manager():
    relative_path = "marketplace__demo/reviewer/SYSTEM.md"
    sm = _StateManager({relative_path: VALID_SYSTEM})
    source = (await discover_object_agents(sm, native_root=Path("/nonexistent-native-root")))[0]
    mount = SelfManifestBackend(source, state_manager=sm)

    updated = VALID_SYSTEM.replace("version: 1.0.0", "version: 1.1.0")
    write = await mount.awrite("/SYSTEM.md", updated)
    assert write.error is None
    assert sm.documents[relative_path] == updated
    assert sm.touched == ["reviewer"]

    read = await mount.aread("/SYSTEM.md")
    assert read.file_data["content"] == updated


@pytest.mark.asyncio
async def test_self_manifest_backend_rejects_rename_and_invalid_manifest(tmp_path: Path):
    agent_dir = tmp_path / "reviewer"
    agent_dir.mkdir()
    (agent_dir / OBJECT_AGENT_SYSTEM_FILE).write_text(VALID_SYSTEM, encoding="utf-8")
    source = (await discover_object_agents(_StateManager(), native_root=tmp_path))[0]
    mount = SelfManifestBackend(source, state_manager=_StateManager())

    renamed = VALID_SYSTEM.replace("name: reviewer", "name: renamed-reviewer")
    write = await mount.awrite("/SYSTEM.md", renamed)
    assert write.error is not None
    assert "renaming" in write.error

    invalid = VALID_SYSTEM.replace("kind: object", "kind: graph")
    write = await mount.awrite("/SYSTEM.md", invalid)
    assert write.error is not None

    # Neither rejected write touched the file on disk.
    assert (agent_dir / OBJECT_AGENT_SYSTEM_FILE).read_text(encoding="utf-8") == VALID_SYSTEM


@pytest.mark.asyncio
async def test_self_manifest_backend_edit_round_trips_via_string_replacement(tmp_path: Path):
    agent_dir = tmp_path / "reviewer"
    agent_dir.mkdir()
    system_path = agent_dir / OBJECT_AGENT_SYSTEM_FILE
    system_path.write_text(VALID_SYSTEM, encoding="utf-8")
    source = (await discover_object_agents(_StateManager(), native_root=tmp_path))[0]
    mount = SelfManifestBackend(source, state_manager=_StateManager())

    edit = await mount.aedit(
        "/SYSTEM.md", "You are a concise reviewer.", "You are a concise, thorough reviewer."
    )
    assert edit.error is None
    assert "thorough" in system_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_lazy_proxy_uses_only_declared_mounts_and_prompt(
    monkeypatch, tmp_path: Path
):
    agent_dir = tmp_path / "reviewer"
    agent_dir.mkdir()
    (agent_dir / "SYSTEM.md").write_text(VALID_SYSTEM, encoding="utf-8")
    source = (await discover_object_agents(_StateManager(), native_root=tmp_path))[0]
    calls = []

    class _NamedTool:
        def __init__(self, name):
            self.name = name

    class _Compiled:
        async def ainvoke(self, state):
            return {"messages": []}

    def _create(**kwargs):
        calls.append(kwargs)
        return _Compiled()

    monkeypatch.setattr("app.progenitor.agent.object_agents.create_agent", _create)
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware",
        lambda **_kwargs: object(),
    )
    proxy = create_object_agent_proxy(
        source,
        state_manager=_StateManager(),
        model="parent-model",
        tools=[_NamedTool("current_time")],
        backend=object(),
        checkpointer=object(),
        agent_specs=lambda _caller: [],
    )
    await proxy["runnable"].ainvoke({"messages": []})

    create_kwargs = calls[0]
    assert create_kwargs["system_prompt"] == "You are a concise reviewer."
    assert not any(
        middleware.__class__.__name__ == "FilesystemMiddleware"
        for middleware in create_kwargs["middleware"]
    )
