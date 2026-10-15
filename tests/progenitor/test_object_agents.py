from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import ReadResult
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.skills import SkillsMiddleware, _alist_skills
from langchain.agents.middleware import HumanInTheLoopMiddleware

from app.progenitor.agent.agent import _resolve_runtime_memory_paths
from app.progenitor.agent.backends import ReadOnlyBackend
from app.progenitor.agent.object_agent_manifest import (
    OBJECT_AGENT_SYSTEM_FILE,
    discover_native_object_agents,
    discover_object_agents,
    parse_front_matter,
    parse_object_agent_manifest,
)
from app.progenitor.agent.object_agents import (
    ScopedMountBackend,
    SelfManifestBackend,
    build_object_agent,
    create_object_agent_proxy,
    object_agent_memory_sources,
)
from app.progenitor.agent.state_backend import ContentDocumentDirectoryBackend

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

PLUGIN_SYSTEM = """---
name: plugin-reviewer
description: Uses installed review skills.
version: 1.0.0
kind: object
skills: [/skills/acme__new/lint/]
mounts:
  - path: /skills/acme__new/lint/
    access: read
permissions:
  - operations: [read]
    paths: [/skills/acme__new/lint/]
---
Review with the lint skill.
"""


class _StateManager:
    def __init__(self, documents=None):
        self.documents = documents or {}
        self.touched = []
        self.markers = {}
        self._runtime_store = SimpleNamespace(
            peek_document=AsyncMock(side_effect=lambda key: self.markers.get(key)),
            save_document=AsyncMock(
                side_effect=lambda key, value: self.markers.__setitem__(key, value)
            ),
        )

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


@pytest.mark.parametrize("broad_mount", [False, True])
async def test_plugin_installed_after_startup_reads_skills_and_companions(
    monkeypatch, tmp_path, broad_mount
):
    documents = {"agent": {}, "skill": {}}
    sm = SimpleNamespace(
        get_content_document=AsyncMock(side_effect=lambda kind, path: documents[kind].get(path)),
        list_content_documents=AsyncMock(side_effect=lambda kind: list(documents[kind].items())),
    )
    # Parent is built before installation, and has only native skill routing.
    parent = CompositeBackend(
        default=object(),
        routes={
            "/skills/": ReadOnlyBackend(
                FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
            )
        },
    )
    original_routes = dict(parent.routes)
    compiled_backends = []

    def create(**kwargs):
        filesystem = next(m for m in kwargs["middleware"] if isinstance(m, FilesystemMiddleware))
        assert any(isinstance(m, SkillsMiddleware) for m in kwargs["middleware"])
        assert filesystem._permissions == list(source.manifest.permissions)
        compiled_backends.append(filesystem.backend)
        return SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": []}))

    monkeypatch.setattr("app.progenitor.agent.object_agents.create_agent", create)
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware", lambda **_kw: object()
    )
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.StateBackend",
        lambda: SimpleNamespace(aread=AsyncMock(return_value=ReadResult(error="File not found"))),
    )
    content = PLUGIN_SYSTEM
    if broad_mount:
        content = content.replace("  - path: /skills/acme__new/lint/", "  - path: /skills/")
    documents["agent"]["acme__new/plugin-reviewer/SYSTEM.md"] = content
    documents["skill"].update(
        {
            "acme__new/lint/SKILL.md": "---\nname: lint\ndescription: Lint changes.\n---\nLint skill.",
            "acme__new/lint/resources/examples.md": "Lint examples.",
            "acme__new/sibling/SKILL.md": "Private sibling.",
            "other__plugin/lint/SKILL.md": "Other plugin.",
        }
    )
    source = (await discover_object_agents(sm))[0]
    proxy = create_object_agent_proxy(
        source, state_manager=sm, model=object(), tools=[], backend=parent, checkpointer=None
    )
    assert (await parent.aread("/skills/acme__new/lint/SKILL.md")).error is not None
    await proxy["runnable"].ainvoke({"messages": []})
    child = compiled_backends[-1]
    assert set(child.routes) == (
        {"/skills/", "/skills/acme__new/lint/"} if broad_mount else {"/skills/acme__new/lint/"}
    )
    assert (
        (await child.aread("/skills/acme__new/lint/SKILL.md"))
        .file_data["content"]
        .endswith("Lint skill.")
    )
    assert (await child.aread("/skills/acme__new/lint/resources/examples.md")).file_data[
        "content"
    ] == "Lint examples."
    assert (await child.aread("/skills/acme__new/sibling/SKILL.md")).error is not None
    assert (await child.aread("/skills/other__plugin/lint/SKILL.md")).error is not None
    assert parent.routes == original_routes
    assert (await parent.aread("/skills/acme__new/lint/SKILL.md")).error is not None
    documents["skill"]["acme__new/lint/resources/examples.md"] = "Updated examples."
    await proxy["runnable"].ainvoke({"messages": []})
    assert compiled_backends[-1] is not child
    assert (
        await compiled_backends[-1].aread("/skills/acme__new/lint/resources/examples.md")
    ).file_data["content"] == "Updated examples."


