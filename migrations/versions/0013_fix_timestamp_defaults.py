"""Fix frozen timestamp column defaults (production hotfix).

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-28

Migrations 0002-0008 declared their ``created_at`` / ``updated_at`` columns
with the plain Python string ``server_default="now()"``. Alembic renders a
plain string as a quoted literal, so the DDL carried ``DEFAULT 'now()'`` and
PostgreSQL resolved that special date/time input ONCE — while the DDL
executed — storing a constant timestamp in ``pg_attrdef`` (production
evidence: ``'2026-09-23 07:34:24.520293+00'::timestamp with time zone``).
Every later INSERT therefore re-used the migration-time instant instead of
evaluating ``now()`` per statement: after days of runtime, all
``device_poll_runs.created_at`` and ``report_jobs.created_at`` values still
carried the install moment even though ``cycle_started_at`` had advanced to
2026-09-27/28. Alembic runs a whole upgrade batch in one transaction, so
every affected table froze to the SAME constant.

This migration re-points the DEFAULT of the 16 affected columns to the
dynamic SQL expression ``now()`` via catalog-only
``ALTER COLUMN ... SET DEFAULT``: no type, nullability, index or constraint
change, no table rebuild, safe to run online on a 0012 database. Tables
created correctly from the start (0009/0010/0012 already used
``sa.text("now()")``) are not touched.

Historical row values are deliberately NOT rewritten: the real insert/update
times can no longer be reconstructed (``cycle_started_at`` / ``started_at``
/ ``generated_at`` are business fields, not row-creation instants) and the
weekly statistics authoritatively read those business fields. Only rows
inserted after this migration receive correct defaults.

The downgrade restores the 0012-era schema SHAPE only: the plain-string
default again freezes to a constant at the moment the downgrade DDL runs.
It cannot reproduce the original install-time constant, never rewrites
historical rows, and is not a production data remedy.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Columns whose DEFAULT was frozen to the migration-time constant by the
# plain-string server_default of migrations 0002-0008.
FROZEN_TIMESTAMP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("devices", "created_at"),
    ("devices", "updated_at"),
    ("device_members", "created_at"),
    ("interfaces", "created_at"),
    ("aggregation_members", "created_at"),
    ("device_poll_runs", "created_at"),
    ("device_monitoring_state", "updated_at"),
    ("device_reachability_incidents", "created_at"),
    ("interface_monitoring_state", "updated_at"),
    ("interface_state_incidents", "created_at"),
    ("system_settings", "updated_at"),
    ("irf_member_observations", "created_at"),
    ("report_jobs", "created_at"),
    ("report_jobs", "updated_at"),
    ("weekly_reports", "created_at"),
    ("weekly_reports", "updated_at"),
)


def upgrade() -> None:
    for table_name, column_name in FROZEN_TIMESTAMP_COLUMNS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        )


def downgrade() -> None:
    # Schema-only: back to the 0012 shape — a quoted literal that PostgreSQL
    # freezes when THIS DDL runs. Historical rows are never rewritten and the
    # original install-time constant is not (and cannot be) restored.
    for table_name, column_name in FROZEN_TIMESTAMP_COLUMNS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.DateTime(timezone=True),
            server_default="now()",
        )
