"""Pydantic schemas for the Gym Agent A2A service."""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Workout(BaseModel):
    """A single workout entry."""

    type: str = Field(..., description="Type of workout, e.g. 'running', 'cycling'")
    duration_min: Optional[int] = Field(None, ge=0, description="Duration in minutes")
    distance_km: Optional[float] = Field(None, ge=0, description="Distance in kilometres")
    notes: Optional[str] = Field(None, description="Free-text notes")


class LogWorkoutPayload(BaseModel):
    """Payload for the log_workout capability."""

    user_id: str
    date: str = Field(..., description="ISO date string (YYYY-MM-DD)")
    workout: Workout

    @field_validator("date")
    @classmethod
    def _validate_date(cls, v: str) -> str:
        """Ensure the date is a valid ISO date string."""
        if isinstance(v, date):
            return v.isoformat()
        date.fromisoformat(str(v))  # raises ValueError on invalid format
        return str(v)

    def date_str(self) -> str:
        """Return the date as an ISO string."""
        return self.date


class GetHistoryPayload(BaseModel):
    """Payload for the get_history capability."""

    user_id: str
    start_date: Optional[str] = Field(None, description="ISO date string (inclusive)")
    end_date: Optional[str] = Field(None, description="ISO date string (inclusive)")
    limit: int = Field(100, ge=1, le=1000)


class SummarizePayload(BaseModel):
    """Payload for the summarize_progress capability."""

    user_id: str
    days: int = Field(30, ge=1, le=3650)


class HabitStreakPayload(BaseModel):
    """Payload for the habit_streak capability."""

    user_id: str
    habit: str = Field(..., description="Workout type to track as a habit")
    min_days: int = Field(1, ge=1, description="Minimum days between workouts to count as streak")


# ---------------------------------------------------------------------------
# A2A envelope
# ---------------------------------------------------------------------------

Capability = Literal["log_workout", "get_history", "summarize_progress", "habit_streak"]

_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "log_workout": LogWorkoutPayload,
    "get_history": GetHistoryPayload,
    "summarize_progress": SummarizePayload,
    "habit_streak": HabitStreakPayload,
}


class A2ARequest(BaseModel):
    """Generic A2A request envelope."""

    capability: Capability = Field(..., description="Name of the capability to invoke")
    payload: dict = Field(..., description="Capability-specific payload")

    def parse_payload(self) -> BaseModel:
        """Validate and parse the raw payload dict into the correct model."""
        model_cls = _PAYLOAD_MODELS[self.capability]
        return model_cls.model_validate(self.payload)