def test_listed_plugin_skill_is_mounted_without_declaring_it(monkeypatch):
    manifest = parse_object_agent_manifest(
        PLUGIN_SYSTEM.replace(
            "skills: [/skills/acme__new/lint/]",
            "skills: [/skills/acme__new/lint/, /skills/other__plugin/lint]",
        ),
        source="test",
    )
    calls = []
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.create_agent", lambda **kw: calls.append(kw)
    )
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware", lambda **_kw: object()
    )
    build_object_agent(
        manifest,
        model=object(),
        tools=[],
        backend=object(),
        checkpointer=None,
        state_manager=_StateManager(),
    )
    filesystem = next(m for m in calls[0]["middleware"] if isinstance(m, FilesystemMiddleware))
    # Exactly the listed skill folders; never their parent packs.
    assert set(filesystem.backend.routes) == {
        "/skills/acme__new/lint/",
        "/skills/other__plugin/lint/",
    }


def test_listed_search_without_knowledge_grants_is_not_given_the_unscoped_tool(monkeypatch):
    # The parent's `search` reads every base; an agent without grants must not get it.
    manifest = parse_object_agent_manifest(
        VALID_SYSTEM.replace("tools: [current_time]", "tools: [current_time, search]").replace(
            "skills: [/skills/review/]", "skills: []"
        ),
        source="test",
    )
    calls = []
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.create_agent", lambda **kw: calls.append(kw)
    )
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware", lambda **_kw: object()
    )
    build_object_agent(
        manifest,
        model=object(),
        tools=[SimpleNamespace(name="current_time"), SimpleNamespace(name="search")],
        backend=object(),
        checkpointer=None,
        state_manager=_StateManager(),
    )
    assert [tool.name for tool in calls[0]["tools"]] == ["current_time"]


def test_manifest_rejects_all_skills_wildcard():
    with pytest.raises(ValueError, match="use mounts for access to all skills"):
        parse_object_agent_manifest(
            VALID_SYSTEM.replace("skills: [/skills/review/]", "skills: [/skills/]"),
            source="test",
        )


async def test_only_listed_skills_load_into_the_prompt(monkeypatch, tmp_path: Path, caplog):
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.StateBackend",
        lambda: SimpleNamespace(aread=AsyncMock(return_value=ReadResult(error="File not found"))),
    )
    for name in ("managing-repos", "sibling"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} skill.\n---\nbody\n"
        )
    parent = CompositeBackend(
        default=object(),
        routes={
            "/skills/": ReadOnlyBackend(FilesystemBackend(root_dir=tmp_path, virtual_mode=True))
        },
    )
    manifest = parse_object_agent_manifest(
        VALID_SYSTEM.replace(
            "skills: [/skills/review/]", "skills: [/skills/managing-repos, /skills/typo]"
        ),
        source="test",
    )
    calls = []
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.create_agent", lambda **kw: calls.append(kw)
    )
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware", lambda **_kw: object()
    )
    build_object_agent(
        manifest,
        model=object(),
        tools=[SimpleNamespace(name="current_time")],
        backend=parent,
        checkpointer=None,
    )

    skills = next(m for m in calls[0]["middleware"] if isinstance(m, SkillsMiddleware))
    with caplog.at_level("WARNING", logger="app.progenitor.agent.object_agents"):
        update = await skills.abefore_agent({}, None, {})
    assert [skill["name"] for skill in update["skills_metadata"]] == ["managing-repos"]
    # A listed skill without SKILL.md is reported instead of silently skipped.
    assert any("/skills/typo" in r.getMessage() for r in caplog.records)
    assert not any("managing-repos" in r.getMessage() for r in caplog.records)
    filesystem = next(m for m in calls[0]["middleware"] if isinstance(m, FilesystemMiddleware))
    assert not (await filesystem.backend.aread("/skills/managing-repos/SKILL.md")).error
    assert (await filesystem.backend.aread("/skills/sibling/SKILL.md")).error


def test_manifest_filters_unknown_keys_and_parses_supported_contract():
    manifest = parse_object_agent_manifest(VALID_SYSTEM, source="test")

    assert manifest.name == "reviewer"
    assert manifest.tools == ("current_time",)
    assert manifest.skills == ("/skills/review/",)
    assert manifest.permissions[0].operations == ["read"]
    assert manifest.interrupt_on == {"current_time": True}
    assert manifest.system_prompt == "You are a concise reviewer."


def test_user_manifest_rejects_shell():
    content = VALID_SYSTEM.replace(
        "tools: [current_time]", "tools: []\nshell: true\nenv_passthrough: [GH_TOKEN]"
    )
    with pytest.raises(
        ValueError, match="shell and env_passthrough are only allowed for native agents"
    ):
        parse_object_agent_manifest(content, source="test", source_kind="user")


def test_marketplace_manifest_rejects_shell():
    content = VALID_SYSTEM.replace(
        "tools: [current_time]", "tools: []\nshell: true\nenv_passthrough: [GH_TOKEN]"
    )
    with pytest.raises(
        ValueError, match="shell and env_passthrough are only allowed for native agents"
    ):
        parse_object_agent_manifest(content, source="test", source_kind="marketplace")


