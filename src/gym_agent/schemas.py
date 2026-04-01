"""Pydantic schemas for the Gym Agent service."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

SetType = Literal["warmup", "working", "backoff", "dropset", "amrap"]


def _validate_iso_date(value: str) -> str:
    """Validate and normalize an ISO date string."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("Date must not be blank")
    try:
        from datetime import date

        date.fromisoformat(stripped)
    except ValueError as exc:  # pragma: no cover - exercised via model validation
        raise ValueError("Date must be in YYYY-MM-DD format") from exc
    return stripped


def _validate_optional_iso_date(value: str | None) -> str | None:
    """Validate an optional ISO date string."""
    if value is None:
        return None
    return _validate_iso_date(value)


def _validate_non_empty_string(value: str) -> str:
    """Ensure required strings are not blank after trimming."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("Value must not be blank")
    return stripped


def _normalize_optional_string(value: str | None) -> str | None:
    """Trim optional strings and collapse empty values to None."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class LiftingSet(BaseModel):
    """A single tracked lifting set."""

    reps: int = Field(..., ge=1, description="Number of reps performed")
    weight_kg: float = Field(..., ge=0, description="Weight used for the set in kilograms")
    set_type: SetType = Field("working", description="Type of lifting set")
    rpe: float | None = Field(None, ge=1, le=10, description="Optional perceived exertion")
    rir: int | None = Field(None, ge=0, le=10, description="Optional reps-in-reserve estimate")
    notes: str | None = Field(None, description="Optional notes for the set")

    @field_validator("notes")
    @classmethod
    def _strip_notes(cls, value: str | None) -> str | None:
        return _normalize_optional_string(value)


class LiftingExercise(BaseModel):
    """A named exercise containing one or more tracked sets."""

    name: str = Field(..., description="Exercise name, e.g. 'Bench Press'")
    sets: list[LiftingSet] = Field(..., min_length=1, description="Tracked sets for the exercise")
    notes: str | None = Field(None, description="Optional exercise-level notes")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _validate_non_empty_string(value)

    @field_validator("notes")
    @classmethod
    def _strip_notes(cls, value: str | None) -> str | None:
        return _normalize_optional_string(value)


class LogLiftingSessionPayload(BaseModel):
    """Payload for logging a gym session."""

    user_id: str
    date: str = Field(..., description="ISO date string (YYYY-MM-DD)")
    workout_name: str = Field(..., description="Workout name or split, e.g. 'Upper A'")
    exercises: list[LiftingExercise] = Field(..., min_length=1)
    notes: str | None = Field(None, description="Optional session notes")

    @field_validator("user_id", "workout_name")
    @classmethod
    def _validate_required_strings(cls, value: str) -> str:
        return _validate_non_empty_string(value)

    @field_validator("date")
    @classmethod
    def _validate_date(cls, value: str) -> str:
        return _validate_iso_date(value)

    @field_validator("notes")
    @classmethod
    def _strip_notes(cls, value: str | None) -> str | None:
        return _normalize_optional_string(value)


class GetLiftingHistoryPayload(BaseModel):
    """Payload for retrieving gym-session history."""

    user_id: str
    start_date: str | None = Field(None, description="ISO date string (inclusive)")
    end_date: str | None = Field(None, description="ISO date string (inclusive)")
    exercise_name: str | None = Field(None, description="Optional exercise name filter")
    limit: int = Field(50, ge=1, le=500)

    @field_validator("user_id")
    @classmethod
    def _validate_user_id(cls, value: str) -> str:
        return _validate_non_empty_string(value)

    @field_validator("start_date", "end_date")
    @classmethod
    def _validate_dates(cls, value: str | None) -> str | None:
        return _validate_optional_iso_date(value)

    @field_validator("exercise_name")
    @classmethod
    def _validate_exercise_name(cls, value: str | None) -> str | None:
        return _normalize_optional_string(value)


