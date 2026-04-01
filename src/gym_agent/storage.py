"""SQLite storage layer for the Gym Agent."""

from __future__ import annotations

import logging
import os
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = str(Path(__file__).resolve().parents[2] / "gym_agent.db")

_db_path: str = os.environ.get("GYM_AGENT_DB", _DEFAULT_DB_PATH)
_memory_conn: sqlite3.Connection | None = None


def _round_kg(value: float) -> float:
    """Round weight/volume values to two decimals."""
    return round(value, 2)


def _require_lastrowid(lastrowid: int | None, entity_name: str) -> int:
    """Return a SQLite row id or raise when insertion did not produce one."""
    if lastrowid is None:  # pragma: no cover - sqlite should always provide this
        raise RuntimeError(f"Failed to persist {entity_name}")
    return int(lastrowid)


def _get_db_path() -> str:
    """Return the active SQLite database path."""
    return os.environ.get("GYM_AGENT_DB", _db_path)


def _connect() -> sqlite3.Connection:
    """Create or reuse a SQLite connection."""
    global _memory_conn

    path = _get_db_path()
    if path == ":memory:":
        if _memory_conn is None:
            _memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            _memory_conn.row_factory = sqlite3.Row
            _memory_conn.execute("PRAGMA foreign_keys = ON")
        return _memory_conn

    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """Yield a managed SQLite connection and close non-memory connections."""
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if connection is not _memory_conn:
            connection.close()


