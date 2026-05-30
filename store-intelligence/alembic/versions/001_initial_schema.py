"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-03 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # events — raw event stream from the detection pipeline
    # ------------------------------------------------------------------
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("store_id", sa.String(50), nullable=False),
        sa.Column("camera_id", sa.String(50), nullable=False),
        sa.Column("visitor_id", sa.String(50), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("zone_id", sa.String(50), nullable=True),
        sa.Column("dwell_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_staff", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("queue_depth", sa.Integer(), nullable=True),
        sa.Column("sku_zone", sa.String(100), nullable=True),
        sa.Column("session_seq", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_events_event_id"),
    )
    op.create_index("idx_events_store_ts", "events", ["store_id", "timestamp"])
    op.create_index(
        "idx_events_store_type_ts", "events", ["store_id", "event_type", "timestamp"]
    )
    op.create_index("idx_events_visitor", "events", ["visitor_id", "store_id"])
    op.create_index(
        "idx_events_zone", "events", ["store_id", "zone_id", "timestamp"]
    )
    op.create_index("idx_events_is_staff", "events", ["store_id", "is_staff"])

    # ------------------------------------------------------------------
    # visitor_sessions — one row per contiguous store visit
    # ------------------------------------------------------------------
    op.create_table(
        "visitor_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("store_id", sa.String(50), nullable=False),
        sa.Column("visitor_id", sa.String(50), nullable=False),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_converted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("basket_value", sa.Float(), nullable=True),
        sa.Column("zones_visited", sa.JSON(), nullable=True),
        sa.Column("reentry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_staff", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("billing_entry_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_sessions_session_id"),
    )
    op.create_index(
        "idx_sessions_store_visitor", "visitor_sessions", ["store_id", "visitor_id"]
    )
    op.create_index(
        "idx_sessions_entry_time", "visitor_sessions", ["store_id", "entry_time"]
    )
    op.create_index(
        "idx_sessions_open", "visitor_sessions", ["store_id", "exit_time"]
    )

    # ------------------------------------------------------------------
    # pos_transactions — POS data used for conversion correlation
    # ------------------------------------------------------------------
    op.create_table(
        "pos_transactions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("store_id", sa.String(50), nullable=False),
        sa.Column("transaction_id", sa.String(100), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("basket_value_inr", sa.Float(), nullable=False),
        sa.Column("correlated_visitor_id", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transaction_id", name="uq_pos_transaction_id"),
    )
    op.create_index(
        "idx_pos_store_ts", "pos_transactions", ["store_id", "timestamp"]
    )


def downgrade() -> None:
    op.drop_table("pos_transactions")
    op.drop_table("visitor_sessions")
    op.drop_table("events")
