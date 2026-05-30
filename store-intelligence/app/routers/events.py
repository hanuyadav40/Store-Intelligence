"""
POST /events/ingest — batch event ingest endpoint.
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.database import get_db
from app.ingestion import ingest_event_batch
from app.schemas import EventBatch, IngestResponse

router = APIRouter(prefix="/events", tags=["events"])
logger = logging.getLogger("api.routers.events")


@router.post(
    "/ingest",
    response_model=IngestResponse,
    summary="Ingest a batch of store events",
    description=(
        "Accepts up to 500 events per request. Events are validated, deduplicated, "
        "and persisted. Returns counts of accepted, rejected, and duplicate events."
    ),
)
async def ingest_events(
    batch: EventBatch,
    request: Request,
    db=Depends(get_db),
) -> IngestResponse:
    logger.info(
        "Ingest request received",
        extra={"event_count": len(batch.events), "url": str(request.url)},
    )
    result = await ingest_event_batch(batch.events, db)
    return result
