"""Incident schema — output of the Intake stage."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class IncidentType(str, Enum):
    """Known incident categories."""

    BAGGAGE_MISMATCH = "baggage_mismatch"
    UNKNOWN = "unknown"


class Incident(BaseModel):
    """An incident extracted from an incoming Gmail message.

    Produced by: Intake stage.
    Consumed by: RunState, all downstream stages.
    """

    message_id: str = Field(description="Gmail message ID")
    thread_id: str = Field(description="Gmail thread ID for dedup")
    trip_ref: str = Field(
        description="12-digit trip reference extracted from mail",
        min_length=12,
        max_length=12,
        pattern=r"^\d{12}$",
    )
    incident_type: IncidentType = Field(description="Classified incident type")
    reported_symptom: str = Field(description="Symptom text extracted from mail body")
    received_at: datetime = Field(description="When the mail was received")
