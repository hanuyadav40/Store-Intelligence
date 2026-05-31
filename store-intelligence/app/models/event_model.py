from enum import Enum
from datetime import datetime
from typing import Optional, Dict, Any
import uuid

from pydantic import BaseModel, Field


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    REENTRY = "REENTRY"
    STAFF = "STAFF"
    GROUP_ENTRY = "GROUP_ENTRY"


class PersonEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    person_id: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    event_type: EventType
    confidence: float = Field(ge=0.0, le=1.0)
    camera_id: str = "cam_01"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}