def test_native_manifest_allows_shell():
    content = VALID_SYSTEM.replace(
        "tools: [current_time]", "tools: []\nshell: true\nenv_passthrough: [GH_TOKEN]"
    )
    manifest = parse_object_agent_manifest(content, source="test", source_kind="native")
    assert manifest.shell is not None
    assert manifest.shell.env_passthrough == ("GH_TOKEN",)


_CONFIG_MOUNT = "permissions: []\nmounts:\n  - path: {path}\n    access: {access}\n"


@pytest.mark.parametrize("source_kind", ["user", "marketplace"])
@pytest.mark.parametrize("path", ["/config/", "/config/mcp.json"])
def test_non_native_manifest_rejects_writable_config_mount(source_kind, path):
    content = VALID_SYSTEM.replace(
        "permissions:\n  - operations: [read]\n    paths: [/repos/]\n",
        _CONFIG_MOUNT.format(path=path, access="read-write"),
    )
    with pytest.raises(ValueError, match="read-write /config/ mounts are only allowed"):
        parse_object_agent_manifest(content, source="test", source_kind=source_kind)


@pytest.mark.parametrize("source_kind, access", [("user", "read"), ("native", "read-write")])
def test_config_mount_allowed_read_only_or_for_native(source_kind, access):
    content = VALID_SYSTEM.replace(
        "permissions:\n  - operations: [read]\n    paths: [/repos/]\n",
        _CONFIG_MOUNT.format(path="/config/", access=access),
    )
    manifest = parse_object_agent_manifest(content, source="test", source_kind=source_kind)
    assert manifest.mounts[0].access == access


def test_raw_front_matter_preserves_authoring_fields():
    raw = parse_front_matter(VALID_SYSTEM, source="test")
    assert raw["unknown_for_future_schema"] == "preserved_for_authoring"


@pytest.mark.asyncio
async def test_lazy_proxy_reloads_and_builds_every_invocation(monkeypatch, tmp_path):
    relative_path = "user/reviewer/SYSTEM.md"
    sm = _StateManager({relative_path: VALID_SYSTEM})
    source = (await discover_object_agents(sm))[0]
    builds = []
    invoked = []

    def create(**kwargs):
        builds.append(kwargs)
        agent = SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": []}))
        invoked.append(agent)
        return agent

    monkeypatch.setattr("app.progenitor.agent.object_agents.create_agent", create)
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware", lambda **_kwargs: object()
    )
    override_model = object()
    model_configs = []

    def resolve_model(config):
        model_configs.append(config)
        return override_model

    monkeypatch.setattr("app.progenitor.agent.object_agents.create_langchain_model", resolve_model)
    tools = [SimpleNamespace(name="current_time"), SimpleNamespace(name="other")]
    parent_model, checkpointer = object(), object()
    proxy = create_object_agent_proxy(
        source,
        state_manager=sm,
        model=parent_model,
        tools=tools,
        backend=CompositeBackend(
            default=object(), routes={"/repos/": object(), "/skills/": object()}
        ),
        checkpointer=checkpointer,
    )
    assert builds == []
    await proxy["runnable"].ainvoke({"messages": ["first"], "ignored": "value"})

    updated = VALID_SYSTEM.replace("tools: [current_time]", "tools: [other]\nmodel: deployment-two")
    updated = updated.replace("skills: [/skills/review/]", "skills: []")
    updated = updated.replace("paths: [/repos/]", "paths: [/repos/project/]")
    updated = updated.replace(
        "interrupt_on:\n  current_time: true",
        "mounts:\n  - path: /repos/project/\n    access: read",
    ).replace("You are a concise reviewer.", "Updated prompt.")
    sm.documents[relative_path] = updated
    await proxy["runnable"].ainvoke({"messages": ["second"]})

    assert len(builds) == 2
    assert builds[0]["system_prompt"] == "You are a concise reviewer."
    assert builds[1]["system_prompt"] == "Updated prompt."
    assert builds[0]["tools"] == [tools[0]]
    assert builds[1]["tools"] == [tools[1]]
    assert builds[0]["model"] is parent_model
    assert builds[1]["model"] is override_model
    assert model_configs[0].model_default == "deployment-two"
    assert all(build["checkpointer"] is checkpointer for build in builds)
    assert any(isinstance(m, HumanInTheLoopMiddleware) for m in builds[0]["middleware"])
    assert not any(isinstance(m, HumanInTheLoopMiddleware) for m in builds[1]["middleware"])
    filesystem = next(m for m in builds[1]["middleware"] if isinstance(m, FilesystemMiddleware))
    assert set(filesystem.backend.routes) == {"/repos/project/"}
    invoked[0].ainvoke.assert_awaited_once_with({"messages": ["first"]})
    invoked[1].ainvoke.assert_awaited_once_with({"messages": ["second"]})
    del sm.documents[relative_path]
    with pytest.raises(ValueError, match="no longer installed"):
        await proxy["runnable"].ainvoke({"messages": ["after deletion"]})
    assert len(builds) == 2


