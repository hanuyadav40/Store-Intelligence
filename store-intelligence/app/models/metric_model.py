from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class MetricsResponse(BaseModel):
    footfall: int = 0
    unique_visitors: int = 0
    conversion_rate: float = 0.0
    avg_dwell_time: float = 0.0          # seconds
    current_occupancy: int = 0
    total_exits: int = 0
    revisit_rate: float = 0.0
    staff_count: int = 0
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}


class FunnelResponse(BaseModel):
    entered: int = 0
    engaged: int = 0
    converted: int = 0
    engagement_rate: float = 0.0
    conversion_rate: float = 0.0


class AnomalyAlert(BaseModel):
    anomaly_id: str
    anomaly_type: str          # FOOTFALL_SPIKE | UNUSUAL_DWELL | CROWD_FORMATION
    description: str
    severity: str              # LOW | MEDIUM | HIGH
    detected_at: datetime
    value: float
    threshold: float

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    uptime_seconds: float
    demo_mode: bool
    events_processed: int
