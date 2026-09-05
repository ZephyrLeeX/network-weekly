"""wave 2: irf_member_observations

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-05

IRF member observation history (W02-T008, SYSTEM_SPEC.md §17/§23). One row
per member per successful ~15-minute observation: `observed` distinguishes
present/missing members, `role`/`previous_role`/`role_changed` record
reliably identified role changes. Long-term record (§24) — retention never
touches it. All timestamps are TIMESTAMPTZ (§3/§23).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "irf_member_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("member_id", sa.SmallInteger(), nullable=False),
        sa.Column("observed", sa.Boolean(), nullable=False),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("previous_role", sa.Text(), nullable=True),
        sa.Column("role_changed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default="now()"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_irf_member_observations_device_member_time",
        "irf_member_observations",
        ["device_id", "member_id", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_irf_member_observations_device_member_time",
        table_name="irf_member_observations",
    )
    op.drop_table("irf_member_observations")