@pytest.mark.asyncio
async def test_builder_preserves_scoped_mounts_and_middleware(monkeypatch):
    content = VALID_SYSTEM.replace(
        "paths: [/repos/]",
        "paths: [/repos/project/]\nmounts:\n  - path: /repos/project/\n    access: read"
        "\n  - path: /skills/review/\n    access: read",
    )
    manifest = parse_object_agent_manifest(content, source="test")
    repo_backend = SimpleNamespace(aread=AsyncMock(return_value="file"), awrite=AsyncMock())
    parent = CompositeBackend(
        default=object(),
        routes={"/repos/": repo_backend, "/skills/": object(), "/config/": object()},
    )
    calls = []
    compiled = object()

    def create(**kwargs):
        calls.append(kwargs)
        return compiled

    monkeypatch.setattr("app.progenitor.agent.object_agents.create_agent", create)
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware", lambda **_kwargs: object()
    )
    tool, model, checkpointer = SimpleNamespace(name="current_time"), object(), object()
    assert (
        build_object_agent(
            manifest, model=model, tools=[tool], backend=parent, checkpointer=checkpointer
        )
        is compiled
    )
    assert calls[0]["model"] is model
    assert calls[0]["checkpointer"] is checkpointer
    filesystem = next(m for m in calls[0]["middleware"] if isinstance(m, FilesystemMiddleware))
    assert set(filesystem.backend.routes) == {"/repos/project/", "/skills/review/"}
    route = filesystem.backend.routes["/repos/project/"]
    assert await route.aread("/README.md") == "file"
    repo_backend.aread.assert_awaited_once_with("/project/README.md", 0, 2000)
    assert not await route.can_write_file("/README.md", "changed", {})
    assert not await route.can_delete_file("/README.md", {})
    repo_backend.awrite.assert_not_awaited()
    assert any(isinstance(m, SkillsMiddleware) for m in calls[0]["middleware"])
    assert any(isinstance(m, HumanInTheLoopMiddleware) for m in calls[0]["middleware"])


@pytest.mark.parametrize(
    "change, match",
    [
        ({"tools": []}, "unknown tools"),
        ({"backend": object()}, "not a known parent route"),
    ],
)
def test_builder_rejects_unknown_tools_and_mounts(change, match):
    content = VALID_SYSTEM.replace(
        "skills: [/skills/review/]", "skills: []\nmounts:\n  - path: /repos/\n    access: read"
    )
    manifest = parse_object_agent_manifest(content, source="test")
    kwargs = {
        "model": object(),
        "tools": [SimpleNamespace(name="current_time")],
        "backend": CompositeBackend(default=object(), routes={"/repos/": object()}),
        "checkpointer": None,
    }
    kwargs.update(change)
    with pytest.raises(ValueError, match=match):
        build_object_agent(manifest, **kwargs)


class _MemoryStore:
    def __init__(self):
        self.docs: dict[str, str] = {}

    async def get_content_document(self, kind, relative_path):
        assert kind == "memory"
        return self.docs.get(relative_path)

    async def save_content_document(self, kind, relative_path, content):
        assert kind == "memory"
        self.docs[relative_path] = content

    async def list_content_documents(self, kind):
        assert kind == "memory"
        return list(self.docs.items())


@pytest.mark.parametrize(
    "value, expected", [("", False), ("memory: true\n", True), ("memory: false\n", False)]
)
def test_manifest_parses_memory_flag(value, expected):
    content = VALID_SYSTEM.replace("kind: object\n", f"kind: object\n{value}")
    assert parse_object_agent_manifest(content, source="test").memory is expected


def test_manifest_rejects_non_boolean_memory():
    with pytest.raises(ValueError, match="'memory' must be a boolean"):
        parse_object_agent_manifest(
            VALID_SYSTEM.replace("kind: object\n", "kind: object\nmemory: yes please\n"),
            source="test",
        )


def test_manifest_rejects_shared_memories_mount_with_private_memory():
    content = VALID_SYSTEM.replace(
        "kind: object\n",
        "kind: object\nmemory: true\nmounts:\n  - path: /memories/\n    access: read\n",
    ).replace("paths: [/repos/]", "paths: [/memories/]")
    with pytest.raises(ValueError, match="memory: true uses /memories/ for the agent's private"):
        parse_object_agent_manifest(content, source="test")


