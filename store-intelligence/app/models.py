"""
SQLAlchemy ORM models.

Design decisions:
- BigInteger PKs for high-volume event tables
- Composite indexes on (store_id, timestamp) for time-range queries
- JSON column for zones_visited to avoid a many-to-many table
- All timestamps stored with timezone (UTC) to avoid DST ambiguity
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Event(Base):
    """One row per structured event emitted by the detection pipeline."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    store_id: Mapped[str] = mapped_column(String(50), nullable=False)
    camera_id: Mapped[str] = mapped_column(String(50), nullable=False)
    visitor_id: Mapped[str] = mapped_column(String(50), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    zone_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    dwell_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_staff: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    queue_depth: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sku_zone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    session_seq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_events_event_id"),
        Index("idx_events_store_ts", "store_id", "timestamp"),
        Index("idx_events_store_type_ts", "store_id", "event_type", "timestamp"),
        Index("idx_events_visitor", "visitor_id", "store_id"),
        Index("idx_events_zone", "store_id", "zone_id", "timestamp"),
        Index("idx_events_is_staff", "store_id", "is_staff"),
    )


class VisitorSession(Base):
    """
    Aggregated record for one contiguous store visit.

    Created when an ENTRY event is ingested and closed on EXIT.
    A REENTRY event increments reentry_count and opens a new session row.
    """

    __tablename__ = "visitor_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    store_id: Mapped[str] = mapped_column(String(50), nullable=False)
    visitor_id: Mapped[str] = mapped_column(String(50), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    exit_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_converted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    basket_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    zones_visited: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    reentry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_staff: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    billing_entry_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("session_id", name="uq_sessions_session_id"),
        Index("idx_sessions_store_visitor", "store_id", "visitor_id"),
        Index("idx_sessions_entry_time", "store_id", "entry_time"),
        Index("idx_sessions_open", "store_id", "exit_time"),
    )


class POSTransaction(Base):
    """POS transaction — loaded from pos_transactions.csv at startup."""

    __tablename__ = "pos_transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(50), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(100), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    basket_value_inr: Mapped[float] = mapped_column(Float, nullable=False)
    correlated_visitor_id: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("transaction_id", name="uq_pos_transaction_id"),
        Index("idx_pos_store_ts", "store_id", "timestamp"),
    )
