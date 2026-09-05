"""wave 2: system_settings

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-05

Configurable monitoring thresholds (W02-T007, SYSTEM_SPEC.md §14: "阈值必须
可配置" — CPU/memory/utilization >= 80% for 15 minutes by default). A plain
key-value table; keys and value ranges are validated by
`backend.monitoring.thresholds`. All timestamps are TIMESTAMPTZ (§3/§23).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default="now()",
        ),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