async def test_memory_agent_sees_only_its_own_memory_at_memories(monkeypatch):
    manifest = parse_object_agent_manifest(
        VALID_SYSTEM.replace("name: reviewer", "name: Code Reviewer\nmemory: true").replace(
            "skills: [/skills/review/]", "skills: []"
        ),
        source="test",
    )
    store = _MemoryStore()
    store.docs.update(
        {
            "AGENTS.md": "progenitor shared memory",
            "agents/other-agent/AGENTS.md": "someone else",
            "agents/code-reviewer/zeta.txt": "a stray note",
            "agents/code-reviewer/AGENTS.md": "main notes",
        }
    )
    parent = CompositeBackend(
        default=object(), routes={"/memories/": ContentDocumentDirectoryBackend(store, "memory")}
    )
    calls = []
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.create_agent", lambda **kw: calls.append(kw)
    )
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware", lambda **_kwargs: object()
    )

    sources = await object_agent_memory_sources(store, manifest.name)
    # Every private file loads, AGENTS.md first, whatever the agent named them.
    assert sources == ["/memories/AGENTS.md", "/memories/zeta.txt"]
    build_object_agent(
        manifest,
        model=object(),
        tools=[SimpleNamespace(name="current_time")],
        backend=parent,
        checkpointer=None,
        memory_sources=sources,
    )

    middleware = calls[0]["middleware"]
    memory = next(m for m in middleware if isinstance(m, MemoryMiddleware))
    backend = next(m for m in middleware if isinstance(m, FilesystemMiddleware)).backend
    loaded = await backend.adownload_files(memory.sources)
    assert [r.content for r in loaded] == [b"main notes", b"a stray note"]
    # Writes land in the agent's private folder; shared and other agents' memory
    # are unreachable from its /memories/.
    await backend.awrite("/memories/new.md", "fresh")
    assert store.docs["agents/code-reviewer/new.md"] == "fresh"
    await backend.aedit("/memories/AGENTS.md", "main notes", "edited notes")
    assert store.docs["agents/code-reviewer/AGENTS.md"] == "edited notes"
    assert store.docs["AGENTS.md"] == "progenitor shared memory"
    assert (await backend.aread("/memories/other-agent/AGENTS.md")).error


async def test_scoped_mount_lists_paths_the_child_can_read(tmp_path: Path):
    (tmp_path / "pack" / "lint").mkdir(parents=True)
    (tmp_path / "pack" / "lint" / "SKILL.md").write_text(
        "---\nname: lint\ndescription: Lint things.\n---\nbody\n"
    )
    # Same-named folder outside the mount: must never be what gets loaded.
    (tmp_path / "lint").mkdir()
    (tmp_path / "lint" / "SKILL.md").write_text("---\nname: outside\ndescription: no\n---\n")
    scoped = ScopedMountBackend(FilesystemBackend(root_dir=tmp_path, virtual_mode=True), "/pack")
    composite = CompositeBackend(default=object(), routes={"/skills/pack/": scoped})

    listed = (await composite.als("/skills/pack/")).entries
    assert [entry["path"] for entry in listed] == ["/skills/pack/lint/"]
    [match] = (await composite.aglob("**/SKILL.md", "/skills/pack/")).matches
    assert not (await composite.aread(match["path"])).error
    assert [skill["name"] for skill in await _alist_skills(composite, "/skills/pack/")] == ["lint"]
    # Sync callers get the same scoping; the outside folder is never reached.
    assert [entry["path"] for entry in composite.ls("/skills/pack/").entries] == [
        "/skills/pack/lint/"
    ]
    [downloaded] = composite.download_files(["/skills/pack/lint/SKILL.md"])
    assert b"name: lint" in (downloaded.content or b"")
    assert "name: lint" in composite.read("/skills/pack/lint/SKILL.md").file_data["content"]


async def test_main_agent_memory_excludes_object_agent_memories():
    store = SimpleNamespace(
        list_content_documents=AsyncMock(
            return_value=[
                ("AGENTS.md", ""),
                ("notes.md", ""),
                ("agents/repo-manager/AGENTS.md", ""),
            ]
        )
    )
    assert await _resolve_runtime_memory_paths(store, None) == [
        "/memories/AGENTS.md",
        "/memories/notes.md",
    ]


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
        _StateManager(
            {
                "marketplace__demo/reviewer/SYSTEM.md": installed,
                "native/reviewer/SYSTEM.md": "Ignored old seed",
            }
        ),
        tmp_path / "native",
    )

    assert [(source.name, source.source, source.manifest.system_prompt) for source in sources] == [
        ("reviewer", "native", "You are a concise reviewer."),
    ]


async def test_native_files_are_not_imported_and_ignore_old_seed_marker(tmp_path):
    system_path = tmp_path / "reviewer" / "SYSTEM.md"
    system_path.parent.mkdir()
    system_path.write_text(VALID_SYSTEM, encoding="utf-8")
    sm = _StateManager(
        {"user/other/SYSTEM.md": VALID_SYSTEM.replace("name: reviewer", "name: other")}
    )
    sm.markers["native_object_agents_seed_v1"] = {"complete": True}
    before = dict(sm.documents)
    sources = await discover_object_agents(sm, tmp_path)
    assert [source.name for source in sources] == ["other", "reviewer"]
    assert sm.documents == before
    assert sm._runtime_store.save_document.await_count == 0


async def test_native_disk_wins_over_stale_pg_copy(tmp_path):
    system_path = tmp_path / "reviewer" / "SYSTEM.md"
    system_path.parent.mkdir()
    system_path.write_text(VALID_SYSTEM, encoding="utf-8")
    edited = VALID_SYSTEM.replace("concise reviewer", "persisted reviewer")
    sm = _StateManager({"native/reviewer/SYSTEM.md": edited})
    sources = await discover_object_agents(sm, tmp_path)
    assert sources[0].manifest.system_prompt == "You are a concise reviewer."
    assert sources[0].file_path == system_path


