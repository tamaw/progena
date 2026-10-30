"""Canonical lifecycle vocabulary for native routine executions.

The shared JSON source preserves routine lifecycle wire values across the
backend and browser. A paused attempt is terminal but resumable through its
approval continuation; ``interrupted`` remains a derived status, not one
persisted by the lifecycle.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import TypeAlias


class RoutineRunStatus(StrEnum):
    """Persisted lifecycle states for one native routine execution attempt."""

    ENQUEUED = "enqueued"
    STARTED = "started"
    PAUSED = "paused"
    STOPPING = "stopping"
    CANCELLING = "cancelling"
    STOPPED = "stopped"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


RoutineRunStatusValue: TypeAlias = RoutineRunStatus | str

_VOCABULARY_PATH = Path(__file__).resolve().parents[2] / "shared" / "ui_event_vocabulary.json"
with _VOCABULARY_PATH.open(encoding="utf-8") as _vocabulary_file:
    _STATUS_VOCABULARY: dict[str, list[str]] = json.load(_vocabulary_file)["routine_run_statuses"]


def _status_group(name: str) -> frozenset[RoutineRunStatus]:
    return frozenset(RoutineRunStatus(value) for value in _STATUS_VOCABULARY[name])


if {status.value for status in RoutineRunStatus} != set(_STATUS_VOCABULARY["VALUES"]):
    raise RuntimeError("routine lifecycle enum does not match shared vocabulary")


ROUTINE_RUN_ACTIVE_STATUSES = _status_group("ACTIVE")
ROUTINE_RUN_EXECUTING_STATUSES = _status_group("CONTROL_TARGET")
ROUTINE_RUN_CONTROL_PENDING_STATUSES = _status_group("CONTROL_PENDING")
ROUTINE_RUN_CONTROL_STATUSES = _status_group("CONTROL")
ROUTINE_RUN_TERMINAL_STATUSES = _status_group("TERMINAL")
# Paused attempts are terminal at the lifecycle layer but are admitted only by
# the approval continuation; generic control admission retains the prior
# stopped/cancelled/completed/failed membership.
ROUTINE_RUN_CONTROL_TERMINAL_STATUSES = ROUTINE_RUN_TERMINAL_STATUSES - {RoutineRunStatus.PAUSED}
ROUTINE_RUN_RESUMABLE_STATUSES = _status_group("RESUMABLE")


def routine_run_status(value: object) -> RoutineRunStatus | None:
    """Return a known routine lifecycle status without accepting other domains."""
    try:
        return RoutineRunStatus(value)
    except (TypeError, ValueError):
        return None


def is_routine_run_active(value: object) -> bool:
    """Whether a lifecycle state represents a not-yet-settled routine attempt."""
    return routine_run_status(value) in ROUTINE_RUN_ACTIVE_STATUSES


def is_routine_run_terminal(value: object) -> bool:
    """Whether a lifecycle state settles one routine execution attempt."""
    return routine_run_status(value) in ROUTINE_RUN_TERMINAL_STATUSES


def is_routine_run_resumable(value: object) -> bool:
    """Whether a settled routine state has a supported continuation path."""
    return routine_run_status(value) in ROUTINE_RUN_RESUMABLE_STATUSES
