"""Shared domain contracts and helpers."""

from __future__ import annotations

import re

_DIRECT_A2A_RE = re.compile(r"^@(?P<agent>[A-Za-z0-9_][A-Za-z0-9_.-]*)\s+(?P<request>.+?)\s*$")


_DIRECT_A2A_EMPTY_RE = re.compile(r"^@(?P<agent>[A-Za-z0-9_][A-Za-z0-9_.-]*)\s*$")