async def test_native_discovery_skips_invalid_files(tmp_path):
    path = tmp_path / "broken" / "SYSTEM.md"
    path.parent.mkdir()
    path.write_text("invalid")
    assert discover_native_object_agents(tmp_path) == []


async def test_discovery_never_reads_disk(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: pytest.fail("disk authority"))
    assert await discover_object_agents(_StateManager()) == []
    sm = _StateManager({"user/reviewer/SYSTEM.md": VALID_SYSTEM})
    assert (await discover_object_agents(sm))[0].source == "user"


def test_docker_native_file_contract():
    root = Path(__file__).resolve().parents[2]
    assert "COPY data/agents/ ./data/agents/" in (root / "docker/progenitor/Dockerfile").read_text()
    assert (
        "COPY data/agents/objects/ ./data/agents/objects/"
        in (root / "docker/backend/Dockerfile").read_text()
    )


@pytest.mark.asyncio
async def test_lazy_proxy_resolves_only_declared_parent_tools(monkeypatch, tmp_path: Path):
    agent_dir = tmp_path / "reviewer"
    agent_dir.mkdir()
    (agent_dir / "SYSTEM.md").write_text(VALID_SYSTEM, encoding="utf-8")
    sm = _StateManager({"user/reviewer/SYSTEM.md": VALID_SYSTEM})
    source = (await discover_object_agents(sm))[0]
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
        state_manager=sm,
        model="parent-model",
        tools=[_Tool("current_time"), _Tool("unrequested")],
        backend=CompositeBackend(default=object(), routes={"/skills/": object()}),
        checkpointer=object(),
    )
    await proxy["runnable"].ainvoke({"messages": []})

    create_kwargs = calls[0]
    assert create_kwargs["model"] == "parent-model"
    assert [tool.name for tool in create_kwargs["tools"]] == ["current_time"]


@pytest.mark.asyncio
async def test_self_manifest_backend_native_source_is_read_only(tmp_path: Path):
    agent_dir = tmp_path / "reviewer"
    agent_dir.mkdir()
    system_path = agent_dir / OBJECT_AGENT_SYSTEM_FILE
    system_path.write_text(VALID_SYSTEM, encoding="utf-8")
    sm = _StateManager({"user/reviewer/SYSTEM.md": VALID_SYSTEM})
    source = discover_native_object_agents(tmp_path)[0]
    mount = SelfManifestBackend(source, state_manager=sm)

    read = await mount.aread("/SYSTEM.md")
    assert read.error is None
    assert read.file_data["content"] == VALID_SYSTEM

    updated = VALID_SYSTEM.replace(
        "description: Reviews requested work.", "description: Reviews requested work, twice."
    )
    write = await mount.awrite("/SYSTEM.md", updated)
    assert write.error is not None
    assert "read-only" in write.error
    assert (await mount.adelete("/SYSTEM.md")).error is not None
    assert (await mount.aedit("/SYSTEM.md", "concise", "edited")).error is not None
    assert system_path.read_text(encoding="utf-8") == VALID_SYSTEM
    assert sm.touched == []


@pytest.mark.asyncio
async def test_self_manifest_backend_writes_installed_document_via_state_manager():
    relative_path = "marketplace__demo/reviewer/SYSTEM.md"
    sm = _StateManager({relative_path: VALID_SYSTEM})
    source = (await discover_object_agents(sm))[0]
    mount = SelfManifestBackend(source, state_manager=sm)

    updated = VALID_SYSTEM.replace("version: 1.0.0", "version: 1.1.0")
    write = await mount.awrite("/SYSTEM.md", updated)
    assert write.error is None
    assert sm.documents[relative_path] == updated
    assert sm.touched == []

    read = await mount.aread("/SYSTEM.md")
    assert read.file_data["content"] == updated


@pytest.mark.asyncio
async def test_self_manifest_backend_rejects_rename_and_invalid_manifest(tmp_path: Path):
    agent_dir = tmp_path / "reviewer"
    agent_dir.mkdir()
    (agent_dir / OBJECT_AGENT_SYSTEM_FILE).write_text(VALID_SYSTEM, encoding="utf-8")
    sm = _StateManager({"user/reviewer/SYSTEM.md": VALID_SYSTEM})
    source = (await discover_object_agents(sm))[0]
    mount = SelfManifestBackend(source, state_manager=sm)

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
    sm = _StateManager({"user/reviewer/SYSTEM.md": VALID_SYSTEM})
    source = (await discover_object_agents(sm))[0]
    mount = SelfManifestBackend(source, state_manager=sm)

    edit = await mount.aedit(
        "/SYSTEM.md", "You are a concise reviewer.", "You are a concise, thorough reviewer."
    )
    assert edit.error is None
    assert "thorough" in sm.documents["user/reviewer/SYSTEM.md"]
    assert system_path.read_text(encoding="utf-8") == VALID_SYSTEM


