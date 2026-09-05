"""wave 2: interface_state_incidents + interface_monitoring_state

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-05

Priority-interface state semantics (W02-T006, SYSTEM_SPEC.md §13/§23):
`interface_state_incidents` holds confirmed interface Down/Recovery records
(long-term, never touched by retention); `interface_monitoring_state` is the
per-interface valid-sample tracking row behind the 2-valid-cycle rules.
All timestamps are TIMESTAMPTZ (§3/§23).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interface_monitoring_state",
        sa.Column("interface_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="normal"),
        sa.Column(
            "consecutive_down_samples", sa.SmallInteger(), nullable=False,
            server_default="0",
        ),
        sa.Column(
            "consecutive_up_samples", sa.SmallInteger(), nullable=False,
            server_default="0",
        ),
        sa.Column("down_run_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("up_run_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default="now()",
        ),
        sa.ForeignKeyConstraint(["interface_id"], ["interfaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("interface_id"),
    )

    op.create_table(
        "interface_state_incidents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "interface_id",
            sa.Integer(),
            sa.ForeignKey("interfaces.id", ondelete="CASCADE"),
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
        "ix_interface_state_incidents_interface",
        "interface_state_incidents",
        ["interface_id", "recovered_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_interface_state_incidents_interface", table_name="interface_state_incidents"
    )
    op.drop_table("interface_state_incidents")
    op.drop_table("interface_monitoring_state")
