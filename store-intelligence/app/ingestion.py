"""
Event ingestion logic.

Handles:
- Schema validation (Pydantic does the heavy lifting)
- Idempotent deduplication by event_id via SELECT-before-INSERT
- Session lifecycle management (ENTRY creates, EXIT closes, REENTRY increments)
- POS correlation at session close time
- Batch processing with partial success on malformed events
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.middleware import trace_id_var
from app.models import Event, POSTransaction, VisitorSession
from app.schemas import IngestError, IngestResponse, StoreEvent

logger = logging.getLogger("api.ingestion")
settings = get_settings()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
async def ingest_event_batch(
    events: list[StoreEvent], db: AsyncSession
) -> IngestResponse:
    """Process a batch of up to 500 events. Returns partial-success response."""
    trace_id = trace_id_var.get() or str(uuid.uuid4())

    accepted = 0
    rejected = 0
    duplicate = 0
    errors: list[IngestError] = []

    for event in events:
        try:
            result = await _ingest_single(event, db)
            if result == "duplicate":
                duplicate += 1
            else:
                accepted += 1
        except Exception as exc:
            rejected += 1
            errors.append(IngestError(event_id=event.event_id, error=str(exc)))
            logger.warning(
                "Event rejected",
                extra={
                    "trace_id": trace_id,
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "error": str(exc),
                },
            )

    logger.info(
        "Batch ingested",
        extra={
            "trace_id": trace_id,
            "event_count": len(events),
            "accepted": accepted,
            "duplicate": duplicate,
            "rejected": rejected,
        },
    )

    return IngestResponse(
        trace_id=trace_id,
        accepted=accepted,
        rejected=rejected,
        duplicate=duplicate,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Single event processing
# ---------------------------------------------------------------------------
async def _ingest_single(event: StoreEvent, db: AsyncSession) -> str:
    """
    Insert one event. Returns 'ok' or 'duplicate'.
    Raises on validation failure.
    """
    # Idempotency check — SELECT is cheaper than catching IntegrityError on INSERT
    exists = await db.execute(
        select(Event.id).where(Event.event_id == event.event_id).limit(1)
    )
    if exists.scalar_one_or_none() is not None:
        return "duplicate"

    db_event = Event(
        event_id=event.event_id,
        store_id=event.store_id,
        camera_id=event.camera_id,
        visitor_id=event.visitor_id,
        event_type=event.event_type,
        timestamp=event.timestamp,
        zone_id=event.zone_id,
        dwell_ms=event.dwell_ms,
        is_staff=event.is_staff,
        confidence=event.confidence,
        queue_depth=event.metadata.queue_depth,
        sku_zone=event.metadata.sku_zone,
        session_seq=event.metadata.session_seq,
    )
    db.add(db_event)

    # Maintain session table based on event type
    await _update_session(event, db)

    try:
        await db.flush()
    except IntegrityError:
        # Race condition duplicate — safe to ignore
        await db.rollback()
        return "duplicate"

    return "ok"


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------
async def _update_session(event: StoreEvent, db: AsyncSession) -> None:
    """Update visitor_sessions table based on the incoming event type."""

    if event.event_type == "ENTRY":
        await _handle_entry(event, db)

    elif event.event_type == "REENTRY":
        await _handle_reentry(event, db)

    elif event.event_type == "EXIT":
        await _handle_exit(event, db)

    elif event.event_type == "ZONE_ENTER":
        await _handle_zone_enter(event, db)

    elif event.event_type == "BILLING_QUEUE_JOIN":
        await _handle_billing_join(event, db)

    elif event.event_type == "BILLING_QUEUE_ABANDON":
        await _handle_billing_abandon(event, db)


async def _get_open_session(
    visitor_id: str, store_id: str, db: AsyncSession
) -> Optional[VisitorSession]:
    """Returns the most recent open (no exit_time) session for a visitor."""
    result = await db.execute(
        select(VisitorSession)
        .where(
            VisitorSession.visitor_id == visitor_id,
            VisitorSession.store_id == store_id,
            VisitorSession.exit_time.is_(None),
        )
        .order_by(VisitorSession.entry_time.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _handle_entry(event: StoreEvent, db: AsyncSession) -> None:
    # Close any dangling open session (crash recovery)
    stale = await _get_open_session(event.visitor_id, event.store_id, db)
    if stale is not None:
        stale.exit_time = event.timestamp  # graceful close

    session = VisitorSession(
        session_id=str(uuid.uuid4()),
        store_id=event.store_id,
        visitor_id=event.visitor_id,
        entry_time=event.timestamp,
        is_staff=event.is_staff,
        zones_visited=[],
        reentry_count=0,
    )
    db.add(session)


async def _handle_reentry(event: StoreEvent, db: AsyncSession) -> None:
    """
    REENTRY: The Re-ID system identified the same person after a prior EXIT.
    We open a new session and increment a counter on their most recent session.
    """
    # Increment reentry_count on the most recently closed session
    result = await db.execute(
        select(VisitorSession)
        .where(
            VisitorSession.visitor_id == event.visitor_id,
            VisitorSession.store_id == event.store_id,
        )
        .order_by(VisitorSession.entry_time.desc())
        .limit(1)
    )
    prev_session = result.scalar_one_or_none()
    if prev_session is not None:
        prev_session.reentry_count = (prev_session.reentry_count or 0) + 1

    # Open a new session for this visit
    new_session = VisitorSession(
        session_id=str(uuid.uuid4()),
        store_id=event.store_id,
        visitor_id=event.visitor_id,
        entry_time=event.timestamp,
        is_staff=event.is_staff,
        zones_visited=[],
        reentry_count=0,
    )
    db.add(new_session)


async def _handle_exit(event: StoreEvent, db: AsyncSession) -> None:
    session = await _get_open_session(event.visitor_id, event.store_id, db)
    if session is None:
        return  # Exit without matching entry — pipeline edge case, ignore

    session.exit_time = event.timestamp

    # POS correlation: check if any POS transaction aligns with this session
    await _correlate_pos(session, db)


async def _handle_zone_enter(event: StoreEvent, db: AsyncSession) -> None:
    session = await _get_open_session(event.visitor_id, event.store_id, db)
    if session is None or event.zone_id is None:
        return

    zones = list(session.zones_visited or [])
    if event.zone_id not in zones:
        zones.append(event.zone_id)
        session.zones_visited = zones  # Trigger JSON column dirty-check


async def _handle_billing_join(event: StoreEvent, db: AsyncSession) -> None:
    session = await _get_open_session(event.visitor_id, event.store_id, db)
    if session is None:
        return

    session.billing_entry_time = event.timestamp

    zones = list(session.zones_visited or [])
    if "BILLING" not in zones:
        zones.append("BILLING")
        session.zones_visited = zones


async def _handle_billing_abandon(event: StoreEvent, db: AsyncSession) -> None:
    # Abandonment is logged as an event; session remains unconverted
    session = await _get_open_session(event.visitor_id, event.store_id, db)
    if session is not None:
        session.billing_entry_time = None  # Reset — they left without purchasing


# ---------------------------------------------------------------------------
# POS correlation
# ---------------------------------------------------------------------------
async def _correlate_pos(session: VisitorSession, db: AsyncSession) -> None:
    """
    Mark a session as converted if a POS transaction occurred within
    POS_CORRELATION_WINDOW_MINUTES after the visitor entered the billing zone.

    Per challenge spec: a visitor who was in billing within 5 minutes before
    a transaction counts as converted.
    """
    if session.billing_entry_time is None:
        return

    window_start = session.billing_entry_time
    window_end = session.billing_entry_time + timedelta(
        minutes=settings.pos_correlation_window_minutes
    )

    result = await db.execute(
        select(POSTransaction)
        .where(
            POSTransaction.store_id == session.store_id,
            POSTransaction.timestamp >= window_start,
            POSTransaction.timestamp <= window_end,
            POSTransaction.correlated_visitor_id.is_(None),
        )
        .order_by(POSTransaction.timestamp)
        .limit(1)
    )
    txn = result.scalar_one_or_none()

    if txn is not None:
        session.is_converted = True
        session.basket_value = txn.basket_value_inr
        txn.correlated_visitor_id = session.visitor_id


# ---------------------------------------------------------------------------
# POS data loader (called at startup)
# ---------------------------------------------------------------------------
async def load_pos_data(path: str, db: AsyncSession) -> int:
    """
    Load pos_transactions.csv into the database.
    Idempotent — rows already present are skipped.
    Returns the count of newly inserted rows.
    """
    import csv
    from datetime import datetime, timezone

    inserted = 0
    try:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Check for duplicate
                exists = await db.execute(
                    select(POSTransaction.id)
                    .where(POSTransaction.transaction_id == row["transaction_id"])
                    .limit(1)
                )
                if exists.scalar_one_or_none() is not None:
                    continue

                ts = datetime.fromisoformat(
                    row["timestamp"].replace("Z", "+00:00")
                )
                txn = POSTransaction(
                    store_id=row["store_id"],
                    transaction_id=row["transaction_id"],
                    timestamp=ts,
                    basket_value_inr=float(row["basket_value_inr"]),
                )
                db.add(txn)
                inserted += 1

        await db.commit()
        logger.info("POS data loaded", extra={"inserted": inserted, "path": path})
    except FileNotFoundError:
        logger.warning("POS data file not found", extra={"path": path})
    except Exception as exc:
        logger.error("POS load failed", extra={"path": path, "error": str(exc)})
        await db.rollback()

    return inserted
