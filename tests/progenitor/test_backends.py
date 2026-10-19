"""
Tests for Phase 3: Backend configuration and Postgres-backed content routing.
"""

from pathlib import Path
from unittest.mock import MagicMock

from app.progenitor.agent.backends import (
    ReadOnlyBackend,
    create_object_agent_plugin_skill_routes,
    create_progenitor_backend,
)
from app.progenitor.agent.state_backend import RepoDirectoryBackend
from app.progenitor.document_types import BUILTIN_DOCUMENT_TYPES


class FakeContentStateManager(MagicMock):
    """Async state_manager stub backing get/save/delete/list_content_document.

    Subclasses MagicMock so the unrelated /config/ state-file factories (which
    bind arbitrary StateManager getters/setters at construction time, e.g.
    ``get_mcp_config``) keep working without needing every method stubbed.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._docs: dict[str, dict[str, str]] = {
            "instruction": {},
            "skill": {},
            "memory": {},
            "agent": {},
        }

    async def get_content_document(self, kind: str, relative_path: str) -> str | None:
        return self._docs[kind].get(relative_path)

    async def save_content_document(self, kind: str, relative_path: str, content: str) -> None:
        self._docs[kind][relative_path] = content

    async def delete_content_document(self, kind: str, relative_path: str) -> None:
        self._docs[kind].pop(relative_path, None)

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        return list(self._docs[kind].items())

    async def list_document_types(self):
        return {item.id: item for item in BUILTIN_DOCUMENT_TYPES}


class TestBackendFactory:
    """Tests for the backend factory."""

    @staticmethod
    def _patch_runtime_free_state_backend(monkeypatch):
        class RuntimeFreeStateBackend:
            def __init__(self):
                self.mode = "runtime-free"

        monkeypatch.setattr(
            "app.progenitor.agent.backends.StateBackend",
            RuntimeFreeStateBackend,
        )
        return RuntimeFreeStateBackend

    def test_returns_composite_backend_without_state_manager(self, monkeypatch):
        """Without a state_manager, only the disk-backed routes are mounted."""
        from deepagents.backends import CompositeBackend

        runtime_free_cls = self._patch_runtime_free_state_backend(monkeypatch)
        backend = create_progenitor_backend(project_root=".")

        assert isinstance(backend, CompositeBackend)
        assert isinstance(backend.default, runtime_free_cls)
        assert "/memories/" not in backend.routes
        assert "/instructions/" not in backend.routes
        assert "/config/" not in backend.routes
        # /skills/ is a plain disk mount (like /schemas/), so it's present even
        # without a state_manager -- as long as data/skills/ exists on disk,
        # which it does for this real project_root=".".
        assert "/skills/" in backend.routes
        assert "/agents/" not in backend.routes

    def test_factory_uses_project_root(self, monkeypatch):
        """project_root is accepted for scoped filesystem mounts."""
        import tempfile

        from deepagents.backends import FilesystemBackend

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_free_cls = self._patch_runtime_free_state_backend(monkeypatch)
            root = Path(tmpdir)
            for rel in ("app", "data/schemas", "data/skills", "logs", "repos"):
                (root / rel).mkdir(parents=True, exist_ok=True)

            state_manager = FakeContentStateManager()
            backend = create_progenitor_backend(
                project_root=tmpdir, worktree_dir=root / "repos", state_manager=state_manager
            )
            assert backend is not None
            assert isinstance(backend.default, runtime_free_cls)
            assert "/memories/" in backend.routes
            assert "/instructions/" in backend.routes
            assert "/skills/" in backend.routes
            assert "/self/" in backend.routes
            assert "/repos/" in backend.routes
            assert "/logs/" in backend.routes
            assert "/schemas/" in backend.routes
            assert isinstance(backend.routes["/self/"], ReadOnlyBackend)
            assert isinstance(backend.routes["/repos/"], ReadOnlyBackend)
            assert isinstance(backend.routes["/config/repos/"], RepoDirectoryBackend)
            assert isinstance(backend.routes["/logs/"], ReadOnlyBackend)
            assert isinstance(backend.routes["/schemas/"], ReadOnlyBackend)
            assert isinstance(backend.routes["/schemas/"]._backend, FilesystemBackend)
            from app.progenitor.agent.state_backend import InstructionDocumentDirectoryBackend

            assert isinstance(backend.routes["/instructions/"], InstructionDocumentDirectoryBackend)
            # /skills/ is disk-backed now (like /schemas/), not Postgres-backed.
            assert isinstance(backend.routes["/skills/"], ReadOnlyBackend)
            assert isinstance(backend.routes["/skills/"]._backend, FilesystemBackend)

    def test_state_manager_mounts_config_and_inbox_routes(self, monkeypatch):
        """A state_manager adds /config/, /tasks/, /workflows/, /routines/, /inbox/."""
        from unittest.mock import MagicMock

        from app.progenitor.agent.state_backend import (
            InboxDirectoryBackend,
            KanbanTaskDirectoryBackend,
            RoutineDirectoryBackend,
            StateDocumentBackend,
            WorkflowDirectoryBackend,
        )

        self._patch_runtime_free_state_backend(monkeypatch)

        # The state-file factories bind StateManager getters/setters at
        # construction time, so the stub needs arbitrary attribute access.
        backend = create_progenitor_backend(project_root=".", state_manager=MagicMock())
        assert isinstance(backend.routes["/config/"], StateDocumentBackend)
        assert isinstance(backend.routes["/tasks/"], KanbanTaskDirectoryBackend)
        assert isinstance(backend.routes["/workflows/"], WorkflowDirectoryBackend)
        assert isinstance(backend.routes["/routines/"], RoutineDirectoryBackend)
        assert isinstance(backend.routes["/inbox/"], InboxDirectoryBackend)

    def test_factory_defaults_to_cwd(self, monkeypatch):
        """Factory defaults to cwd when no project_root specified."""
        self._patch_runtime_free_state_backend(monkeypatch)
        backend = create_progenitor_backend()
        assert backend is not None

    async def test_memories_write_and_schemas_read_round_trip(self, monkeypatch):
        """/memories/ (Postgres, read-write) and /schemas/ (disk, read-only) both work end to end."""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            self._patch_runtime_free_state_backend(monkeypatch)
            root = Path(tmpdir)
            for rel in ("app", "data/schemas", "logs"):
                (root / rel).mkdir(parents=True, exist_ok=True)

            schema = {"type": "object", "properties": {"id": {"type": "string"}}}
            (root / "data" / "schemas" / "widget.schema.v1.json").write_text(
                json.dumps(schema), encoding="utf-8"
            )

            state_manager = FakeContentStateManager()
            backend = create_progenitor_backend(project_root=tmpdir, state_manager=state_manager)

            content = "## Testing\n- Keep tests focused.\n"
            written = await backend.awrite("/memories/patterns/testing.md", content)
            memory_read = await backend.aread("/memories/patterns/testing.md")
            schema_read = await backend.aread("/schemas/widget.schema.v1.json")

            assert written.error is None
            assert state_manager._docs["memory"]["patterns/testing.md"] == content
            assert memory_read.error is None
            assert memory_read.file_data["content"] == content
            assert schema_read.error is None
            assert json.loads(schema_read.file_data["content"]) == schema

    async def test_instructions_mount_reads_and_validates_writes(self, monkeypatch):
        """/instructions/ reads Postgres content and validates writes."""
        from app.progenitor.agent.state_backend import InstructionDocumentDirectoryBackend

        self._patch_runtime_free_state_backend(monkeypatch)
        state_manager = FakeContentStateManager()
        state_manager._docs["instruction"]["progenitor/genesis.md"] = "# Genesis\n"

        backend = create_progenitor_backend(project_root=".", state_manager=state_manager)

        instruction_read = await backend.aread("/instructions/progenitor/genesis.md")
        assert instruction_read.error is None
        assert instruction_read.file_data["content"] == "# Genesis\n"

        assert isinstance(backend.routes["/instructions/"], InstructionDocumentDirectoryBackend)

        valid = await backend.awrite("/instructions/progenitor/genesis.md", "# Updated\n")
        assert valid.error is None
        assert state_manager._docs["instruction"]["progenitor/genesis.md"] == "# Updated\n"

        invalid = await backend.awrite(
            "/instructions/progenitor/genesis.md",
            "---\nunknown_key: 1\n---\n# Body\n",
        )
        assert invalid.error is not None
        assert "unknown front matter key" in invalid.error

    async def test_plugin_skill_segment_is_read_only_and_scoped(self, monkeypatch):
        """/skills/<segment>/ reads only that plugin's own Postgres skill rows."""
        self._patch_runtime_free_state_backend(monkeypatch)
        state_manager = FakeContentStateManager()
        state_manager._docs["skill"]["acme__formatter/lint/SKILL.md"] = "---\nname: lint\n---\n"
        state_manager._docs["skill"]["other__plugin/quality/SKILL.md"] = "---\nname: quality\n---\n"

        backend = create_progenitor_backend(
            project_root=".",
            state_manager=state_manager,
            plugin_skill_segments=["acme__formatter", "other__plugin"],
        )

        assert isinstance(backend.routes["/skills/acme__formatter/"], ReadOnlyBackend)
        read = await backend.aread("/skills/acme__formatter/lint/SKILL.md")
        assert read.error is None
        assert read.file_data["content"] == "---\nname: lint\n---\n"

        # Scoped to its own segment: the other plugin's file isn't visible here.
        cross_read = await backend.aread("/skills/acme__formatter/quality/SKILL.md")
        assert cross_read.error is not None

    async def test_agent_introspection_combines_read_only_disk_and_pg(self, monkeypatch, tmp_path):
        self._patch_runtime_free_state_backend(monkeypatch)
        disk_path = tmp_path / "data/agents/objects/reviewer/SYSTEM.md"
        disk_path.parent.mkdir(parents=True)
        disk_path.write_text("Bundled disk manifest")
        state_manager = FakeContentStateManager()
        state_manager._docs["agent"]["native/reviewer/SYSTEM.md"] = "Persisted native manifest"
        state_manager._docs["agent"]["user/custom/SYSTEM.md"] = "Persisted user manifest"
        backend = create_progenitor_backend(project_root=tmp_path, state_manager=state_manager)
        for path in ("/agents/native/reviewer/SYSTEM.md", "/agents/objects/reviewer/SYSTEM.md"):
            read = await backend.aread(path)
            assert read.error is None
            assert read.file_data["content"] == "Bundled disk manifest"
            assert (await backend.awrite(path, "overwrite")).error is not None
            assert (await backend.aedit(path, "Bundled", "Modified")).error is not None
            # CompositeBackend exposes no delete operation; native routes have
            # no writable file tools and cannot be uninstalled through plugins.
            assert not hasattr(backend, "adelete")
            uploads = await backend.aupload_files([(path, b"overwrite")])
            assert uploads[0].error == "permission_denied"
            assert disk_path.read_text() == "Bundled disk manifest"
        assert (await backend.aread("/agents/user/custom/SYSTEM.md")).error is None
        assert (await backend.aread("/agents/objects/custom/SYSTEM.md")).error is not None
        assert isinstance(backend.routes["/agents/"], ReadOnlyBackend)
        del state_manager._docs["agent"]["native/reviewer/SYSTEM.md"]
        assert (await backend.aread("/agents/objects/reviewer/SYSTEM.md")).error is None

    async def test_user_agent_creation_yaml_render_edit_and_discovery(self, monkeypatch):
        from app.progenitor.agent.object_agent_manifest import discover_object_agents
        from app.progenitor.tools.json_to_yaml import JsonToYamlTool
        from app.progenitor.tools.protocol import ToolContext

        self._patch_runtime_free_state_backend(monkeypatch)
        sm = FakeContentStateManager()
        backend = create_progenitor_backend(project_root=".", state_manager=sm)
        rendered = await JsonToYamlTool().execute(
            ToolContext(
                tool_registry=None,
                llm_client=None,
                tool_args={
                    "data": {
                        "name": "custom-reviewer",
                        "description": "Review: code",
                        "version": "1.0.0",
                        "kind": "object",
                        "tools": [],
                        "skills": [],
                        "system_prompt": "Review code carefully: explain your findings.",
                    },
                    "body_field": "system_prompt",
                },
            )
        )
        assert rendered.success
        path = "/agents/user/custom-reviewer/SYSTEM.md"
        content = rendered.data["content"]
        assert (await backend.awrite(path, content)).error is None
        assert (await backend.aread(path)).file_data["content"] == content
        assert (
            await backend.aedit(path, "Review code carefully", "Review code thoroughly")
        ).error is None
        discovered = await discover_object_agents(sm)
        assert len(discovered) == 1
        assert discovered[0].name == "custom-reviewer"
        assert discovered[0].manifest.description == "Review: code"
        assert "thoroughly" in discovered[0].manifest.system_prompt
        assert discovered[0].relative_path == "user/custom-reviewer/SYSTEM.md"

        # Failed edits leave the last valid document intact.
        saved = sm._docs["agent"]["user/custom-reviewer/SYSTEM.md"]
        assert (await backend.aedit(path, "kind: object", "kind: graph")).error is not None
        assert sm._docs["agent"]["user/custom-reviewer/SYSTEM.md"] == saved

    async def test_user_agent_rejects_invalid_documents_and_paths(self, monkeypatch):
        self._patch_runtime_free_state_backend(monkeypatch)
        sm = FakeContentStateManager()
        backend = create_progenitor_backend(project_root=".", state_manager=sm)
        valid = "---\nname: custom\ndescription: Reviewer\nversion: '1.0'\nkind: object\ntools: []\n---\nReview code.\n"
        for content in (
            "No front matter",
            valid.replace("version: '1.0'\n", ""),
            valid.replace("tools: []", "tools: [42]"),
            valid.replace("Review code.", ""),
            valid.replace("name: custom", "name: another"),
        ):
            assert (
                await backend.awrite("/agents/user/custom/SYSTEM.md", content)
            ).error is not None
        for path in ("/agents/user/custom/agent.md", "/agents/user/custom/nested/SYSTEM.md"):
            assert (await backend.awrite(path, valid)).error is not None
        assert sm._docs["agent"] == {}

    async def test_user_agent_cannot_shadow_native_or_plugin_agent(self, monkeypatch, tmp_path):
        self._patch_runtime_free_state_backend(monkeypatch)
        sm = FakeContentStateManager()
        content = "---\nname: reviewer\ndescription: Reviewer\nversion: '1.0'\nkind: object\n---\nReview code.\n"
        native = tmp_path / "data/agents/objects/reviewer/SYSTEM.md"
        native.parent.mkdir(parents=True)
        native.write_text(content)
        backend = create_progenitor_backend(project_root=tmp_path, state_manager=sm)
        path = "/agents/user/reviewer/SYSTEM.md"
        assert "already owned" in (await backend.awrite(path, content)).error
        native.unlink()
        sm._docs["agent"]["acme__plugin/reviewer/SYSTEM.md"] = content
        assert "already owned" in (await backend.awrite(path, content)).error
        assert "user/reviewer/SYSTEM.md" not in sm._docs["agent"]


