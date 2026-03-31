"""FastAPI application for the Gym Agent A2A service."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import ValidationError

from . import storage
from .schemas import (
    A2ARequest,
    GetHistoryPayload,
    HabitStreakPayload,
    LogWorkoutPayload,
    SummarizePayload,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:  # pragma: no cover
    storage.init_db()
    yield


app = FastAPI(title="Gym Agent", version="1.0.0", lifespan=_lifespan)

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_storage() -> None:
    """Dependency that ensures the DB is ready (no-op after startup)."""
    return None


# ---------------------------------------------------------------------------
# Agent card
# ---------------------------------------------------------------------------

AGENT_CARD: dict[str, Any] = {
    "name": "gym_agent",
    "version": "1.0.0",
    "description": "A2A-compatible agent for logging and analysing gym workouts.",
    "capabilities": [
        {
            "name": "log_workout",
            "description": "Log a workout for a user on a given date.",
            "payload_schema": "LogWorkoutPayload",
        },
        {
            "name": "get_history",
            "description": "Retrieve workout history for a user with optional date filters.",
            "payload_schema": "GetHistoryPayload",
        },
        {
            "name": "summarize_progress",
            "description": "Return aggregate statistics (totals, averages) over a rolling window.",
            "payload_schema": "SummarizePayload",
        },
        {
            "name": "habit_streak",
            "description": "Calculate current and longest streak for a given workout habit.",
            "payload_schema": "HabitStreakPayload",
        },
    ],
}


@app.get("/agent/card")
def agent_card() -> dict[str, Any]:
    """Return the A2A agent card describing this agent's capabilities."""
    return AGENT_CARD


# ---------------------------------------------------------------------------
# A2A endpoint
# ---------------------------------------------------------------------------


def _make_response(result: Any, context_id: str | None) -> dict[str, Any]:
    resp: dict[str, Any] = {"status": "ok", "result": result}
    if context_id:
        resp["context_id"] = context_id
    return resp


def _make_error(message: str, context_id: str | None) -> dict[str, Any]:
    resp: dict[str, Any] = {"status": "error", "message": message}
    if context_id:
        resp["context_id"] = context_id
    return resp


@app.post("/a2a")
def a2a(
    request: Request,
    body: A2ARequest,
    _storage: None = Depends(get_storage),
) -> dict[str, Any]:
    """A2A dispatch endpoint.

    Accepts an :class:`A2ARequest` envelope, validates the payload against the
    capability-specific schema, executes the requested operation, and returns a
    structured JSON response.

    Optional header ``X-A2A-Context-ID`` is echoed back in the response.
    """
    context_id: str | None = request.headers.get("X-A2A-Context-ID")

    try:
        typed_payload = body.parse_payload()
    except ValidationError as exc:
        logger.warning("Payload validation error for capability %s: %s", body.capability, exc)
        raise HTTPException(status_code=400, detail=_make_error(str(exc), context_id))

    logger.info("A2A capability=%s user=%s", body.capability, getattr(typed_payload, "user_id", "?"))

    try:
        result = _dispatch(body.capability, typed_payload)
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected error in capability %s", body.capability)
        raise HTTPException(status_code=500, detail=_make_error(str(exc), context_id))

    return _make_response(result, context_id)


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


def _dispatch(capability: str, payload: Any) -> Any:
    """Route a validated payload to the appropriate storage call."""
    if capability == "log_workout":
        return _handle_log_workout(payload)
    if capability == "get_history":
        return _handle_get_history(payload)
    if capability == "summarize_progress":
        return _handle_summarize(payload)
    if capability == "habit_streak":
        return _handle_habit_streak(payload)
    raise ValueError(f"Unknown capability: {capability}")  # pragma: no cover


def _handle_log_workout(payload: LogWorkoutPayload) -> dict[str, Any]:
    workout_dict = payload.workout.model_dump(exclude_none=True)
    record_id = storage.save_workout(payload.user_id, payload.date_str(), workout_dict)
    return {"id": record_id, "user_id": payload.user_id, "date": payload.date_str()}


def _handle_get_history(payload: GetHistoryPayload) -> list[dict[str, Any]]:
    return storage.get_history(
        payload.user_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        limit=payload.limit,
    )


def _handle_summarize(payload: SummarizePayload) -> dict[str, Any]:
    return storage.get_summary(payload.user_id, payload.days)


def _handle_habit_streak(payload: HabitStreakPayload) -> dict[str, int]:
    return storage.get_habit_streak(payload.user_id, payload.habit, payload.min_days)
