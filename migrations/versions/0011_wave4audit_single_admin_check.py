"""wave 4 audit: single-admin database invariant hardening

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-06

W04-AUDIT (SYSTEM_SPEC.md §21/§23): the old `uq_users_single_admin` unique
index on the BOOLEAN `singleton` column admitted one `true` row AND one
`false` row — two administrator rows could coexist. Adding
`ck_users_singleton_true` (CHECK `singleton IS TRUE`) closes that hole:
every row must now claim the one singleton slot, so the unique index makes
"at most one user row" a real database guarantee while a `singleton=false`
row is refused outright. Existing rows are all `singleton=true`
(`initialize_admin` never wrote anything else), so this is safe to apply
over a populated table and every existing administrator keeps working.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_users_singleton_true",
        "users",
        sa.text("singleton IS TRUE"),
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_singleton_true", "users", type_="check")