def init_db() -> None:
    """Initialise the lifting/bodyweight schema."""
    global _memory_conn

    if _get_db_path() == ":memory:" and _memory_conn is not None:
        _memory_conn.close()
        _memory_conn = None

    with _connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS lifting_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                date TEXT NOT NULL,
                workout_name TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS lifting_exercises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                exercise_order INTEGER NOT NULL,
                name TEXT NOT NULL,
                notes TEXT,
                FOREIGN KEY(session_id) REFERENCES lifting_sessions(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS lifting_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exercise_id INTEGER NOT NULL,
                set_order INTEGER NOT NULL,
                reps INTEGER NOT NULL,
                weight_kg REAL NOT NULL,
                set_type TEXT NOT NULL,
                rpe REAL,
                rir INTEGER,
                notes TEXT,
                FOREIGN KEY(exercise_id) REFERENCES lifting_exercises(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bodyweight_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                date TEXT NOT NULL,
                weight_kg REAL NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_lifting_sessions_user_date "
            "ON lifting_sessions(user_id, date DESC, id DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_lifting_exercises_session_order "
            "ON lifting_exercises(session_id, exercise_order)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_lifting_exercises_name "
            "ON lifting_exercises(name)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_lifting_sets_exercise_order "
            "ON lifting_sets(exercise_id, set_order)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_bodyweight_entries_user_date "
            "ON bodyweight_entries(user_id, date DESC, id DESC)"
        )

    logger.info("gym_agent: SQLite backend ready at %s", _get_db_path())


def save_lifting_session(
    user_id: str,
    session_date: str,
    workout_name: str,
    exercises: list[dict[str, Any]],
    notes: str | None = None,
) -> int:
    """Persist a lifting session with nested exercises and sets."""
    with _connection() as connection:
        session_cursor = connection.execute(
            """
            INSERT INTO lifting_sessions (user_id, date, workout_name, notes)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, session_date, workout_name, notes),
        )
        session_id = _require_lastrowid(session_cursor.lastrowid, "lifting session")

        for exercise_order, exercise in enumerate(exercises, start=1):
            exercise_cursor = connection.execute(
                """
                INSERT INTO lifting_exercises (session_id, exercise_order, name, notes)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, exercise_order, exercise["name"], exercise.get("notes")),
            )
            exercise_id = _require_lastrowid(exercise_cursor.lastrowid, "lifting exercise")
            for set_order, lifting_set in enumerate(exercise["sets"], start=1):
                connection.execute(
                    """
                    INSERT INTO lifting_sets (
                        exercise_id,
                        set_order,
                        reps,
                        weight_kg,
                        set_type,
                        rpe,
                        rir,
                        notes
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        exercise_id,
                        set_order,
                        lifting_set["reps"],
                        lifting_set["weight_kg"],
                        lifting_set["set_type"],
                        lifting_set.get("rpe"),
                        lifting_set.get("rir"),
                        lifting_set.get("notes"),
                    ),
                )

    return session_id


def get_lifting_history(
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    exercise_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return lifting sessions with nested exercises and sets."""
    clauses = ["s.user_id = ?"]
    params: list[Any] = [user_id]
    if start_date:
        clauses.append("s.date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("s.date <= ?")
        params.append(end_date)
    if exercise_name:
        clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM lifting_exercises filter_exercise
                WHERE filter_exercise.session_id = s.id
                AND LOWER(filter_exercise.name) = LOWER(?)
            )
            """
        )
        params.append(exercise_name)
    params.append(limit)

    with _connection() as connection:
        session_rows = connection.execute(
            f"""
            SELECT s.id, s.user_id, s.date, s.workout_name, s.notes, s.created_at
            FROM lifting_sessions s
            WHERE {' AND '.join(clauses)}
            ORDER BY s.date DESC, s.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        sessions = [_build_session(connection, session_row["id"]) for session_row in session_rows]

    return sessions


def summarize_strength_progress(
    user_id: str,
    days: int,
    exercise_name: str | None = None,
) -> dict[str, Any]:
    """Summarize lifting progress over a rolling time window."""
    start_date = (date.today() - timedelta(days=days - 1)).isoformat()
    end_date = date.today().isoformat()

    clauses = ["s.user_id = ?", "s.date >= ?", "s.date <= ?"]
    params: list[Any] = [user_id, start_date, end_date]
    if exercise_name:
        clauses.append("LOWER(e.name) = LOWER(?)")
        params.append(exercise_name)

    with _connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                s.id AS session_id,
                s.date AS session_date,
                e.name AS exercise_name,
                sets.reps AS reps,
                sets.weight_kg AS weight_kg
            FROM lifting_sessions s
            JOIN lifting_exercises e ON e.session_id = s.id
            JOIN lifting_sets sets ON sets.exercise_id = e.id
            WHERE {' AND '.join(clauses)}
            ORDER BY s.date DESC, s.id DESC, e.exercise_order ASC, sets.set_order ASC
            """,
            params,
        ).fetchall()

    if not rows:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "exercise_name": exercise_name,
            "total_sessions": 0,
            "distinct_exercises": 0,
            "total_sets": 0,
            "total_reps": 0,
            "total_volume_kg": 0.0,
            "avg_session_volume_kg": 0.0,
            "top_set_weight_kg": 0.0,
            "latest_session_date": None,
            "exercise_summaries": [],
        }

    session_ids = {int(row["session_id"]) for row in rows}
    exercise_summaries: dict[str, dict[str, Any]] = {}
    total_reps = 0
    total_volume = 0.0
    top_set_weight = 0.0
    latest_session_date = max(str(row["session_date"]) for row in rows)

    for row in rows:
        exercise_key = str(row["exercise_name"])
        reps = int(row["reps"])
        weight_kg = float(row["weight_kg"])
        volume_kg = reps * weight_kg
        total_reps += reps
        total_volume += volume_kg
        top_set_weight = max(top_set_weight, weight_kg)

        summary = exercise_summaries.setdefault(
            exercise_key,
            {
                "exercise_name": exercise_key,
                "total_sets": 0,
                "total_reps": 0,
                "total_volume_kg": 0.0,
                "top_set_weight_kg": 0.0,
            },
        )
        summary["total_sets"] += 1
        summary["total_reps"] += reps
        summary["total_volume_kg"] += volume_kg
        summary["top_set_weight_kg"] = max(float(summary["top_set_weight_kg"]), weight_kg)

    ordered_summaries = []
    for summary in sorted(exercise_summaries.values(), key=lambda item: item["exercise_name"]):
        ordered_summaries.append(
            {
                **summary,
                "total_volume_kg": _round_kg(float(summary["total_volume_kg"])),
                "top_set_weight_kg": _round_kg(float(summary["top_set_weight_kg"])),
            }
        )

    return {
        "start_date": start_date,
        "end_date": end_date,
        "exercise_name": exercise_name,
        "total_sessions": len(session_ids),
        "distinct_exercises": len(exercise_summaries),
        "total_sets": len(rows),
        "total_reps": total_reps,
        "total_volume_kg": _round_kg(total_volume),
        "avg_session_volume_kg": _round_kg(total_volume / len(session_ids)),
        "top_set_weight_kg": _round_kg(top_set_weight),
        "latest_session_date": latest_session_date,
        "exercise_summaries": ordered_summaries,
    }


def get_personal_records(
    user_id: str,
    exercise_name: str | None = None,
) -> dict[str, Any]:
    """Return heaviest and highest-volume set records for each exercise."""
    clauses = ["s.user_id = ?"]
    params: list[Any] = [user_id]
    if exercise_name:
        clauses.append("LOWER(e.name) = LOWER(?)")
        params.append(exercise_name)

    with _connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                e.name AS exercise_name,
                s.date AS session_date,
                sets.reps AS reps,
                sets.weight_kg AS weight_kg
            FROM lifting_sessions s
            JOIN lifting_exercises e ON e.session_id = s.id
            JOIN lifting_sets sets ON sets.exercise_id = e.id
            WHERE {' AND '.join(clauses)}
            ORDER BY e.name ASC, s.date ASC, sets.set_order ASC
            """,
            params,
        ).fetchall()

    records_by_exercise: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        records_by_exercise[str(row["exercise_name"])].append(row)

    records = []
    for exercise, exercise_rows in sorted(records_by_exercise.items()):
        heaviest_row = max(exercise_rows, key=lambda row: (float(row["weight_kg"]), int(row["reps"])))
        highest_volume_row = max(
            exercise_rows,
            key=lambda row: (
                float(row["weight_kg"]) * int(row["reps"]),
                int(row["reps"]),
                float(row["weight_kg"]),
            ),
        )
        records.append(
            {
                "exercise_name": exercise,
                "heaviest_set": {
                    "weight_kg": _round_kg(float(heaviest_row["weight_kg"])),
                    "reps": int(heaviest_row["reps"]),
                    "achieved_on": str(heaviest_row["session_date"]),
                },
                "highest_volume_set": {
                    "weight_kg": _round_kg(float(highest_volume_row["weight_kg"])),
                    "reps": int(highest_volume_row["reps"]),
                    "volume_kg": _round_kg(
                        float(highest_volume_row["weight_kg"]) * int(highest_volume_row["reps"])
                    ),
                    "achieved_on": str(highest_volume_row["session_date"]),
                },
            }
        )

    return {
        "user_id": user_id,
        "exercise_name": exercise_name,
        "records": records,
    }