@pytest.mark.asyncio
async def test_lazy_proxy_uses_only_declared_mounts_and_prompt(monkeypatch, tmp_path: Path):
    agent_dir = tmp_path / "reviewer"
    agent_dir.mkdir()
    (agent_dir / "SYSTEM.md").write_text(VALID_SYSTEM, encoding="utf-8")
    sm = _StateManager({"user/reviewer/SYSTEM.md": VALID_SYSTEM})
    source = (await discover_object_agents(sm))[0]
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
        state_manager=sm,
        model="parent-model",
        tools=[_NamedTool("current_time")],
        backend=CompositeBackend(default=object(), routes={"/skills/": object()}),
        checkpointer=object(),
    )
    await proxy["runnable"].ainvoke({"messages": []})

    create_kwargs = calls[0]
    assert create_kwargs["system_prompt"] == "You are a concise reviewer."
    # No declared mounts: the only file surface is the listed skill's own folder.
    filesystem = next(m for m in create_kwargs["middleware"] if isinstance(m, FilesystemMiddleware))
    assert set(filesystem.backend.routes) == {"/skills/review/"}


KNOWLEDGE_PAGE = "---\ntype: Concept\ntitle: Retry\n---\nRetry three times.\n"


class _KnowledgeStateManager(_StateManager):
    def __init__(self, documents=None, bases=("eng", "ops")):
        super().__init__(documents)
        self.bases = list(bases)
        self.pages = {}

    async def get_knowledge_config(self):
        return {"bases": [{"id": base, "name": base.title()} for base in self.bases]}

    async def get_knowledge_page(self, base, path):
        return self.pages.get((base, path))

    async def save_knowledge_page(self, base, path, content):
        self.pages[(base, path)] = {"content": content}

    async def list_knowledge_pages(self):
        return [(base, path, payload) for (base, path), payload in self.pages.items()]


def _knowledge_system(grants: str, perm_path: str = "/knowledge/eng/", ops: str = "read") -> str:
    return (
        VALID_SYSTEM.replace("kind: object\n", f"kind: object\nknowledge:\n{grants}")
        .replace("skills: [/skills/review/]", "skills: []")
        .replace(
            "operations: [read]\n    paths: [/repos/]",
            f"operations: [{ops}]\n    paths: [{perm_path}]",
        )
    )


@pytest.mark.parametrize(
    ("grants", "message"),
    [
        ("  - base: Bad Base\n    access: read\n", r"knowledge\[0\]\.base"),
        ("  - base: eng\n    access: admin\n", r"knowledge\[0\]\.access must be read or write"),
        ("  - base: eng\n    access: read\n  - base: eng\n    access: write\n", "duplicate"),
    ],
)
def test_manifest_rejects_invalid_knowledge_grants(grants, message):
    with pytest.raises(ValueError, match=message):
        parse_object_agent_manifest(_knowledge_system(grants), source="test")


def test_manifest_rejects_knowledge_as_mount():
    content = VALID_SYSTEM.replace(
        "kind: object\n", "kind: object\nmounts:\n  - path: /knowledge/eng/\n    access: read\n"
    )
    with pytest.raises(ValueError, match="'knowledge' field, not mounts"):
        parse_object_agent_manifest(content, source="test")


async def _knowledge_child_backend(monkeypatch, access: str):
    from deepagents.backends.protocol import LsResult, WriteResult

    from app.progenitor.agent.knowledge_backend import KnowledgePageBackend

    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.StateBackend",
        lambda: SimpleNamespace(
            aread=AsyncMock(return_value=ReadResult(error="File not found")),
            awrite=AsyncMock(return_value=WriteResult(error="no state route")),
            als=AsyncMock(return_value=LsResult(entries=[])),
        ),
    )
    manifest = parse_object_agent_manifest(
        _knowledge_system(
            f"  - base: eng\n    access: {access}\n",
            ops="read, write" if access == "write" else "read",
        ),
        source="test",
    )
    sm = _KnowledgeStateManager()
    sm.pages[("eng", "a.md")] = {"content": KNOWLEDGE_PAGE}
    sm.pages[("ops", "secret.md")] = {"content": KNOWLEDGE_PAGE}
    parent = CompositeBackend(
        default=object(),
        routes={"/knowledge/": KnowledgePageBackend(sm), "/skills/": object()},
    )
    calls = []
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.create_agent", lambda **kw: calls.append(kw)
    )
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware", lambda **_kwargs: object()
    )
    build_object_agent(
        manifest,
        model=object(),
        tools=[SimpleNamespace(name="current_time")],
        backend=parent,
        checkpointer=None,
    )
    middleware = calls[0]["middleware"]
    skills = next(m for m in middleware if isinstance(m, SkillsMiddleware))
    backend = next(m for m in middleware if isinstance(m, FilesystemMiddleware)).backend
    return sm, backend, skills


