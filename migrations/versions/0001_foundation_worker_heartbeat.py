"""foundation: worker_heartbeat table

Revision ID: 0001
Revises:
Create Date: 2026-09-04

Wave 0 foundation schema: the persistent worker heartbeat required by
W00-T005 (SYSTEM_SPEC.md §23). Device/interface/metric tables arrive with
the Wave 1/2 migrations. All timestamps are TIMESTAMPTZ (SYSTEM_SPEC.md §3).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeat",
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Text(), nullable=True),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("worker_id"),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeat")
