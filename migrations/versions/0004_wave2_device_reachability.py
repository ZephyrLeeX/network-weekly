"""wave 2: device_reachability_incidents + device_monitoring_state

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-05

Device reachability semantics (W02-T004, SYSTEM_SPEC.md §9/§23):
`device_reachability_incidents` holds the confirmed Down/Recovery records
the weekly report is built from (long-term — never touched by retention);
`device_monitoring_state` is the per-device cycle-tracking row (consecutive
failed/reachable cycles and their run anchors) that implements the 2-cycle
rules with gap detection. All timestamps are TIMESTAMPTZ (§3/§23).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_monitoring_state",
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column(
            "state", sa.Text(), nullable=False, server_default="normal"
        ),
        sa.Column("last_cycle_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failed_cycles", sa.SmallInteger(), nullable=False,
            server_default="0",
        ),
        sa.Column(
            "consecutive_reachable_cycles", sa.SmallInteger(), nullable=False,
            server_default="0",
        ),
        sa.Column("failed_run_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reachable_run_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default="now()",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id"),
    )

    op.create_table(
        "device_reachability_incidents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default="now()"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_device_reachability_incidents_device",
        "device_reachability_incidents",
        ["device_id", "recovered_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_device_reachability_incidents_device", table_name="device_reachability_incidents"
    )
    op.drop_table("device_reachability_incidents")
    op.drop_table("device_monitoring_state")
