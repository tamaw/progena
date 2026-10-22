"""Agent-built UI canvases: shared storage contract and validation.

A canvas is a directory of text files the agent writes at ``/canvases/<name>/``
(stored one row per file as ``canvas:<name>/<path>`` content documents). The
frontend renders it in a sandboxed, network-less iframe under **Canvases**; the
entry ``.mjs`` file default-exports ``mount(root, host)``.

Pure module (no store access) shared by the runtime mount and the backend routes,
so both enforce the same names, paths, caps, and manifest rules.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any

from src.progenitor.documents.catalog import load_schema
from src.progenitor.documents.kinds import (
    CANVAS_DATA_KEY_PREFIX,
    CANVAS_DOCUMENT_KEY_PREFIX,
    CANVAS_STATUS_KEY_PREFIX,
)
from src.progenitor.instructions.paths import resolve_data_dir
from src.progenitor.llm.structured import validate_against_schema

__all__ = [
    "MAX_CANVAS_BYTES",
    "MAX_CANVAS_DATA_BYTES",
    "MAX_CANVAS_FILES",
    "MAX_CANVAS_FILE_BYTES",
    "CANVAS_DATA_KEY_PREFIX",
    "CANVAS_DOCUMENT_KEY_PREFIX",
    "CANVAS_FILE_EXTENSIONS",
    "CANVAS_MANIFEST_FILE",
    "CANVAS_STATUS_KEY_PREFIX",
    "CanvasValidationError",
    "load_canvas_libraries",
    "canvas_data_key",
    "canvas_file_key",
    "canvas_revision",
    "canvas_status_key",
    "split_canvas_file_key",
    "validate_canvas_data_key",
    "validate_canvas_file",
    "validate_canvas_manifest",
    "validate_canvas_name",
    "validate_canvas_path",
]

CANVAS_MANIFEST_FILE = "manifest.json"
CANVAS_FILE_EXTENSIONS = frozenset({".mjs", ".css", ".json", ".svg", ".md", ".txt"})
MAX_CANVAS_FILE_BYTES = 256 * 1024
MAX_CANVAS_BYTES = 1024 * 1024
MAX_CANVAS_FILES = 32
MAX_CANVAS_DATA_BYTES = 256 * 1024

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,47}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DATA_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CanvasValidationError(ValueError):
    """A canvas name, path, file, or manifest broke the canvas contract."""


def validate_canvas_name(name: str) -> str:
    """Return ``name`` if it is a valid canvas name, else raise."""

    if not _NAME_RE.match(name or ""):
        raise CanvasValidationError(
            f"Invalid canvas name {name!r}: use 2-48 chars of a-z, 0-9 and '-', "
            "starting with a letter."
        )
    return name


def validate_canvas_path(path: str) -> str:
    """Return a canvas-relative file path if valid, else raise.

    Paths are relative POSIX paths with no ``..`` and an allowed extension.
    """

    if not path or path.startswith("/") or "\\" in path:
        raise CanvasValidationError(f"Invalid canvas file path {path!r}: must be relative.")
    segments = path.split("/")
    for segment in segments:
        if not _SEGMENT_RE.match(segment):
            raise CanvasValidationError(
                f"Invalid canvas file path {path!r}: segment {segment!r} must start with a "
                "letter or digit and contain only letters, digits, '.', '_' and '-'."
            )
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in CANVAS_FILE_EXTENSIONS:
        allowed = ", ".join(sorted(CANVAS_FILE_EXTENSIONS))
        raise CanvasValidationError(
            f"Unsupported canvas file type {suffix or '(none)'!r} for {path!r}; allowed: {allowed}."
        )
    return path


def canvas_file_key(name: str, path: str) -> str:
    """Store key of one canvas file: ``canvas:<name>/<path>``."""

    return f"{CANVAS_DOCUMENT_KEY_PREFIX}{name}/{path}"


def split_canvas_file_key(key: str) -> tuple[str, str] | None:
    """Return ``(name, path)`` for a ``canvas:<name>/<path>`` key, else ``None``."""

    if not key.startswith(CANVAS_DOCUMENT_KEY_PREFIX):
        return None
    name, sep, path = key.removeprefix(CANVAS_DOCUMENT_KEY_PREFIX).partition("/")
    if not sep or not name or not path:
        return None
    return name, path


def canvas_data_key(name: str, key: str) -> str:
    """Store key of one canvas data entry: ``canvas_data:<name>/<key>``."""

    return f"{CANVAS_DATA_KEY_PREFIX}{name}/{key}"


def canvas_status_key(name: str) -> str:
    """Store key of a canvas's last host-reported load status."""

    return f"{CANVAS_STATUS_KEY_PREFIX}{name}"


