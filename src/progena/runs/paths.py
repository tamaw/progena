"""Stable mount paths for rendered runs."""

from __future__ import annotations

import re
from datetime import datetime, tzinfo
from typing import Literal

from src.progena.workflows.documents import WORKFLOW_RUN_ID_PREFIX

RunKind = Literal["routine", "workflow", "schedule"]

RUN_DIRECTORIES: dict[RunKind, str] = {
    "routine": "routines",
    "workflow": "workflows",
    "schedule": "schedules",
}
_ID_PREFIXES = (WORKFLOW_RUN_ID_PREFIX, "schedrun_")
_SLUG_CHARS = 40


def slug(title: str) -> str:
    """Lower-case, dash-separated, at most 40 characters (as ``/conversations/`` names)."""
    text = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return text[:_SLUG_CHARS].rstrip("-") or "run"


def short_id(run_id: str) -> str:
    """The first 8 characters of a run id, after its type prefix."""
    for prefix in _ID_PREFIXES:
        if run_id.startswith(prefix):
            run_id = run_id[len(prefix) :]
            break
    return run_id.replace("-", "")[:8]


def run_file_name(*, started_at: datetime, title: str, run_id: str, tz: tzinfo) -> str:
    """``<YYYY-MM-DD>_<slug>_<run_id>.md`` with an unambiguous persisted identity."""
    return f"{started_at.astimezone(tz):%Y-%m-%d}_{slug(title)}_{run_id}.md"


def run_file_path(
    kind: RunKind, *, started_at: datetime, title: str, run_id: str, tz: tzinfo
) -> str:
    """The run's full mount path, including its complete persisted run ID."""
    name = run_file_name(started_at=started_at, title=title, run_id=run_id, tz=tz)
    return f"/runs/{RUN_DIRECTORIES[kind]}/{name}"
