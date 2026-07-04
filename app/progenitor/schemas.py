"""Loading helpers for JSON Schema documents under ``data/schemas/``.

The ``structured_output`` tool drives schema-constrained structured-output LLM
calls from JSON Schema files stored alongside prompts and skills. Schemas are
looked up by filename so the agent can pass a stable, human-authored contract
instead of an inline schema.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .prompts.paths import resolve_data_dir

# Schema files follow the convention ``<name>.schema.v<n>.json`` so a document's
# contract can evolve without breaking older references. A bare logical name
# (``mcp``) resolves to the highest available version.
_VERSIONED_SCHEMA_RE = re.compile(r"^(?P<name>.+)\.schema\.v(?P<version>\d+)\.json$")


def resolve_schemas_dir(schemas_dir: Path | str | None = None) -> Path:
    """Resolve the schema directory from explicit override, env, and default.

    Mirrors :func:`app.progenitor.prompts.paths.resolve_prompts_dir`.
    """
    if schemas_dir is not None:
        return Path(schemas_dir).expanduser().resolve()

    env_schemas_dir = os.getenv("PROGENITOR_SCHEMAS_DIR", "").strip()
    if env_schemas_dir:
        resolved_env_dir = Path(env_schemas_dir).expanduser().resolve()
        if not resolved_env_dir.exists():
            raise FileNotFoundError(
                f"PROGENITOR_SCHEMAS_DIR points to missing directory: {resolved_env_dir}"
            )
        return resolved_env_dir

    resolved_default_dir = resolve_data_dir() / "schemas"
    if not resolved_default_dir.exists():
        raise FileNotFoundError(
            f"Schemas directory not found: {resolved_default_dir}. "
            "Ensure the data/schemas/ directory exists at the project root."
        )
    return resolved_default_dir


def list_schemas(schemas_dir: Path | str | None = None) -> list[str]:
    """Return the available schema filenames (sorted), or empty if none exist."""
    schema_root = resolve_schemas_dir(schemas_dir)
    if not schema_root.is_dir():
        return []
    return sorted(p.name for p in schema_root.glob("*.json") if p.is_file())


def _resolve_latest_version(name: str, schema_root: Path) -> Path | None:
    """Return the highest-versioned ``<name>.schema.v<n>.json`` file, if any."""
    best: tuple[int, Path] | None = None
    for path in schema_root.glob(f"{name}.schema.v*.json"):
        match = _VERSIONED_SCHEMA_RE.match(path.name)
        if match is None or match.group("name") != name:
            continue
        version = int(match.group("version"))
        if best is None or version > best[0]:
            best = (version, path)
    return best[1] if best else None


def resolve_schema_path(filename: str, *, schemas_dir: Path | str | None = None) -> Path:
    """Resolve a schema reference to a concrete file path.

    Accepts either an exact filename (e.g. ``schedule_entry.schema.v1.json``)
    or a bare logical name (``schedule_entry``), which resolves to the highest
    available version. ``filename`` must be a bare name with no path
    separators or ``..`` components — it is an agent-controlled tool
    argument, so this is rejected outright rather than merely contained,
    since it is never legitimate for a schema reference to point outside
    ``schemas_dir``. Raises ``FileNotFoundError`` (listing available schemas)
    when nothing matches.
    """
    if not filename or filename in (".", "..") or "/" in filename or "\\" in filename:
        raise ValueError(f"Schema reference must be a bare filename, got: {filename!r}")

    schema_root = resolve_schemas_dir(schemas_dir)

    if filename.endswith(".json"):
        candidate = schema_root / filename
    else:
        latest = _resolve_latest_version(filename, schema_root)
        candidate = latest if latest is not None else schema_root / f"{filename}.json"

    if not candidate.is_file():
        available = list_schemas(schema_root)
        hint = ", ".join(available) if available else "(none found)"
        raise FileNotFoundError(
            f"Schema not found for '{filename}' (looked for {candidate.name}). "
            f"Available schemas: {hint}"
        )
    return candidate


def load_schema(filename: str, *, schemas_dir: Path | str | None = None) -> dict[str, Any]:
    """Load and parse a JSON Schema file by exact filename or logical name.

    See :func:`resolve_schema_path` for how references are resolved. Raises
    ``ValueError`` when the file is not a JSON object.
    """
    schema_file = resolve_schema_path(filename, schemas_dir=schemas_dir)

    parsed = json.loads(schema_file.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"Schema file {schema_file} must contain a JSON object")
    return parsed
