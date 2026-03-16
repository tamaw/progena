"""Storage layer for the Gym Agent.

Primary backend: SQLite (file ``gym_agent.db`` in the repository root).
Fallback backend: JSON file at ``data/gym_agent.json``.

The active backend is selected once at import time via :func:`init_db`.
To override the database path (e.g. for testing with ``:memory:``), set the
``GYM_AGENT_DB`` environment variable *before* calling :func:`init_db`.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = str(Path(__file__).resolve().parents[2] / "gym_agent.db")
_FALLBACK_JSON = str(Path(__file__).resolve().parents[2] / "data" / "gym_agent.json")

_db_path: str = os.environ.get("GYM_AGENT_DB", _DEFAULT_DB_PATH)
_use_json_fallback: bool = False
_memory_conn: sqlite3.Connection | None = None  # reused for :memory: databases


def _get_db_path() -> str:
    """Return the active DB path (can be overridden at runtime for tests)."""
    return os.environ.get("GYM_AGENT_DB", _db_path)


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    global _memory_conn
    path = _get_db_path()
    if path == ":memory:":
        # Reuse the same connection so all callers share the same in-memory DB.
        if _memory_conn is None:
            _memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            _memory_conn.row_factory = sqlite3.Row
            _memory_conn.execute("PRAGMA journal_mode=WAL")
        return _memory_conn
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Initialise the storage backend.

    Creates the SQLite schema if it does not exist.  Falls back to JSON if
    SQLite cannot be initialised.  When ``GYM_AGENT_DB`` is ``:memory:``,
    resets the shared in-memory connection so tests start with a clean slate.
    """
    global _use_json_fallback, _memory_conn
    if _get_db_path() == ":memory:":
        # Drop and recreate the shared in-memory connection for a clean state.
        if _memory_conn is not None:
            _memory_conn.close()
        _memory_conn = None  # _connect() will create a fresh one
    try:
        conn = _connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workouts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                date        TEXT    NOT NULL,
                type        TEXT    NOT NULL,
                duration_min INTEGER,
                distance_km REAL,
                notes       TEXT,
                created_at  TEXT    DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_date ON workouts(user_id, date)")
        conn.commit()
        logger.info("gym_agent: SQLite backend ready at %s", _get_db_path())
    except Exception as exc:  # pragma: no cover
        logger.warning("gym_agent: SQLite unavailable (%s), falling back to JSON", exc)
        _use_json_fallback = True
        _ensure_json_store()


# ---------------------------------------------------------------------------
# JSON fallback helpers
# ---------------------------------------------------------------------------

def _ensure_json_store() -> None:
    """Create the JSON store file if it does not exist."""
    path = Path(_FALLBACK_JSON)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps({"workouts": []}))


def _load_json() -> dict[str, Any]:
    _ensure_json_store()
    return json.loads(Path(_FALLBACK_JSON).read_text())


def _save_json(data: dict[str, Any]) -> None:
    Path(_FALLBACK_JSON).write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_workout(user_id: str, workout_date: str, workout: dict[str, Any]) -> int:
    """Persist a workout entry and return its ID.

    Args:
        user_id: Identifier for the user.
        workout_date: ISO date string (``YYYY-MM-DD``).
        workout: Dict with keys ``type``, ``duration_min``, ``distance_km``, ``notes``.

    Returns:
        The integer ID of the saved record.
    """
    if _use_json_fallback:
        return _json_save_workout(user_id, workout_date, workout)

    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO workouts (user_id, date, type, duration_min, distance_km, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                workout_date,
                workout.get("type", ""),
                workout.get("duration_min"),
                workout.get("distance_km"),
                workout.get("notes"),
            ),
        )
        return cur.lastrowid  # type: ignore[return-value]


def get_history(
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Retrieve workout history for a user.

    Args:
        user_id: Identifier for the user.
        start_date: Optional ISO date string lower bound (inclusive).
        end_date: Optional ISO date string upper bound (inclusive).
        limit: Maximum number of records to return.

    Returns:
        List of workout dicts ordered by date descending.
    """
    if _use_json_fallback:
        return _json_get_history(user_id, start_date, end_date, limit)

    clauses = ["user_id = ?"]
    params: list[Any] = [user_id]
    if start_date:
        clauses.append("date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("date <= ?")
        params.append(end_date)
    params.append(limit)

    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM workouts WHERE {' AND '.join(clauses)} ORDER BY date DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_summary(user_id: str, days: int) -> dict[str, Any]:
    """Return aggregate statistics for a user over the last *days* days.

    Args:
        user_id: Identifier for the user.
        days: Number of past days to include (e.g. 30).

    Returns:
        Dict with keys ``total_workouts``, ``total_duration_min``,
        ``avg_duration_min``, ``total_distance_km``, ``start_date``, ``end_date``.
    """
    if _use_json_fallback:
        return _json_get_summary(user_id, days)

    start = (date.today() - timedelta(days=days - 1)).isoformat()
    end = date.today().isoformat()

    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*)                      AS total_workouts,
                COALESCE(SUM(duration_min),0) AS total_duration_min,
                COALESCE(AVG(duration_min),0) AS avg_duration_min,
                COALESCE(SUM(distance_km),0)  AS total_distance_km
            FROM workouts
            WHERE user_id = ? AND date >= ? AND date <= ?
            """,
            (user_id, start, end),
        ).fetchone()

    return {
        "total_workouts": row["total_workouts"],
        "total_duration_min": row["total_duration_min"],
        "avg_duration_min": round(row["avg_duration_min"], 2),
        "total_distance_km": round(row["total_distance_km"], 2),
        "start_date": start,
        "end_date": end,
    }


