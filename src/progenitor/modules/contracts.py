"""Agent-built UI modules: shared storage contract and validation.

A module is a directory of text files the agent writes at ``/modules/<name>/``
(stored one row per file as ``module:<name>/<path>`` content documents). The
frontend renders it in a sandboxed, network-less iframe under **Modules**; the
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
    MODULE_DATA_KEY_PREFIX,
    MODULE_DOCUMENT_KEY_PREFIX,
    MODULE_STATUS_KEY_PREFIX,
)
from src.progenitor.instructions.paths import resolve_data_dir
from src.progenitor.llm.structured import validate_against_schema

__all__ = [
    "MAX_MODULE_BYTES",
    "MAX_MODULE_DATA_BYTES",
    "MAX_MODULE_FILES",
    "MAX_MODULE_FILE_BYTES",
    "MODULE_DATA_KEY_PREFIX",
    "MODULE_DOCUMENT_KEY_PREFIX",
    "MODULE_FILE_EXTENSIONS",
    "MODULE_MANIFEST_FILE",
    "MODULE_STATUS_KEY_PREFIX",
    "ModuleValidationError",
    "load_module_libraries",
    "module_data_key",
    "module_file_key",
    "module_revision",
    "module_status_key",
    "split_module_file_key",
    "validate_module_data_key",
    "validate_module_file",
    "validate_module_manifest",
    "validate_module_name",
    "validate_module_path",
]

MODULE_MANIFEST_FILE = "manifest.json"
MODULE_FILE_EXTENSIONS = frozenset({".mjs", ".css", ".json", ".svg", ".md", ".txt"})
MAX_MODULE_FILE_BYTES = 256 * 1024
MAX_MODULE_BYTES = 1024 * 1024
MAX_MODULE_FILES = 32
MAX_MODULE_DATA_BYTES = 256 * 1024

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,47}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DATA_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ModuleValidationError(ValueError):
    """A module name, path, file, or manifest broke the module contract."""


def validate_module_name(name: str) -> str:
    """Return ``name`` if it is a valid module name, else raise."""

    if not _NAME_RE.match(name or ""):
        raise ModuleValidationError(
            f"Invalid module name {name!r}: use 2-48 chars of a-z, 0-9 and '-', "
            "starting with a letter."
        )
    return name


def validate_module_path(path: str) -> str:
    """Return a module-relative file path if valid, else raise.

    Paths are relative POSIX paths with no ``..`` and an allowed extension.
    """

    if not path or path.startswith("/") or "\\" in path:
        raise ModuleValidationError(f"Invalid module file path {path!r}: must be relative.")
    segments = path.split("/")
    for segment in segments:
        if not _SEGMENT_RE.match(segment):
            raise ModuleValidationError(
                f"Invalid module file path {path!r}: segment {segment!r} must start with a "
                "letter or digit and contain only letters, digits, '.', '_' and '-'."
            )
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in MODULE_FILE_EXTENSIONS:
        allowed = ", ".join(sorted(MODULE_FILE_EXTENSIONS))
        raise ModuleValidationError(
            f"Unsupported module file type {suffix or '(none)'!r} for {path!r}; allowed: {allowed}."
        )
    return path


def module_file_key(name: str, path: str) -> str:
    """Store key of one module file: ``module:<name>/<path>``."""

    return f"{MODULE_DOCUMENT_KEY_PREFIX}{name}/{path}"


def split_module_file_key(key: str) -> tuple[str, str] | None:
    """Return ``(name, path)`` for a ``module:<name>/<path>`` key, else ``None``."""

    if not key.startswith(MODULE_DOCUMENT_KEY_PREFIX):
        return None
    name, sep, path = key.removeprefix(MODULE_DOCUMENT_KEY_PREFIX).partition("/")
    if not sep or not name or not path:
        return None
    return name, path


def module_data_key(name: str, key: str) -> str:
    """Store key of one module data entry: ``module_data:<name>/<key>``."""

    return f"{MODULE_DATA_KEY_PREFIX}{name}/{key}"


def module_status_key(name: str) -> str:
    """Store key of a module's last host-reported load status."""

    return f"{MODULE_STATUS_KEY_PREFIX}{name}"


def validate_module_data_key(key: str) -> str:
    """Return a module data key if valid, else raise."""

    if not _DATA_KEY_RE.match(key or ""):
        raise ModuleValidationError(
            f"Invalid module data key {key!r}: use 1-128 chars of letters, digits, '.', '_' "
            "and '-', starting with a letter or digit."
        )
    return key


def module_revision(files: Mapping[str, str]) -> str:
    """Content revision of a module: first 12 hex of sha256 over its sorted files."""

    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(files[path].encode())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


@lru_cache(maxsize=1)
def load_module_libraries() -> dict[str, Any]:
    """Load the vendored library catalogue (``data/modules/libraries.json``)."""

    path = resolve_data_dir() / "modules" / "libraries.json"
    return json.loads(path.read_text(encoding="utf-8"))


def validate_module_manifest(name: str, content: str) -> dict[str, Any]:
    """Parse and validate ``manifest.json`` text for module ``name``; return it."""

    try:
        manifest = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ModuleValidationError(f"manifest.json is not valid JSON: {exc}") from exc
    error = validate_against_schema(manifest, load_schema("module_manifest"))
    if error:
        raise ModuleValidationError(f"manifest.json is invalid: {error}")
    if manifest["name"] != name:
        raise ModuleValidationError(
            f"manifest.json name {manifest['name']!r} must equal the module directory {name!r}."
        )
    known = {library["id"] for library in load_module_libraries()["libraries"]}
    unknown = sorted(set(manifest.get("libraries", [])) - known)
    if unknown:
        raise ModuleValidationError(
            f"Unknown libraries {unknown}; available: {sorted(known)} "
            "(see /modules/_catalogue.json)."
        )
    return manifest


def validate_module_file(
    name: str,
    path: str,
    content: str,
    existing: Mapping[str, str],
) -> None:
    """Validate writing ``content`` to ``path`` given the module's other files.

    ``existing`` maps the module's current paths to their content (the file being
    written may be among them). Raises :class:`ModuleValidationError`.
    """

    validate_module_name(name)
    validate_module_path(path)
    size = len(content.encode())
    if size > MAX_MODULE_FILE_BYTES:
        raise ModuleValidationError(
            f"{path} is {size} bytes; the per-file limit is {MAX_MODULE_FILE_BYTES}."
        )
    others = {other: text for other, text in existing.items() if other != path}
    if len(others) + 1 > MAX_MODULE_FILES:
        raise ModuleValidationError(f"A module may hold at most {MAX_MODULE_FILES} files.")
    total = size + sum(len(text.encode()) for text in others.values())
    if total > MAX_MODULE_BYTES:
        raise ModuleValidationError(
            f"Module would be {total} bytes; the per-module limit is {MAX_MODULE_BYTES}."
        )
    if path == MODULE_MANIFEST_FILE:
        validate_module_manifest(name, content)
    elif MODULE_MANIFEST_FILE not in existing:
        raise ModuleValidationError(
            f"Write /modules/{name}/{MODULE_MANIFEST_FILE} before the module's other files."
        )
