"""Agent-written data artifacts: shared storage contract and validation.

An artifact is one text file (CSV, TSV, JSON, JSONL, Markdown or plain text)
that Progena, an object agent with an ``/artifacts/`` mount, or a workflow
writes at ``/artifacts/<path>``. It is stored as an ``artifact:<path>`` content
document, so canvases read it through ``host.artifacts`` and workflows react to
its document events. Only the latest content is kept.

Pure module (no store access) shared by the runtime mount and the backend routes.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from src.progena.documents.kinds import ARTIFACT_DOCUMENT_KEY_PREFIX

__all__ = [
    "ARTIFACT_DOCUMENT_KEY_PREFIX",
    "ARTIFACT_FILE_EXTENSIONS",
    "MAX_ARTIFACT_BYTES",
    "ArtifactValidationError",
    "artifact_key",
    "validate_artifact",
    "validate_artifact_path",
]

ARTIFACT_FILE_EXTENSIONS = frozenset({".csv", ".tsv", ".json", ".jsonl", ".md", ".txt"})
MAX_ARTIFACT_BYTES = 1024 * 1024

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ArtifactValidationError(ValueError):
    """An artifact path or content broke the artifact contract."""


def validate_artifact_path(path: str) -> str:
    """Return an ``/artifacts/``-relative path if valid, else raise.

    Paths are relative POSIX paths whose segments start with a letter or digit
    (so no ``..`` or hidden files) and end in an allowed extension.
    """

    if not path or path.startswith("/") or "\\" in path:
        raise ArtifactValidationError(f"Invalid artifact path {path!r}: must be relative.")
    for segment in path.split("/"):
        if not _SEGMENT_RE.fullmatch(segment):
            raise ArtifactValidationError(
                f"Invalid artifact path {path!r}: segment {segment!r} must start with a "
                "letter or digit and contain only letters, digits, '.', '_' and '-'."
            )
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in ARTIFACT_FILE_EXTENSIONS:
        allowed = ", ".join(sorted(ARTIFACT_FILE_EXTENSIONS))
        raise ArtifactValidationError(
            f"Unsupported artifact type {suffix or '(none)'!r} for {path!r}; allowed: {allowed}."
        )
    return path


def validate_artifact(path: str, content: str) -> None:
    """Validate writing ``content`` to artifact ``path``; raise on violation."""

    validate_artifact_path(path)
    size = len(content.encode())
    if size > MAX_ARTIFACT_BYTES:
        raise ArtifactValidationError(
            f"{path} is {size} bytes; the per-artifact limit is {MAX_ARTIFACT_BYTES}."
        )


def artifact_key(path: str) -> str:
    """Store key of one artifact: ``artifact:<path>``."""

    return f"{ARTIFACT_DOCUMENT_KEY_PREFIX}{path}"
