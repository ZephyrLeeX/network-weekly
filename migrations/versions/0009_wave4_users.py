"""wave 4: users (single administrator)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-06

Single local administrator account (W04-T001, SYSTEM_SPEC.md §21/§23).

- `users`: one row for the one local administrator. The password is stored
  ONLY as a salted scrypt hash (self-describing `scrypt$N$r$p$salt$hash`
  string); the plaintext never reaches the database.
- `uq_users_single_admin` (unique on `singleton`, always true) makes
  "系统只有一个本地管理员账号" a database guarantee, not just a
  service-level convention. `username` is unique as well.

All timestamps are TIMESTAMPTZ (§3/§23).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("singleton", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("uq_users_single_admin", "users", ["singleton"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_single_admin", table_name="users")
    op.drop_table("users")