async def test_dynamic_plugin_skill_routes_are_exact_and_read_only():
    sm = FakeContentStateManager()
    sm._docs["skill"]["acme__new/lint/SKILL.md"] = "Lint skill"
    sm._docs["skill"]["acme__new/lint/resources/examples.md"] = "Companion examples"
    sm._docs["skill"]["acme__new/other/SKILL.md"] = "Sibling skill"
    routes = create_object_agent_plugin_skill_routes(
        sm, ["/skills/acme__new/lint/", "/skills/native/", "/repos/"]
    )
    assert set(routes) == {"/skills/acme__new/lint/"}
    route = routes["/skills/acme__new/lint/"]
    assert (await route.aread("/SYSTEM.md")).error is not None
    assert (await route.aread("/SKILL.md")).file_data["content"] == "Lint skill"
    assert (await route.aread("/resources/examples.md")).file_data[
        "content"
    ] == "Companion examples"
    assert (await route.aread("/other/SKILL.md")).error is not None
    assert not await route.can_write_file("/SKILL.md", "change", {})
    assert not await route.can_delete_file("/SKILL.md", {})


def test_dynamic_plugin_skill_inference_stays_inside_declared_mounts():
    sm = FakeContentStateManager()
    routes = create_object_agent_plugin_skill_routes(
        sm,
        ["/skills/"],
        ["/skills/acme__new/review/", "/skills/native/"],
    )
    assert set(routes) == {"/skills/acme__new/review/"}
    narrower = create_object_agent_plugin_skill_routes(
        sm,
        ["/skills/acme__new/review/"],
        ["/skills/other__plugin/review/", "/skills/acme__new/reviewer/"],
    )
    assert set(narrower) == {"/skills/acme__new/review/"}