class SummarizeStrengthProgressPayload(BaseModel):
    """Payload for gym-progress summaries."""

    user_id: str
    days: int = Field(30, ge=1, le=3650)
    exercise_name: str | None = Field(None, description="Optional exercise name filter")

    @field_validator("user_id")
    @classmethod
    def _validate_user_id(cls, value: str) -> str:
        return _validate_non_empty_string(value)

    @field_validator("exercise_name")
    @classmethod
    def _validate_exercise_name(cls, value: str | None) -> str | None:
        return _normalize_optional_string(value)


class GetPersonalRecordsPayload(BaseModel):
    """Payload for personal-record lookups."""

    user_id: str
    exercise_name: str | None = Field(None, description="Optional exercise name filter")

    @field_validator("user_id")
    @classmethod
    def _validate_user_id(cls, value: str) -> str:
        return _validate_non_empty_string(value)

    @field_validator("exercise_name")
    @classmethod
    def _validate_exercise_name(cls, value: str | None) -> str | None:
        return _normalize_optional_string(value)


class LogBodyweightPayload(BaseModel):
    """Payload for logging a bodyweight entry."""

    user_id: str
    date: str = Field(..., description="ISO date string (YYYY-MM-DD)")
    weight_kg: float = Field(..., gt=0, description="Measured bodyweight in kilograms")
    notes: str | None = Field(None, description="Optional measurement notes")

    @field_validator("user_id")
    @classmethod
    def _validate_user_id(cls, value: str) -> str:
        return _validate_non_empty_string(value)

    @field_validator("date")
    @classmethod
    def _validate_date(cls, value: str) -> str:
        return _validate_iso_date(value)

    @field_validator("notes")
    @classmethod
    def _strip_notes(cls, value: str | None) -> str | None:
        return _normalize_optional_string(value)


class GetBodyweightHistoryPayload(BaseModel):
    """Payload for retrieving bodyweight history."""

    user_id: str
    start_date: str | None = Field(None, description="ISO date string (inclusive)")
    end_date: str | None = Field(None, description="ISO date string (inclusive)")
    limit: int = Field(100, ge=1, le=1000)

    @field_validator("user_id")
    @classmethod
    def _validate_user_id(cls, value: str) -> str:
        return _validate_non_empty_string(value)

    @field_validator("start_date", "end_date")
    @classmethod
    def _validate_dates(cls, value: str | None) -> str | None:
        return _validate_optional_iso_date(value)


class SummarizeBodyweightProgressPayload(BaseModel):
    """Payload for bodyweight summaries."""

    user_id: str
    days: int = Field(30, ge=1, le=3650)

    @field_validator("user_id")
    @classmethod
    def _validate_user_id(cls, value: str) -> str:
        return _validate_non_empty_string(value)


Capability = Literal[
    "log_lifting_session",
    "get_lifting_history",
    "summarize_strength_progress",
    "get_personal_records",
    "log_bodyweight",
    "get_bodyweight_history",
    "summarize_bodyweight_progress",
]

_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "log_lifting_session": LogLiftingSessionPayload,
    "get_lifting_history": GetLiftingHistoryPayload,
    "summarize_strength_progress": SummarizeStrengthProgressPayload,
    "get_personal_records": GetPersonalRecordsPayload,
    "log_bodyweight": LogBodyweightPayload,
    "get_bodyweight_history": GetBodyweightHistoryPayload,
    "summarize_bodyweight_progress": SummarizeBodyweightProgressPayload,
}


class A2ARequest(BaseModel):
    """Generic A2A request envelope."""

    capability: Capability = Field(..., description="Name of the capability to invoke")
    payload: dict[str, Any] = Field(..., description="Capability-specific payload")

    def parse_payload(self) -> BaseModel:
        """Validate and parse the raw payload dict into the correct model."""
        model_cls = _PAYLOAD_MODELS[self.capability]
        return model_cls.model_validate(self.payload)
