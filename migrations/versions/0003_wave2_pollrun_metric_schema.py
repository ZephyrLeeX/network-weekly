"""wave 2: device_poll_runs, device_metrics, interface_metrics

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-05

Wave 2 monitoring-pipeline raw-data schema (W02-T001, SYSTEM_SPEC.md
§7.1/§8/§15/§23/§24). One `device_poll_runs` row per planned 5-minute cycle
per device (unique `(device_id, cycle_started_at)`), device- and
interface-level metric samples referencing it, with the time indexes the
weekly statistics and the 90-day batched retention (§24) need. All
timestamps are TIMESTAMPTZ (§3/§23).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_poll_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cycle_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("ssh_reachable", sa.Boolean(), nullable=True),
        sa.Column("failed_sections", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default="now()"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "cycle_started_at", name="uq_device_poll_run_cycle"),
    )
    op.create_index(
        "ix_device_poll_runs_cycle_started_at", "device_poll_runs", ["cycle_started_at"]
    )

    op.create_table(
        "device_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "poll_run_id",
            sa.Integer(),
            sa.ForeignKey("device_poll_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cpu_usage_percent", sa.Float(), nullable=True),
        sa.Column("memory_usage_percent", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("poll_run_id", name="uq_device_metric_poll_run"),
    )
    op.create_index(
        "ix_device_metrics_device_collected", "device_metrics", ["device_id", "collected_at"]
    )
    op.create_index("ix_device_metrics_collected_at", "device_metrics", ["collected_at"])

    op.create_table(
        "interface_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "poll_run_id",
            sa.Integer(),
            sa.ForeignKey("device_poll_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "interface_id",
            sa.Integer(),
            sa.ForeignKey("interfaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("admin_state", sa.Text(), nullable=True),
        sa.Column("oper_state", sa.Text(), nullable=True),
        sa.Column("speed_bps", sa.BigInteger(), nullable=True),
        sa.Column("in_octets", sa.BigInteger(), nullable=True),
        sa.Column("out_octets", sa.BigInteger(), nullable=True),
        sa.Column("in_errors", sa.BigInteger(), nullable=True),
        sa.Column("out_errors", sa.BigInteger(), nullable=True),
        sa.Column("in_discards", sa.BigInteger(), nullable=True),
        sa.Column("out_discards", sa.BigInteger(), nullable=True),
        sa.Column("fcs_errors", sa.BigInteger(), nullable=True),
        sa.Column("in_utilization_percent", sa.Float(), nullable=True),
        sa.Column("out_utilization_percent", sa.Float(), nullable=True),
        sa.Column("utilization_elapsed_seconds", sa.Float(), nullable=True),
        sa.Column(
            "utilization_rebaselined", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("poll_run_id", "interface_id", name="uq_interface_metric_sample"),
    )
    op.create_index(
        "ix_interface_metrics_interface_collected",
        "interface_metrics",
        ["interface_id", "collected_at"],
    )
    op.create_index("ix_interface_metrics_collected_at", "interface_metrics", ["collected_at"])


def downgrade() -> None:
    op.drop_index("ix_interface_metrics_collected_at", table_name="interface_metrics")
    op.drop_index(
        "ix_interface_metrics_interface_collected", table_name="interface_metrics"
    )
    op.drop_table("interface_metrics")
    op.drop_index("ix_device_metrics_collected_at", table_name="device_metrics")
    op.drop_index("ix_device_metrics_device_collected", table_name="device_metrics")
    op.drop_table("device_metrics")
    op.drop_index("ix_device_poll_runs_cycle_started_at", table_name="device_poll_runs")
    op.drop_table("device_poll_runs")