def save_bodyweight(
    user_id: str,
    entry_date: str,
    weight_kg: float,
    notes: str | None = None,
) -> int:
    """Persist a bodyweight entry."""
    with _connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO bodyweight_entries (user_id, date, weight_kg, notes)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, entry_date, weight_kg, notes),
        )
        return _require_lastrowid(cursor.lastrowid, "bodyweight entry")


def get_bodyweight_history(
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return bodyweight history ordered newest-first."""
    clauses = ["user_id = ?"]
    params: list[Any] = [user_id]
    if start_date:
        clauses.append("date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("date <= ?")
        params.append(end_date)
    params.append(limit)

    with _connection() as connection:
        rows = connection.execute(
            f"""
            SELECT id, user_id, date, weight_kg, notes, created_at
            FROM bodyweight_entries
            WHERE {' AND '.join(clauses)}
            ORDER BY date DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    return [
        {
            "id": int(row["id"]),
            "user_id": str(row["user_id"]),
            "date": str(row["date"]),
            "weight_kg": _round_kg(float(row["weight_kg"])),
            "notes": row["notes"],
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


def summarize_bodyweight_progress(user_id: str, days: int) -> dict[str, Any]:
    """Summarize bodyweight change over a rolling time window."""
    start_date = (date.today() - timedelta(days=days - 1)).isoformat()
    end_date = date.today().isoformat()

    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT id, date, weight_kg
            FROM bodyweight_entries
            WHERE user_id = ? AND date >= ? AND date <= ?
            ORDER BY date ASC, id ASC
            """,
            (user_id, start_date, end_date),
        ).fetchall()

    if not rows:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "entry_count": 0,
            "starting_weight_kg": None,
            "latest_weight_kg": None,
            "change_kg": None,
            "min_weight_kg": None,
            "max_weight_kg": None,
            "avg_weight_kg": None,
        }

    weights = [float(row["weight_kg"]) for row in rows]
    starting_weight = weights[0]
    latest_weight = weights[-1]
    return {
        "start_date": start_date,
        "end_date": end_date,
        "entry_count": len(rows),
        "starting_weight_kg": _round_kg(starting_weight),
        "latest_weight_kg": _round_kg(latest_weight),
        "change_kg": _round_kg(latest_weight - starting_weight),
        "min_weight_kg": _round_kg(min(weights)),
        "max_weight_kg": _round_kg(max(weights)),
        "avg_weight_kg": _round_kg(sum(weights) / len(weights)),
    }


def _build_session(connection: sqlite3.Connection, session_id: int) -> dict[str, Any]:
    """Hydrate a session row into a nested response payload."""
    session_row = connection.execute(
        """
        SELECT id, user_id, date, workout_name, notes, created_at
        FROM lifting_sessions
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    if session_row is None:  # pragma: no cover - defensive guard
        raise ValueError(f"Unknown session id: {session_id}")

    exercise_rows = connection.execute(
        """
        SELECT id, exercise_order, name, notes
        FROM lifting_exercises
        WHERE session_id = ?
        ORDER BY exercise_order ASC, id ASC
        """,
        (session_id,),
    ).fetchall()

    exercises: list[dict[str, Any]] = []
    total_sets = 0
    total_reps = 0
    total_volume = 0.0

    for exercise_row in exercise_rows:
        set_rows = connection.execute(
            """
            SELECT id, set_order, reps, weight_kg, set_type, rpe, rir, notes
            FROM lifting_sets
            WHERE exercise_id = ?
            ORDER BY set_order ASC, id ASC
            """,
            (exercise_row["id"],),
        ).fetchall()
        exercise_sets = []
        exercise_reps = 0
        exercise_volume = 0.0
        for set_row in set_rows:
            reps = int(set_row["reps"])
            weight_kg = float(set_row["weight_kg"])
            volume_kg = reps * weight_kg
            exercise_reps += reps
            exercise_volume += volume_kg
            exercise_sets.append(
                {
                    "id": int(set_row["id"]),
                    "set_order": int(set_row["set_order"]),
                    "reps": reps,
                    "weight_kg": _round_kg(weight_kg),
                    "set_type": str(set_row["set_type"]),
                    "rpe": None if set_row["rpe"] is None else float(set_row["rpe"]),
                    "rir": None if set_row["rir"] is None else int(set_row["rir"]),
                    "notes": set_row["notes"],
                    "volume_kg": _round_kg(volume_kg),
                }
            )

        total_sets += len(exercise_sets)
        total_reps += exercise_reps
        total_volume += exercise_volume
        exercises.append(
            {
                "id": int(exercise_row["id"]),
                "exercise_order": int(exercise_row["exercise_order"]),
                "name": str(exercise_row["name"]),
                "notes": exercise_row["notes"],
                "total_sets": len(exercise_sets),
                "total_reps": exercise_reps,
                "total_volume_kg": _round_kg(exercise_volume),
                "sets": exercise_sets,
            }
        )

    return {
        "id": int(session_row["id"]),
        "user_id": str(session_row["user_id"]),
        "date": str(session_row["date"]),
        "workout_name": str(session_row["workout_name"]),
        "notes": session_row["notes"],
        "created_at": str(session_row["created_at"]),
        "exercise_count": len(exercises),
        "total_sets": total_sets,
        "total_reps": total_reps,
        "total_volume_kg": _round_kg(total_volume),
        "exercises": exercises,
    }
