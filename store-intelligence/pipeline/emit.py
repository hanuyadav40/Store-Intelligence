"""
Event emitter — state machine per visitor_id.

Converts raw ByteTrack detections (frame-level) into structured store events
that conform to the API ingest schema. Events are batched and POSTed to the
API at the end of the clip (or when the batch reaches MAX_BATCH_SIZE).

State machine per visitor:
  OUTSIDE → (ENTRY) → IN_STORE
  IN_STORE → (ZONE_ENTER) → IN_ZONE
  IN_ZONE  → (ZONE_DWELL every DWELL_EMIT_INTERVAL_SECONDS) → IN_ZONE
  IN_ZONE  → (ZONE_EXIT) → IN_STORE
  IN_STORE → (BILLING_QUEUE_JOIN when billing zone) → IN_BILLING
  IN_BILLING → (BILLING_QUEUE_ABANDON on zone exit without purchase) → IN_STORE
  IN_STORE → (EXIT) → OUTSIDE

REENTRY events are handled upstream by detect.py which passes is_reentry=True
and the resolved visitor_id from the ReID gallery.
"""
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger("pipeline.emit")

DWELL_EMIT_INTERVAL = 30  # seconds
MAX_BATCH_SIZE = 200
BILLING_ZONE_KEYWORDS = {"BILLING", "BILLING_QUEUE", "CASHIER", "CHECKOUT"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_event_id() -> str:
    return str(uuid.uuid4())


@dataclass
class VisitorState:
    visitor_id: str
    store_id: str
    camera_id: str
    is_staff: bool = False
    current_zone_id: Optional[str] = None
    current_sku_zone: Optional[str] = None
    zone_entry_time: float = 0.0
    last_dwell_emit_time: float = 0.0
    session_seq: int = 0
    in_billing: bool = False


class EventEmitter:
    """
    Converts detection stream into API-ready structured events.

    Parameters
    ----------
    store_id: str
    camera_id: str
    api_url: str
        Base URL of the store-intelligence API (e.g., http://localhost:8000)
    confidence: float
        Default confidence value (use track confidence if available)
    """

    def __init__(
        self,
        store_id: str,
        camera_id: str,
        api_url: str,
        confidence: float = 0.85,
    ):
        self.store_id = store_id
        self.camera_id = camera_id
        self.api_url = api_url.rstrip("/")
        self.confidence = confidence
        self._states: dict[str, VisitorState] = {}
        self._pending: list[dict] = []

    # ------------------------------------------------------------------
    # Public interface (called by detect.py per frame)
    # ------------------------------------------------------------------
    def on_entry(
        self,
        visitor_id: str,
        is_staff: bool = False,
        is_reentry: bool = False,
        confidence: Optional[float] = None,
    ) -> None:
        if visitor_id not in self._states:
            self._states[visitor_id] = VisitorState(
                visitor_id=visitor_id,
                store_id=self.store_id,
                camera_id=self.camera_id,
                is_staff=is_staff,
            )
        state = self._states[visitor_id]
        state.session_seq += 1

        event_type = "REENTRY" if is_reentry else "ENTRY"
        self._emit(
            state,
            event_type=event_type,
            confidence=confidence or self.confidence,
        )

    def on_zone_change(
        self,
        visitor_id: str,
        new_zone_id: Optional[str],
        new_sku_zone: Optional[str],
        confidence: Optional[float] = None,
    ) -> None:
        state = self._states.get(visitor_id)
        if state is None:
            return

        old_zone = state.current_zone_id
        now = time.monotonic()

        # Emit ZONE_EXIT + final dwell for old zone
        if old_zone is not None and old_zone != new_zone_id:
            total_dwell_ms = int((now - state.zone_entry_time) * 1000)
            self._emit(
                state,
                event_type="ZONE_DWELL",
                zone_id=old_zone,
                sku_zone=state.current_sku_zone,
                dwell_ms=total_dwell_ms,
                confidence=confidence or self.confidence,
            )
            self._emit(
                state,
                event_type="ZONE_EXIT",
                zone_id=old_zone,
                sku_zone=state.current_sku_zone,
                dwell_ms=total_dwell_ms,
                confidence=confidence or self.confidence,
            )

            # Billing queue abandon: was in billing, now left without completing
            if state.in_billing and not self._is_billing_zone(new_zone_id):
                self._emit(
                    state,
                    event_type="BILLING_QUEUE_ABANDON",
                    zone_id=old_zone,
                    sku_zone=state.current_sku_zone,
                    dwell_ms=total_dwell_ms,
                    confidence=confidence or self.confidence,
                )
                state.in_billing = False

        # Emit ZONE_ENTER for new zone
        if new_zone_id is not None:
            is_billing = self._is_billing_zone(new_zone_id)
            queue_depth = self._count_billing_visitors() if is_billing else None
            self._emit(
                state,
                event_type="BILLING_QUEUE_JOIN" if is_billing else "ZONE_ENTER",
                zone_id=new_zone_id,
                sku_zone=new_sku_zone,
                dwell_ms=0,
                queue_depth=queue_depth,
                confidence=confidence or self.confidence,
            )
            if is_billing:
                state.in_billing = True

        # Update state
        state.current_zone_id = new_zone_id
        state.current_sku_zone = new_sku_zone
        state.zone_entry_time = now
        state.last_dwell_emit_time = now

    def on_dwell_tick(
        self, visitor_id: str, confidence: Optional[float] = None
    ) -> None:
        """
        Call periodically (e.g., every DWELL_EMIT_INTERVAL seconds) for
        visitors that remain in the same zone.
        """
        state = self._states.get(visitor_id)
        if state is None or state.current_zone_id is None:
            return

        now = time.monotonic()
        if now - state.last_dwell_emit_time < DWELL_EMIT_INTERVAL:
            return

        elapsed_ms = int((now - state.zone_entry_time) * 1000)
        self._emit(
            state,
            event_type="ZONE_DWELL",
            zone_id=state.current_zone_id,
            sku_zone=state.current_sku_zone,
            dwell_ms=elapsed_ms,
            confidence=confidence or self.confidence,
        )
        state.last_dwell_emit_time = now

    def on_exit(
        self, visitor_id: str, confidence: Optional[float] = None
    ) -> None:
        state = self._states.get(visitor_id)
        if state is None:
            return

        now = time.monotonic()

        # Close open zone
        if state.current_zone_id is not None:
            dwell_ms = int((now - state.zone_entry_time) * 1000)
            self._emit(
                state,
                event_type="ZONE_DWELL",
                zone_id=state.current_zone_id,
                sku_zone=state.current_sku_zone,
                dwell_ms=dwell_ms,
                confidence=confidence or self.confidence,
            )
            self._emit(
                state,
                event_type="ZONE_EXIT",
                zone_id=state.current_zone_id,
                sku_zone=state.current_sku_zone,
                dwell_ms=dwell_ms,
                confidence=confidence or self.confidence,
            )

        self._emit(
            state,
            event_type="EXIT",
            confidence=confidence or self.confidence,
        )

        # Keep state for ReID reference but reset zone
        state.current_zone_id = None
        state.in_billing = False

    def flush(self) -> int:
        """Send all pending events to the API. Returns number of events sent."""
        if not self._pending:
            return 0
        total = 0
        # Send in MAX_BATCH_SIZE chunks
        for i in range(0, len(self._pending), MAX_BATCH_SIZE):
            chunk = self._pending[i : i + MAX_BATCH_SIZE]
            self._post_batch(chunk)
            total += len(chunk)
        self._pending.clear()
        return total

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _emit(
        self,
        state: VisitorState,
        event_type: str,
        zone_id: Optional[str] = None,
        sku_zone: Optional[str] = None,
        dwell_ms: int = 0,
        queue_depth: Optional[int] = None,
        confidence: float = 0.85,
    ) -> None:
        state.session_seq += 1
        event = {
            "event_id": _new_event_id(),
            "store_id": state.store_id,
            "camera_id": state.camera_id,
            "visitor_id": state.visitor_id,
            "event_type": event_type,
            "timestamp": _utcnow(),
            "zone_id": zone_id,
            "dwell_ms": dwell_ms,
            "is_staff": state.is_staff,
            "confidence": round(confidence, 4),
            "metadata": {
                "session_seq": state.session_seq,
                "sku_zone": sku_zone,
                "queue_depth": queue_depth,
            },
        }
        self._pending.append(event)

        if len(self._pending) >= MAX_BATCH_SIZE:
            self.flush()

    def _is_billing_zone(self, zone_id: Optional[str]) -> bool:
        if zone_id is None:
            return False
        return any(kw in zone_id.upper() for kw in BILLING_ZONE_KEYWORDS)

    def _count_billing_visitors(self) -> int:
        return sum(
            1 for s in self._states.values() if s.in_billing and not s.is_staff
        )

    def _post_batch(self, events: list[dict]) -> None:
        url = f"{self.api_url}/events/ingest"
        payload = {"events": events}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "Batch posted: accepted=%d rejected=%d duplicate=%d",
                data.get("accepted", 0),
                data.get("rejected", 0),
                data.get("duplicate", 0),
            )
        except requests.RequestException as exc:
            logger.error("Failed to post event batch: %s", exc)
