"""wave 4: sessions (server-side login sessions)

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-06

Server-side login sessions (W04-T002, SYSTEM_SPEC.md §21/§23).

- `sessions`: one row per login. `id` IS the opaque bearer token carried by
  the HttpOnly/SameSite=Lax cookie; it is resolved server-side, so a
  restart or a logout invalidates it durably. Expiry is doubly bounded:
  `expires_at` = created + 7 days (absolute, never extended) and
  `last_seen_at` + 12 hours (idle, slid on every authenticated request).
- `ix_sessions_expires_at` supports expired-session purging.

All timestamps are TIMESTAMPTZ (§3/§23).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_table("sessions")