async def test_read_knowledge_grant_reads_but_cannot_write_or_see_siblings(monkeypatch):
    _, backend, _ = await _knowledge_child_backend(monkeypatch, "read")
    assert "Retry three times." in (await backend.aread("/knowledge/eng/a.md")).file_data["content"]
    assert (await backend.awrite("/knowledge/eng/b.md", KNOWLEDGE_PAGE)).error
    assert (await backend.aread("/knowledge/ops/secret.md")).error
    listed = [entry["path"] for entry in (await backend.als("/knowledge/")).entries]
    assert all(not path.startswith("/knowledge/ops") for path in listed)


async def test_write_knowledge_grant_lands_in_its_base(monkeypatch):
    sm, backend, _ = await _knowledge_child_backend(monkeypatch, "write")
    assert (await backend.awrite("/knowledge/eng/b.md", KNOWLEDGE_PAGE)).error is None
    assert ("eng", "b.md") in sm.pages
    assert (await backend.awrite("/knowledge/eng/INSTRUCTIONS.md", "brief")).error
    assert (await backend.awrite("/knowledge/ops/b.md", KNOWLEDGE_PAGE)).error


def test_knowledge_grant_attaches_curating_skill(monkeypatch):
    from app.progenitor.agent import object_agents

    manifest = parse_object_agent_manifest(
        _knowledge_system("  - base: eng\n    access: read\n"), source="test"
    )
    seen = []
    monkeypatch.setattr(
        object_agents,
        "_DeclaredSkillsBackend",
        lambda backend, dirs, **_kw: seen.append(dirs) or backend,
    )
    monkeypatch.setattr(object_agents, "create_agent", lambda **_kw: object())
    monkeypatch.setattr(object_agents, "SummarizationMiddleware", lambda **_kwargs: object())
    parent = CompositeBackend(
        default=object(),
        routes={
            "/knowledge/": SimpleNamespace(for_object_agent=lambda: object()),
            "/skills/": object(),
        },
    )
    build_object_agent(
        manifest,
        model=object(),
        tools=[SimpleNamespace(name="current_time")],
        backend=parent,
        checkpointer=None,
    )
    assert any("/skills/curating-knowledge" in str(d) for dirs in seen for d in dirs)


async def test_proxy_skips_unknown_base_and_reports_warning(monkeypatch):
    from langchain_core.messages import AIMessage

    from app.progenitor.agent.knowledge_backend import KnowledgePageBackend

    system = _knowledge_system("  - base: nope\n    access: read\n", perm_path="/knowledge/nope/")
    sm = _KnowledgeStateManager({"user/reviewer/SYSTEM.md": system})
    source = (await discover_object_agents(sm))[0]
    built = []

    def create(**kwargs):
        built.append(kwargs)
        return SimpleNamespace(
            ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="done")]})
        )

    monkeypatch.setattr("app.progenitor.agent.object_agents.create_agent", create)
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware", lambda **_kwargs: object()
    )
    proxy = create_object_agent_proxy(
        source,
        state_manager=sm,
        model=object(),
        tools=[SimpleNamespace(name="current_time")],
        backend=CompositeBackend(
            default=object(), routes={"/knowledge/": KnowledgePageBackend(sm), "/skills/": object()}
        ),
        checkpointer=None,
    )
    result = await proxy["runnable"].ainvoke({"messages": []})
    last = result["messages"][-1]
    assert last.text.startswith("done\n\nWarnings:\n- ")
    assert "does not exist in /config/knowledge.json" in last.text
    assert built


async def test_proxy_runs_with_remaining_grants_when_a_granted_base_is_missing(monkeypatch):
    from langchain_core.messages import AIMessage

    from app.progenitor.agent.knowledge_backend import KnowledgePageBackend

    # Permissions name both bases; the missing base's must not break the build.
    system = _knowledge_system(
        "  - base: eng\n    access: read\n  - base: nope\n    access: read\n",
        perm_path="/knowledge/eng/, /knowledge/nope/",
    )
    sm = _KnowledgeStateManager({"user/reviewer/SYSTEM.md": system})
    source = (await discover_object_agents(sm))[0]
    built = []

    def create(**kwargs):
        built.append(kwargs)
        return SimpleNamespace(
            ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="done")]})
        )

    monkeypatch.setattr("app.progenitor.agent.object_agents.create_agent", create)
    monkeypatch.setattr(
        "app.progenitor.agent.object_agents.SummarizationMiddleware", lambda **_kwargs: object()
    )
    proxy = create_object_agent_proxy(
        source,
        state_manager=sm,
        model=object(),
        tools=[SimpleNamespace(name="current_time")],
        backend=CompositeBackend(
            default=object(), routes={"/knowledge/": KnowledgePageBackend(sm), "/skills/": object()}
        ),
        checkpointer=None,
    )
    result = await proxy["runnable"].ainvoke({"messages": []})
    assert "'nope'" in result["messages"][-1].text
    filesystem = next(m for m in built[0]["middleware"] if isinstance(m, FilesystemMiddleware))
    assert {route for route in filesystem.backend.routes if route.startswith("/knowledge/")} == {
        "/knowledge/eng/"
    }
