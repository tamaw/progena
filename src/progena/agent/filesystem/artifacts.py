"""Virtual filesystem mount for agent-written data artifacts."""

from __future__ import annotations

from typing import Any

from deepagents.backends.protocol import WriteResult

from src.progena.agent.filesystem.content import ContentDocumentDirectoryBackend
from src.progena.artifacts.contracts import ArtifactValidationError, validate_artifact


class ArtifactDirectoryBackend(ContentDocumentDirectoryBackend):
    """``/artifacts/``: validated CSV/JSON/text files that canvases can visualise.

    Edits go through :meth:`awrite` (see the base class), so they are validated too.
    """

    def __init__(self, state_manager: Any) -> None:
        super().__init__(state_manager, "artifact")

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        try:
            validate_artifact(self._name(file_path), content)
        except ArtifactValidationError as exc:
            return WriteResult(error=f"Invalid artifact: {exc}")
        return await super().awrite(file_path, content)
