"""JSON value equality and dotted-path access for workflow predicates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def json_equal(a: Any, b: Any) -> bool:
    """Equality as JSONB sees it: ``true`` is not ``1``, but ``1`` is ``1.0``."""

    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict):
        return set(a) == set(b) and all(json_equal(a[k], b[k]) for k in a)
    return a == b


def value_at_path(value: Any, path: str) -> Any:
    """Walk a dotted path (``after.column_id``) into nested objects; ``None`` if absent."""

    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def path_exists(value: Any, path: str) -> bool:
    """Whether every segment of a dotted path is present."""

    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return False
        value = value[part]
    return True
