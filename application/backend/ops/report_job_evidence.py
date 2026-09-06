"""Scheduled weekly-report job history for the A09 acceptance item (§4.1).

Used by `scripts/acceptance_evidence.sh` via
`python -m backend.ops.report_job_evidence`. Strictly read-only.

A09 (two consecutive Monday 00:10 Asia/Shanghai automatic reports) must be
judged from `report_jobs` HISTORY — rows with `trigger='scheduled'` and
their `created_at` — never from `weekly_reports.generated_at`: the registry
is the CURRENT success per week, and a later manual regenerate overwrites
`generated_at`, destroying the original scheduled timing (W05-AUDIT-2).
A succeeded scheduled `report_jobs` row is append-only history, so it keeps
proving the original automatic generation across any number of regenerates.

The evidence lines therefore print, per recent scheduled job: week, status,
attempts, the expected Monday 00:10 instant for that week (the period end —
next Monday 00:00 — plus the §4.1 ten minutes), and the actual
created/started/finished times — all in Asia/Shanghai. `on_schedule`
annotates the ±5 min loop tolerance (the worker's 60 s loop creates the job
shortly after 00:10); the human acceptance judgment stays with
docs/ACCEPTANCE.md. Error texts and other free-form fields are deliberately
not printed.

The summary lines at the bottom list which weeks have an ON-TIME succeeded
scheduled attempt (trigger=scheduled + status=succeeded AND created within
±5 min of that week's Monday 00:10 — all three, per docs/ACCEPTANCE.md A09;
W05-PRE-ACCEPTANCE-HARDENING: a late scheduled success no longer counts,
which the old summary silently did) and whether two of them are consecutive
ISO weeks — the shape A09 needs. `weekly_reports`/DOCX remain corroboration
that a week ended with a downloadable success, but they can never prove the
trigger or its timing.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, select

from backend.db.engine import get_engine
from backend.db.models import ReportJob
from backend.reporting.jobs import STATUS_SUCCEEDED, TRIGGER_SCHEDULED

#: A09 judges Asia/Shanghai wall-clock times (§3, Monday 00:10).
BUSINESS_TIMEZONE: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")

#: §4.1: the scheduled job is created at Monday 00:10 after the period end.
SCHEDULE_OFFSET: Final[timedelta] = timedelta(minutes=10)

#: Tolerance for the worker's 60 s scheduling loop (original A09 wording).
ON_SCHEDULE_TOLERANCE: Final[timedelta] = timedelta(minutes=5)

#: How many recent scheduled job rows to show (all attempts of several
#: weeks; the newest attempts first). Read-only bound, not a judgment.
MAX_ROWS: Final[int] = 24


@dataclass(frozen=True)
class ScheduledJobEvidence:
    """One scheduled `report_jobs` row, with its expected creation instant."""

    job_id: int
    week_code: str
    status: str
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    expected_at: datetime


def scheduled_jobs(conn: Connection, *, max_rows: int = MAX_ROWS) -> list[ScheduledJobEvidence]:
    """The recent `trigger='scheduled'` job rows, newest attempts first."""

    rows = conn.execute(
        select(
            ReportJob.id,
            ReportJob.week_code,
            ReportJob.status,
            ReportJob.attempts,
            ReportJob.created_at,
            ReportJob.started_at,
            ReportJob.finished_at,
            ReportJob.period_end,
        )
        .where(ReportJob.trigger == TRIGGER_SCHEDULED)
        .order_by(ReportJob.week_code.desc(), ReportJob.created_at.desc())
        .limit(max_rows)
    ).all()
    return [
        ScheduledJobEvidence(
            job_id=row.id,
            week_code=row.week_code,
            status=row.status,
            attempts=row.attempts,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            # §4.1: the job for the completed week is created once `now`
            # passes the NEXT Monday 00:10 — period end (Monday 00:00) plus
            # ten minutes.
            expected_at=row.period_end + SCHEDULE_OFFSET,
        )
        for row in rows
    ]


def is_on_schedule(evidence: ScheduledJobEvidence) -> bool:
    """Whether the job was created within ±tolerance of its Monday 00:10."""

    return (
        evidence.expected_at - ON_SCHEDULE_TOLERANCE
        <= evidence.created_at
        <= evidence.expected_at + ON_SCHEDULE_TOLERANCE
    )


def _local(instant: datetime | None) -> str:
    if instant is None:
        return "-"
    return instant.astimezone(BUSINESS_TIMEZONE).isoformat()


def format_job_line(evidence: ScheduledJobEvidence) -> str:
    """One evidence line for the collector output."""

    return (
        f"job_id={evidence.job_id} week={evidence.week_code} "
        f"trigger={TRIGGER_SCHEDULED} status={evidence.status} "
        f"attempts={evidence.attempts} "
        f"expected={_local(evidence.expected_at)} created={_local(evidence.created_at)} "
        f"started={_local(evidence.started_at)} finished={_local(evidence.finished_at)} "
        f"on_schedule={'yes' if is_on_schedule(evidence) else 'no'}"
    )


def scheduled_on_time_succeeded_weeks(
    evidence: list[ScheduledJobEvidence],
) -> list[str]:
    """Distinct week codes whose scheduled job BOTH succeeded AND was
    created on time (Monday 00:10 ±5 min), oldest first.

    A09 (docs/ACCEPTANCE.md) needs all three: trigger=scheduled (guaranteed
    by `scheduled_jobs`), status=succeeded, and `is_on_schedule` — a late
    scheduled success is honest evidence but does not satisfy A09, so it
    must not feed the consecutive-weeks summary
    (W05-PRE-ACCEPTANCE-HARDENING).
    """

    weeks = {
        item.week_code
        for item in evidence
        if item.status == STATUS_SUCCEEDED and is_on_schedule(item)
    }
    return sorted(weeks)


def next_week_code(week_code: str) -> str:
    """The ISO week code following `week_code` (handles year wrap)."""

    year, week = week_code.split("-W")
    next_monday = date.fromisocalendar(int(year), int(week), 1) + timedelta(days=7)
    iso = next_monday.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def consecutive_scheduled_pairs(
    weeks: list[str],
) -> list[tuple[str, str]]:
    """Adjacent pairs of the given weeks that are consecutive ISO weeks."""

    ordered = sorted(weeks)
    return [
        (first, second)
        for first, second in zip(ordered, ordered[1:], strict=False)
        if next_week_code(first) == second
    ]


def render_scheduled_job_evidence(
    evidence: list[ScheduledJobEvidence],
    *,
    max_rows: int = MAX_ROWS,
) -> str:
    """The full A09 evidence block, newline-terminated."""

    lines = [format_job_line(item) for item in evidence]
    if not lines:
        lines.append("no scheduled report jobs recorded")
    weeks = scheduled_on_time_succeeded_weeks(evidence)
    lines.append(
        f"weeks with an on-time succeeded scheduled job: {', '.join(weeks) or 'none'}"
    )
    pairs = consecutive_scheduled_pairs(weeks)
    lines.append(
        "two consecutive on-time scheduled+succeeded weeks (A09): "
        + (
            f"yes ({' -> '.join(pairs[0])})"
            if pairs
            else "no"
        )
    )
    lines.append(f"scheduled job rows listed: {len(evidence)} (recent {max_rows})")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    """CLI entry point (`python -m backend.ops.report_job_evidence`)."""

    if len(argv) != 1:
        print("usage: python -m backend.ops.report_job_evidence", file=sys.stderr)
        return 2
    with get_engine().connect() as conn:
        print(render_scheduled_job_evidence(scheduled_jobs(conn)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