def validate_canvas_data_key(key: str) -> str:
    """Return a canvas data key if valid, else raise."""

    if not _DATA_KEY_RE.match(key or ""):
        raise CanvasValidationError(
            f"Invalid canvas data key {key!r}: use 1-128 chars of letters, digits, '.', '_' "
            "and '-', starting with a letter or digit."
        )
    return key


def canvas_revision(files: Mapping[str, str]) -> str:
    """Content revision of a canvas: first 12 hex of sha256 over its sorted files."""

    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(files[path].encode())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


@lru_cache(maxsize=1)
def load_canvas_libraries() -> dict[str, Any]:
    """Load the vendored library catalogue (``data/canvases/libraries.json``)."""

    path = resolve_data_dir() / "canvases" / "libraries.json"
    return json.loads(path.read_text(encoding="utf-8"))


def validate_canvas_manifest(name: str, content: str) -> dict[str, Any]:
    """Parse and validate ``manifest.json`` text for canvas ``name``; return it."""

    try:
        manifest = json.loads(content)
    except json.JSONDecodeError as exc:
        raise CanvasValidationError(f"manifest.json is not valid JSON: {exc}") from exc
    error = validate_against_schema(manifest, load_schema("canvas_manifest"))
    if error:
        raise CanvasValidationError(f"manifest.json is invalid: {error}")
    if manifest["name"] != name:
        raise CanvasValidationError(
            f"manifest.json name {manifest['name']!r} must equal the canvas directory {name!r}."
        )
    known = {library["id"] for library in load_canvas_libraries()["libraries"]}
    unknown = sorted(set(manifest.get("libraries", [])) - known)
    if unknown:
        raise CanvasValidationError(
            f"Unknown libraries {unknown}; available: {sorted(known)} "
            "(see /canvases/_catalogue.json)."
        )
    return manifest


def validate_canvas_file(
    name: str,
    path: str,
    content: str,
    existing: Mapping[str, str],
) -> None:
    """Validate writing ``content`` to ``path`` given the canvas's other files.

    ``existing`` maps the canvas's current paths to their content (the file being
    written may be among them). Raises :class:`CanvasValidationError`.
    """

    validate_canvas_name(name)
    validate_canvas_path(path)
    size = len(content.encode())
    if size > MAX_CANVAS_FILE_BYTES:
        raise CanvasValidationError(
            f"{path} is {size} bytes; the per-file limit is {MAX_CANVAS_FILE_BYTES}."
        )
    others = {other: text for other, text in existing.items() if other != path}
    if len(others) + 1 > MAX_CANVAS_FILES:
        raise CanvasValidationError(f"A canvas may hold at most {MAX_CANVAS_FILES} files.")
    total = size + sum(len(text.encode()) for text in others.values())
    if total > MAX_CANVAS_BYTES:
        raise CanvasValidationError(
            f"Canvas would be {total} bytes; the per-canvas limit is {MAX_CANVAS_BYTES}."
        )
    if path == CANVAS_MANIFEST_FILE:
        validate_canvas_manifest(name, content)
    elif CANVAS_MANIFEST_FILE not in existing:
        raise CanvasValidationError(
            f"Write /canvases/{name}/{CANVAS_MANIFEST_FILE} before the canvas's other files."
        )
