"""Artifact virtual filesystem contract tests."""

from __future__ import annotations

from src.progena.agent.filesystem.artifacts import ArtifactDirectoryBackend
from src.progena.artifacts.contracts import MAX_ARTIFACT_BYTES


class FakeArtifactStateManager:
    def __init__(self) -> None:
        self.docs: dict[str, str] = {}

    async def get_content_document(self, kind: str, relative_path: str) -> str | None:
        assert kind == "artifact"
        return self.docs.get(relative_path)

    async def save_content_document(self, kind: str, relative_path: str, content: str) -> None:
        assert kind == "artifact"
        self.docs[relative_path] = content

    async def delete_content_document(self, kind: str, relative_path: str) -> None:
        assert kind == "artifact"
        self.docs.pop(relative_path, None)

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        assert kind == "artifact"
        return list(self.docs.items())


async def test_artifact_mount_stores_nested_data_files_as_documents():
    state_manager = FakeArtifactStateManager()
    backend = ArtifactDirectoryBackend(state_manager)

    csv = await backend.awrite("/sales/q3.csv", "month,revenue\nJul,10\n")
    json_file = await backend.awrite("/report.json", '{"ok": true}')

    assert csv.error is None
    assert json_file.error is None
    assert state_manager.docs == {
        "sales/q3.csv": "month,revenue\nJul,10\n",
        "report.json": '{"ok": true}',
    }
    listing = await backend.als("/sales/")
    assert [entry["path"] for entry in listing.entries or []] == ["/sales/q3.csv"]


async def test_artifact_mount_rejects_bad_paths_types_and_size_without_persisting():
    state_manager = FakeArtifactStateManager()
    backend = ArtifactDirectoryBackend(state_manager)

    for path, content in (
        ("/data.xlsx", "x"),
        ("/noext", "x"),
        ("/../escape.csv", "x"),
        ("/sales/.hidden.csv", "x"),
        ("/sales\n/q3.csv", "x"),
        ("/big.txt", "x" * (MAX_ARTIFACT_BYTES + 1)),
    ):
        result = await backend.awrite(path, content)
        assert result.error is not None and result.error.startswith("Invalid artifact:"), path
    assert state_manager.docs == {}

    at_limit = await backend.awrite("/big.txt", "x" * MAX_ARTIFACT_BYTES)
    assert at_limit.error is None


async def test_artifact_edit_is_revalidated_against_the_size_cap():
    state_manager = FakeArtifactStateManager()
    backend = ArtifactDirectoryBackend(state_manager)
    await backend.awrite("/log.txt", "seed")

    grown = await backend.aedit("/log.txt", "seed", "x" * (MAX_ARTIFACT_BYTES + 1))

    assert grown.error is not None and "per-artifact limit" in grown.error
    assert state_manager.docs == {"log.txt": "seed"}
