"""wave 3: weekly_reports + report_jobs

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-05

Persistent report responsibility (W03-T009, SYSTEM_SPEC.md §4/§23).

- `report_jobs`: one generation task per attempt-generation. A partial
  UNIQUE index on `week_code` over the ACTIVE statuses
  (pending/running/failed) makes "同一统计周最多允许一个活跃生成任务"
  (§4.3) a database guarantee, while still allowing a NEW job for a week
  whose previous job already succeeded (manual regenerate, §4.4).
- `weekly_reports`: the long-term per-week registry (§24) — the one
  current DOCX per week (§4.4) plus its generation status/error. A failed
  generation never overwrites a `success` row: the old file stays
  downloadable until a new one replaces it atomically (§4.4/§5).

All timestamps are TIMESTAMPTZ (§3/§23).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("week_code", sa.Text(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending",
        ),  # pending / running / failed / succeeded
        sa.Column(
            "trigger",
            sa.Text(),
            nullable=False,
            server_default="scheduled",
        ),  # scheduled / manual
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default="now()"
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default="now()"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_report_jobs_week_code", "report_jobs", ["week_code"])
    op.create_index(
        "uq_report_jobs_active_week",
        "report_jobs",
        ["week_code"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running', 'failed')"),
    )
    op.create_index("ix_report_jobs_status", "report_jobs", ["status"])

    op.create_table(
        "weekly_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("week_code", sa.Text(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),  # success / failed
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default="now()"
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default="now()"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_weekly_reports_week_code", "weekly_reports", ["week_code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_weekly_reports_week_code", table_name="weekly_reports")
    op.drop_table("weekly_reports")
    op.drop_index("ix_report_jobs_status", table_name="report_jobs")
    op.drop_index("uq_report_jobs_active_week", table_name="report_jobs")
    op.drop_index("ix_report_jobs_week_code", table_name="report_jobs")
    op.drop_table("report_jobs")
