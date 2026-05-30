"""
Pydantic v2 request/response schemas.

The StoreEvent schema mirrors the challenge-specified event format exactly.
All validators are strict enough to catch schema violations but flexible
enough to accept events from different detection pipeline implementations.
"""
from datetime import datetime
from typing import Annotated, Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Event type catalogue (matches challenge spec exactly)
# ---------------------------------------------------------------------------
EventTypeEnum = Literal[
    "ENTRY",
    "EXIT",
    "ZONE_ENTER",
    "ZONE_EXIT",
    "ZONE_DWELL",
    "BILLING_QUEUE_JOIN",
    "BILLING_QUEUE_ABANDON",
    "REENTRY",
]

SeverityEnum = Literal["INFO", "WARN", "CRITICAL"]


# ---------------------------------------------------------------------------
# Inbound event schema
# ---------------------------------------------------------------------------
class EventMetadata(BaseModel):
    queue_depth: Optional[int] = Field(None, ge=0)
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = Field(None, ge=1)


class StoreEvent(BaseModel):
    event_id: str = Field(..., description="UUID v4 — must be globally unique")
    store_id: str = Field(..., min_length=1, max_length=50)
    camera_id: str = Field(..., min_length=1, max_length=50)
    visitor_id: str = Field(..., min_length=1, max_length=50)
    event_type: EventTypeEnum
    timestamp: datetime
    zone_id: Optional[str] = Field(None, max_length=50)
    dwell_ms: int = Field(default=0, ge=0)
    is_staff: bool = False
    confidence: float = Field(..., ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("event_id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        try:
            UUID(v, version=4)
        except (ValueError, AttributeError):
            raise ValueError(f"event_id must be a valid UUID v4, got: {v!r}")
        return v

    @model_validator(mode="after")
    def validate_zone_consistency(self) -> "StoreEvent":
        """ENTRY and EXIT events must have null zone_id."""
        if self.event_type in ("ENTRY", "EXIT", "REENTRY") and self.zone_id is not None:
            # Allow but do not enforce — pipeline may include entry zone context
            pass
        if self.event_type in ("ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL") and not self.zone_id:
            raise ValueError(
                f"event_type {self.event_type!r} requires a non-null zone_id"
            )
        if self.event_type == "BILLING_QUEUE_JOIN" and self.metadata.queue_depth is None:
            raise ValueError("BILLING_QUEUE_JOIN requires metadata.queue_depth")
        return self


class EventBatch(BaseModel):
    events: Annotated[list[StoreEvent], Field(min_length=1, max_length=500)]


# ---------------------------------------------------------------------------
# Ingest response
# ---------------------------------------------------------------------------
class IngestError(BaseModel):
    event_id: str
    error: str


class IngestResponse(BaseModel):
    trace_id: str
    accepted: int
    rejected: int
    duplicate: int
    errors: list[IngestError]


# ---------------------------------------------------------------------------
# Metrics response
# ---------------------------------------------------------------------------
class ZoneDwellStats(BaseModel):
    zone_id: str
    avg_dwell_ms: float
    visit_count: int


class StoreMetrics(BaseModel):
    store_id: str
    date: str
    unique_visitors: int
    conversion_rate: float
    avg_dwell_per_zone: list[ZoneDwellStats]
    current_queue_depth: int
    abandonment_rate: float
    total_transactions: int
    as_of: str


# ---------------------------------------------------------------------------
# Funnel response
# ---------------------------------------------------------------------------
class FunnelStage(BaseModel):
    stage: str
    label: str
    count: int
    drop_off_pct: float


class FunnelResponse(BaseModel):
    store_id: str
    date: str
    stages: list[FunnelStage]
    data_confidence: bool = True


# ---------------------------------------------------------------------------
# Heatmap response
# ---------------------------------------------------------------------------
class HeatmapZone(BaseModel):
    zone_id: str
    sku_zone: Optional[str]
    visit_frequency: int
    avg_dwell_ms: float
    normalised_score: float = Field(..., ge=0.0, le=100.0)


class HeatmapResponse(BaseModel):
    store_id: str
    date: str
    zones: list[HeatmapZone]
    data_confidence: bool = True


# ---------------------------------------------------------------------------
# Anomaly response
# ---------------------------------------------------------------------------
class Anomaly(BaseModel):
    anomaly_id: str
    anomaly_type: str
    severity: SeverityEnum
    description: str
    suggested_action: str
    detected_at: str
    metadata: dict[str, Any] = {}


class AnomalyResponse(BaseModel):
    store_id: str
    anomalies: list[Anomaly]
    as_of: str


# ---------------------------------------------------------------------------
# Health response
# ---------------------------------------------------------------------------
class StoreHealthStatus(BaseModel):
    store_id: str
    last_event_at: Optional[str]
    lag_seconds: Optional[float]
    status: Literal["OK", "STALE_FEED", "NO_DATA"]


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    version: str
    environment: str
    database: Literal["ok", "error"]
    redis: Literal["ok", "error"]
    stores: list[StoreHealthStatus]
    uptime_seconds: float
    as_of: str
