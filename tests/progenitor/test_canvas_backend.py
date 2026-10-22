"""Canvas virtual filesystem contract tests."""

from __future__ import annotations

import json
from typing import Any

from src.progenitor.agent.filesystem.canvases import CanvasDirectoryBackend
from src.progenitor.canvases.contracts import MAX_CANVAS_FILE_BYTES, canvas_revision
from src.progenitor.documents.types import BUILTIN_DOCUMENT_TYPES


class FakeCanvasStateManager:
    def __init__(self) -> None:
        self.docs: dict[str, str] = {}
        self.statuses: dict[str, dict[str, Any]] = {}

    async def get_content_document(self, kind: str, relative_path: str) -> str | None:
        assert kind == "canvas"
        return self.docs.get(relative_path)

    async def save_content_document(self, kind: str, relative_path: str, content: str) -> None:
        assert kind == "canvas"
        self.docs[relative_path] = content

    async def delete_content_document(self, kind: str, relative_path: str) -> None:
        assert kind == "canvas"
        self.docs.pop(relative_path, None)

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        assert kind == "canvas"
        return list(self.docs.items())

    async def list_document_types(self):
        return {item.id: item for item in BUILTIN_DOCUMENT_TYPES}

    async def get_canvas_status(self, name: str) -> dict[str, Any] | None:
        return self.statuses.get(name)


def manifest(name: str = "demo", **extra: object) -> str:
    return json.dumps({"name": name, "title": "Demo", "entry": "index.mjs", **extra})


async def test_canvas_mount_writes_manifest_then_files_to_content_documents():
    state_manager = FakeCanvasStateManager()
    backend = CanvasDirectoryBackend(state_manager)

    first = await backend.awrite("/demo/manifest.json", manifest())
    second = await backend.awrite("/demo/index.mjs", "export default () => {}")

    assert first.error is None
    assert second.error is None
    assert state_manager.docs == {
        "demo/manifest.json": manifest(),
        "demo/index.mjs": "export default () => {}",
    }


async def test_canvas_mount_rejects_invalid_writes_without_persisting():
    state_manager = FakeCanvasStateManager()
    backend = CanvasDirectoryBackend(state_manager)

    cases = [
        ("/demo/index.mjs", "export default () => {}", "manifest.json"),
        ("/x/manifest.json", manifest("x"), "Invalid canvas name"),
        ("/demo/file.js", "x", "Unsupported canvas file type"),
        ("/demo/manifest.json", manifest("other"), "must equal"),
        (
            "/demo/manifest.json",
            manifest(libraries=["not-a-library"]),
            "Unknown libraries",
        ),
        ("/_private/manifest.json", manifest("private"), "Invalid canvas name"),
        ("/demo/manifest.json", "x" * (MAX_CANVAS_FILE_BYTES + 1), "per-file limit"),
    ]
    for path, content, expected in cases:
        result = await backend.awrite(path, content)
        assert result.error is not None and expected in result.error
        assert state_manager.docs == {}


async def test_canvas_edit_revalidates_post_edit_content():
    state_manager = FakeCanvasStateManager()
    backend = CanvasDirectoryBackend(state_manager)
    await backend.awrite("/demo/manifest.json", manifest())

    result = await backend.aedit("/demo/manifest.json", '"demo"', '"wrong"')

    assert result.error is not None and "must equal" in result.error
    assert json.loads(state_manager.docs["demo/manifest.json"])["name"] == "demo"


async def test_canvas_catalogue_and_status_are_virtual_projections():
    state_manager = FakeCanvasStateManager()
    backend = CanvasDirectoryBackend(state_manager)
    await backend.awrite("/demo/manifest.json", manifest(libraries=["d3"]))
    await backend.awrite("/demo/index.mjs", "export default () => {}")
    files = {path.removeprefix("demo/"): content for path, content in state_manager.docs.items()}
    revision = canvas_revision(files)
    state_manager.statuses["demo"] = {
        "state": "loaded",
        "revision": revision,
        "errors": [],
        "reported_at": "2026-10-20T00:00:00Z",
        "ignored": True,
    }

    catalogue = await backend.aread("/_catalogue.json")
    status = await backend.aread("/demo/status.json")
    assert catalogue.error is None
    catalogue_data = json.loads(catalogue.file_data["content"])
    assert {library["id"] for library in catalogue_data["libraries"]} == {"d3", "three"}
    assert {item["id"] for item in catalogue_data["document_types"]} >= {
        "canvas",
        "canvas_data",
        "canvas_status",
    }
    assert "mount" in catalogue_data["host_api"]
    assert json.loads(status.file_data["content"]) == {
        "current_revision": revision,
        "errors": [],
        "reported_at": "2026-10-20T00:00:00Z",
        "revision": revision,
        "stale": False,
        "state": "loaded",
    }

    state_manager.statuses["demo"]["revision"] = "old"
    stale = await backend.aread("/demo/status.json")
    assert json.loads(stale.file_data["content"])["stale"] is True

    state_manager.statuses.clear()
    unloaded = await backend.aread("/demo/status.json")
    assert json.loads(unloaded.file_data["content"])["state"] == "not_loaded"


async def test_canvas_ls_includes_catalogue_canvas_files_and_status():
    state_manager = FakeCanvasStateManager()
    backend = CanvasDirectoryBackend(state_manager)
    await backend.awrite("/demo/manifest.json", manifest())
    await backend.awrite("/demo/index.mjs", "export default () => {}")

    root = await backend.als("/")
    canvas = await backend.als("/demo")

    assert [(item["path"], item["is_dir"]) for item in root.entries] == [
        ("/_catalogue.json", False),
        ("/demo", True),
    ]
    assert [(item["path"], item["is_dir"]) for item in canvas.entries] == [
        ("/demo/index.mjs", False),
        ("/demo/manifest.json", False),
        ("/demo/status.json", False),
    ]
