"""Values capabilities."""

from __future__ import annotations

import json
from typing import Any

JsonDoc = dict[str, Any] | list[Any]


def make_json_safe(value: Any) -> Any:
    """Return a JSON-serializable representation of ``value``."""
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)