def get_habit_streak(user_id: str, habit: str, min_days: int = 1) -> dict[str, int]:
    """Calculate current and longest streak for a given workout habit.

    A *streak* is a consecutive run of days (with at most ``min_days - 1`` gap)
    on which the user performed the specified habit workout type.

    Args:
        user_id: Identifier for the user.
        habit: Workout type to track (e.g. ``"running"``).
        min_days: Minimum number of days between two workout dates to still
                  count as consecutive (default 1 = adjacent days only).

    Returns:
        Dict with keys ``current_streak`` and ``longest_streak``.
    """
    if _use_json_fallback:
        return _json_get_habit_streak(user_id, habit, min_days)

    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM workouts WHERE user_id=? AND type=? ORDER BY date ASC",
            (user_id, habit),
        ).fetchall()

    return _compute_streaks([r["date"] for r in rows], min_days)


# ---------------------------------------------------------------------------
# Streak computation (shared between backends)
# ---------------------------------------------------------------------------

def _compute_streaks(dates: list[str], min_days: int) -> dict[str, int]:
    """Compute current and longest streak from a sorted list of ISO date strings."""
    if not dates:
        return {"current_streak": 0, "longest_streak": 0}

    parsed = sorted(date.fromisoformat(d) for d in dates)
    today = date.today()

    current = longest = 1
    run = 1

    for i in range(1, len(parsed)):
        gap = (parsed[i] - parsed[i - 1]).days
        if gap <= min_days:
            run += 1
        else:
            run = 1
        longest = max(longest, run)

    # Check if streak is still active (last workout within min_days of today)
    if (today - parsed[-1]).days <= min_days:
        current = run
    else:
        current = 0

    return {"current_streak": current, "longest_streak": longest}


# ---------------------------------------------------------------------------
# JSON fallback implementations
# ---------------------------------------------------------------------------

def _next_json_id(workouts: list[dict]) -> int:
    return max((w.get("id", 0) for w in workouts), default=0) + 1


def _json_save_workout(user_id: str, workout_date: str, workout: dict[str, Any]) -> int:
    data = _load_json()
    new_id = _next_json_id(data["workouts"])
    data["workouts"].append(
        {
            "id": new_id,
            "user_id": user_id,
            "date": workout_date,
            "type": workout.get("type", ""),
            "duration_min": workout.get("duration_min"),
            "distance_km": workout.get("distance_km"),
            "notes": workout.get("notes"),
        }
    )
    _save_json(data)
    return new_id


def _json_get_history(
    user_id: str,
    start_date: str | None,
    end_date: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    data = _load_json()
    rows = [w for w in data["workouts"] if w["user_id"] == user_id]
    if start_date:
        rows = [w for w in rows if w["date"] >= start_date]
    if end_date:
        rows = [w for w in rows if w["date"] <= end_date]
    rows.sort(key=lambda w: w["date"], reverse=True)
    return rows[:limit]


def _json_get_summary(user_id: str, days: int) -> dict[str, Any]:
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    end = date.today().isoformat()
    data = _load_json()
    rows = [
        w
        for w in data["workouts"]
        if w["user_id"] == user_id and start <= w["date"] <= end
    ]
    total = len(rows)
    total_dur = sum(w["duration_min"] or 0 for w in rows)
    total_dist = sum(w["distance_km"] or 0.0 for w in rows)
    avg_dur = round(total_dur / total, 2) if total else 0.0
    return {
        "total_workouts": total,
        "total_duration_min": total_dur,
        "avg_duration_min": avg_dur,
        "total_distance_km": round(total_dist, 2),
        "start_date": start,
        "end_date": end,
    }


def _json_get_habit_streak(user_id: str, habit: str, min_days: int) -> dict[str, int]:
    data = _load_json()
    dates = sorted(
        {w["date"] for w in data["workouts"] if w["user_id"] == user_id and w["type"] == habit}
    )
    return _compute_streaks(dates, min_days)
