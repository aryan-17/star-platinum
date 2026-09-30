"""Trip index and call tree schemas — output of the Log Acquisition stage."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Service(str, Enum):
    """Known services mapped from pod prefixes (longest-prefix-first)."""

    AIR_SMS_NEW = "air-sms-new"
    AIR_SMS = "air-sms"
    SUPPLY_CORE_NEW = "supply-core-new"
    OTHER = "other"


class PayloadFormat(str, Enum):
    """Payload format for a log file."""

    JSON = "json"
    SOAP_XML = "soap_xml"
    PLAIN_TEXT = "plain_text"
    UNKNOWN = "unknown"


class FileRef(BaseModel):
    """Reference to a cached payload file."""

    file_name: str
    itinerary_id: str
    date: str = Field(description="YYYY-MM-DD")
    trip_id: str
    is_request: bool = Field(description="True for request, False for response")
    format: PayloadFormat = PayloadFormat.UNKNOWN


class CallNode(BaseModel):
    """A normalised API call from the trip index.

    Produced by: index normaliser (merges air_api_call + air_book).
    Consumed by: call tree builder, playbook engine, Investigator tools.
    """

    api: str = Field(description="API name as logged, e.g. 'SMS_HOLD'")
    api_type: str = Field(description="API type, e.g. 'NEW-SMS', 'SUPPLY_CORE'")
    url: str = ""
    service: Service = Service.OTHER
    pod: str = Field(default="", description="Pod hostname from air_book")
    host: str = Field(default="", description="IP address from air_api_call")
    time: datetime | None = Field(default=None, description="Call timestamp")
    duration_ms: int | None = Field(
        default=None,
        description="Duration in ms. None if original was invalid (e.g. negative epoch).",
    )
    http_code: int = 200
    identifier: str = Field(default="", description="Correlation ID linking parent/child calls")
    itinerary: str = ""
    supplier: str = ""
    step_name: str = Field(default="", description="From air_book")
    event: str = Field(default="", description="From air_book")
    req_file: FileRef | None = None
    res_file: FileRef | None = None
    children: list[CallNode] = Field(
        default_factory=list,
        description="Child calls linked by identifier",
    )
    is_external: bool = Field(
        default=False,
        description="True if URL is an external supplier call (https://)",
    )
    is_retry: bool = Field(default=False, description="Flagged if same API called twice in window")
    is_slow: bool = Field(default=False, description="Duration > 10s")
    is_timeout: bool = Field(default=False, description="Duration > 30s")


class Journey(BaseModel):
    """A single journey within a round-trip booking."""

    journey_index: int = Field(description="0-based index")
    origin: str = Field(default="", description="Airport code, e.g. 'COK'")
    destination: str = Field(default="", description="Airport code, e.g. 'CAI'")
    supplier: str = Field(default="", description="Detected supplier name")
    supplier_detected_by: str = Field(
        default="",
        description="How supplier was detected, e.g. 'url:airarabia.com'",
    )
    calls: list[CallNode] = Field(default_factory=list)
    hold_id: str = ""
    booking_url: str = ""


class TripIndex(BaseModel):
    """Normalised trip data from the log API.

    Produced by: Log Acquisition stage.
    Consumed by: Evidence stage, Investigator tools.
    """

    trip_id: str
    itineraries: list[str] = Field(default_factory=list)
    trips: list[str] = Field(default_factory=list)
    channel: str = ""
    pax_count: int = 0
    journeys: list[Journey] = Field(default_factory=list)
    all_calls: list[CallNode] = Field(
        default_factory=list,
        description="Flat list of all normalised calls",
    )
    file_inventory: list[str] = Field(
        default_factory=list,
        description="All file names from files_list",
    )
    anomalies: list[str] = Field(
        default_factory=list,
        description="Anomaly flags: retries, slow calls, timeouts",
    )
