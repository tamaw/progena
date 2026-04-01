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
    GetBodyweightHistoryPayload,
    GetLiftingHistoryPayload,
    GetPersonalRecordsPayload,
    LogBodyweightPayload,
    LogLiftingSessionPayload,
    SummarizeBodyweightProgressPayload,
    SummarizeStrengthProgressPayload,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:  # pragma: no cover
    storage.init_db()
    yield


app = FastAPI(title="Gym Agent", version="2.0.0", lifespan=_lifespan)


def get_storage() -> None:
    """Dependency hook to ensure storage startup runs before requests."""
    return None


AGENT_CARD: dict[str, Any] = {
    "name": "gym_agent",
    "version": "2.0.0",
    "description": "A2A-compatible gym agent for structured training and bodyweight tracking.",
    "capabilities": [
        {
            "name": "log_lifting_session",
            "description": "Log a structured gym session with exercises and sets.",
            "payload_schema": "LogLiftingSessionPayload",
        },
        {
            "name": "get_lifting_history",
            "description": "Retrieve gym-session history with optional date or exercise filters.",
            "payload_schema": "GetLiftingHistoryPayload",
        },
        {
            "name": "summarize_strength_progress",
            "description": "Summarize training volume, reps, sets, and session trends.",
            "payload_schema": "SummarizeStrengthProgressPayload",
        },
        {
            "name": "get_personal_records",
            "description": "Return heaviest and highest-volume set records by exercise.",
            "payload_schema": "GetPersonalRecordsPayload",
        },
        {
            "name": "log_bodyweight",
            "description": "Log a bodyweight measurement for a user.",
            "payload_schema": "LogBodyweightPayload",
        },
        {
            "name": "get_bodyweight_history",
            "description": "Retrieve bodyweight history with optional date filters.",
            "payload_schema": "GetBodyweightHistoryPayload",
        },
        {
            "name": "summarize_bodyweight_progress",
            "description": "Summarize bodyweight change over a rolling window.",
            "payload_schema": "SummarizeBodyweightProgressPayload",
        },
    ],
}


@app.get("/agent/card")
def agent_card() -> dict[str, Any]:
    """Return the A2A agent card describing this agent's capabilities."""
    return AGENT_CARD


def _make_response(result: Any, context_id: str | None) -> dict[str, Any]:
    response: dict[str, Any] = {"status": "ok", "result": result}
    if context_id:
        response["context_id"] = context_id
    return response


def _make_error(message: str, context_id: str | None) -> dict[str, Any]:
    response: dict[str, Any] = {"status": "error", "message": message}
    if context_id:
        response["context_id"] = context_id
    return response


@app.post("/a2a")
def a2a(
    request: Request,
    body: A2ARequest,
    _storage: None = Depends(get_storage),
) -> dict[str, Any]:
    """Dispatch A2A requests to lifting/bodyweight capability handlers."""
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


def _dispatch(capability: str, payload: Any) -> Any:
    """Route a validated payload to the correct handler."""
    if capability == "log_lifting_session":
        return _handle_log_lifting_session(payload)
    if capability == "get_lifting_history":
        return _handle_get_lifting_history(payload)
    if capability == "summarize_strength_progress":
        return _handle_summarize_strength_progress(payload)
    if capability == "get_personal_records":
        return _handle_get_personal_records(payload)
    if capability == "log_bodyweight":
        return _handle_log_bodyweight(payload)
    if capability == "get_bodyweight_history":
        return _handle_get_bodyweight_history(payload)
    if capability == "summarize_bodyweight_progress":
        return _handle_summarize_bodyweight_progress(payload)
    raise ValueError(f"Unknown capability: {capability}")  # pragma: no cover


def _handle_log_lifting_session(payload: LogLiftingSessionPayload) -> dict[str, Any]:
    session_id = storage.save_lifting_session(
        user_id=payload.user_id,
        session_date=payload.date,
        workout_name=payload.workout_name,
        exercises=[exercise.model_dump(exclude_none=True) for exercise in payload.exercises],
        notes=payload.notes,
    )
    total_sets = sum(len(exercise.sets) for exercise in payload.exercises)
    total_reps = sum(lifting_set.reps for exercise in payload.exercises for lifting_set in exercise.sets)
    total_volume_kg = round(
        sum(
            lifting_set.reps * lifting_set.weight_kg
            for exercise in payload.exercises
            for lifting_set in exercise.sets
        ),
        2,
    )
    return {
        "session_id": session_id,
        "user_id": payload.user_id,
        "date": payload.date,
        "workout_name": payload.workout_name,
        "exercise_count": len(payload.exercises),
        "total_sets": total_sets,
        "total_reps": total_reps,
        "total_volume_kg": total_volume_kg,
    }


def _handle_get_lifting_history(payload: GetLiftingHistoryPayload) -> list[dict[str, Any]]:
    return storage.get_lifting_history(
        user_id=payload.user_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        exercise_name=payload.exercise_name,
        limit=payload.limit,
    )


def _handle_summarize_strength_progress(
    payload: SummarizeStrengthProgressPayload,
) -> dict[str, Any]:
    return storage.summarize_strength_progress(
        user_id=payload.user_id,
        days=payload.days,
        exercise_name=payload.exercise_name,
    )


def _handle_get_personal_records(payload: GetPersonalRecordsPayload) -> dict[str, Any]:
    return storage.get_personal_records(
        user_id=payload.user_id,
        exercise_name=payload.exercise_name,
    )


def _handle_log_bodyweight(payload: LogBodyweightPayload) -> dict[str, Any]:
    entry_id = storage.save_bodyweight(
        user_id=payload.user_id,
        entry_date=payload.date,
        weight_kg=payload.weight_kg,
        notes=payload.notes,
    )
    return {
        "id": entry_id,
        "user_id": payload.user_id,
        "date": payload.date,
        "weight_kg": round(payload.weight_kg, 2),
    }


def _handle_get_bodyweight_history(
    payload: GetBodyweightHistoryPayload,
) -> list[dict[str, Any]]:
    return storage.get_bodyweight_history(
        user_id=payload.user_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        limit=payload.limit,
    )


def _handle_summarize_bodyweight_progress(
    payload: SummarizeBodyweightProgressPayload,
) -> dict[str, Any]:
    return storage.summarize_bodyweight_progress(
        user_id=payload.user_id,
        days=payload.days,
    )
