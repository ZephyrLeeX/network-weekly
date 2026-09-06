"""Persistent weekly-report jobs: schedule, retry, regenerate
(W03-T009/T010, SYSTEM_SPEC.md §4/§27).

The report generation responsibility lives in PostgreSQL, never in an
in-memory timer (§4.2): `report_jobs` rows survive worker restarts, a
worker that boots mid-week recovers the responsibility, and failures are
retried every 10 minutes until success (§4.3).

Invariants (§27):

- one ACTIVE job per statistics week — pending/running/failed — enforced
  by the partial unique index `uq_report_jobs_active_week` (§4.3);
- at most one generation executing at a time: jobs run inline on the
  worker's report loop thread;
- a `running` row is always a leftover (worker restart, or a terminal
  success/failure update lost to a database outage): the loop re-runs
  recovery on its passes, so a job can never be stranded without a retry
  (§4.2/§4.3);
- a failed regeneration never destroys the previous successful DOCX —
  the `weekly_reports` success row and its file stay untouched until a
  new attempt atomically replaces the file (§4.4/§5);
- report statistics read only persisted data (§27.10) — the executor
  never contacts a device.

`next_retry_at` carries the 10-minute retry schedule (§4.3); a fresh
pending job has `next_retry_at = now` (runnable immediately).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.db.models import ReportJob, WeeklyReport
from backend.reporting.docx import RenderedReport, render_report_docx
from backend.reporting.period import (
    BUSINESS_TIMEZONE,
    ReportPeriod,
    is_complete,
    period_for_iso_week,
)
from backend.reporting.service import WeeklyReportData, build_weekly_report_data

logger = logging.getLogger(__name__)

# SQL predicate mirrored from the partial unique index (models/migration 0008).
_ACTIVE_WHERE = sa_text("status IN ('pending', 'running', 'failed')")

# §4.3: retry a failed generation every 10 minutes until it succeeds.
RETRY_DELAY = timedelta(minutes=10)
# §4.1: Monday 00:10 Asia/Shanghai == period end (Monday 00:00) + 10 minutes.
GENERATION_DELAY = timedelta(minutes=10)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_FAILED = "failed"
STATUS_SUCCEEDED = "succeeded"
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_RUNNING, STATUS_FAILED)

TRIGGER_SCHEDULED = "scheduled"
TRIGGER_MANUAL = "manual"

REPORT_STATUS_SUCCESS = "success"
REPORT_STATUS_FAILED = "failed"

_MAX_ERROR_LENGTH = 500
# Database error text can embed SQL or DSN details; the persisted summary
# stays generic — full detail goes to the (secret-redacted) worker log.
_DATABASE_ERROR_TEXT = "database error during report generation (see worker logs)"


class ReportJobError(RuntimeError):
    """Raised when a report job cannot be requested (e.g. week incomplete)."""


@dataclass(frozen=True)
class ReportOutcome:
    """The result of one execution attempt of a report job."""

    job_id: int
    week_code: str
    status: str  # STATUS_SUCCEEDED / STATUS_FAILED
    error: str | None = None
    report_path: str | None = None


def _error_summary(exc: BaseException) -> str:
    """Sanitized, readable failure summary for persistence (§4.3/§22.2)."""

    if isinstance(exc, SQLAlchemyError):
        return f"{type(exc).__name__}: {_DATABASE_ERROR_TEXT}"
    text = f"{type(exc).__name__}: {exc}"
    return text[:_MAX_ERROR_LENGTH]


def _active_job(session: Session, week_code: str) -> ReportJob | None:
    return session.execute(
        select(ReportJob)
        .where(ReportJob.week_code == week_code, ReportJob.status.in_(ACTIVE_STATUSES))
        .order_by(ReportJob.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()


def _insert_job(
    session: Session,
    period: ReportPeriod,
    *,
    trigger: str,
    now: datetime,
) -> ReportJob:
    """Insert one job; a concurrent active job for the week wins the race."""

    session.execute(
        pg_insert(ReportJob)
        .values(
            week_code=period.week_code,
            period_start=period.start,
            period_end=period.end,
            status=STATUS_PENDING,
            trigger=trigger,
            attempts=0,
            next_retry_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["week_code"],
            index_where=_ACTIVE_WHERE,
        )
    )
    session.commit()
    job = _active_job(session, period.week_code)
    assert job is not None  # the insert above either created or found one
    return job



def ensure_scheduled_job(
    session: Session, period: ReportPeriod, *, now: datetime
) -> ReportJob | None:
    """Idempotently create the §4.1 Monday job for one completed week.

    Returns the existing active job when one is present, None when the
    week already has a successful report (nothing left to do).
    """

    if not is_complete(period, now):
        raise ReportJobError(
            f"cannot schedule a report for an incomplete week {period.week_code}"
        )
    succeeded = session.execute(
        select(WeeklyReport).where(
            WeeklyReport.week_code == period.week_code,
            WeeklyReport.status == REPORT_STATUS_SUCCESS,
        )
    ).scalar_one_or_none()
    if succeeded is not None:
        return None
    existing = _active_job(session, period.week_code)
    if existing is not None:
        return existing
    job = _insert_job(session, period, trigger=TRIGGER_SCHEDULED, now=now)
    logger.info("scheduled weekly report job %d for %s", job.id, period.week_code)
    return job


def request_regenerate(
    session: Session, period: ReportPeriod, *, now: datetime
) -> ReportJob:
    """§4.4 manual regenerate: idempotent, never duplicates active work.

    If an active job already owns the week it is returned unchanged
    (§27.4); otherwise a fresh manual job is created. The current DOCX of
    the week (if any) stays downloadable until the new attempt succeeds.
    """

    if not is_complete(period, now):
        raise ReportJobError(
            f"cannot regenerate an incomplete week {period.week_code}"
        )
    existing = _active_job(session, period.week_code)
    if existing is not None:
        return existing
    job = _insert_job(session, period, trigger=TRIGGER_MANUAL, now=now)
    logger.info("manual regenerate job %d for %s", job.id, period.week_code)
    return job


def recover_stale_running_jobs(session: Session, *, now: datetime) -> int:
    """Reset jobs stuck in `running` back to pending (§4.2).

    Generations run inline on the report loop's thread, so a `running` row
    can only be a leftover: the previous worker died mid-generation, or its
    terminal success/failure update was lost to a database outage. The
    attempt is re-run rather than lost. Returns how many jobs were
    recovered.
    """

    stuck = session.execute(
        select(ReportJob).where(ReportJob.status == STATUS_RUNNING)
    ).scalars().all()
    for job in stuck:
        job.status = STATUS_PENDING
        job.last_error = "generation interrupted (restart or lost update); retrying"
        job.next_retry_at = now
        job.updated_at = now
        logger.warning("recovered stale running report job %d (%s)", job.id, job.week_code)
    if stuck:
        session.commit()
    return len(stuck)


def _load_period(job: ReportJob) -> ReportPeriod:
    """Rebuild the period in the business timezone (§3).

    Stored TIMESTAMPTZ values come back as UTC instants; ISO week identity
    must be computed on Asia/Shanghai wall-clock time, so convert first.
    """

    start = job.period_start.astimezone(BUSINESS_TIMEZONE)
    end = job.period_end.astimezone(BUSINESS_TIMEZONE)
    period = ReportPeriod(start=start, end=end)
    if period.week_code != job.week_code:
        raise ReportJobError(
            f"job week_code {job.week_code} does not match its period "
            f"({period.week_code}); refusing to generate the wrong week"
        )
    return period


def _mark_success(
    session: Session, job_id: int, rendered: RenderedReport, *, now: datetime
) -> None:
    job = session.get(ReportJob, job_id)
    if job is None:
        raise ReportJobError(f"report job {job_id} disappeared mid-generation")
    session.execute(
        pg_insert(WeeklyReport)
        .values(
            week_code=job.week_code,
            period_start=job.period_start,
            period_end=job.period_end,
            status=REPORT_STATUS_SUCCESS,
            file_path=str(rendered.path),
            generated_at=now,
            last_error=None,
        )
        .on_conflict_do_update(
            index_elements=["week_code"],
            set_={
                "status": REPORT_STATUS_SUCCESS,
                "file_path": str(rendered.path),
                "generated_at": now,
                "last_error": None,
                "updated_at": now,
            },
        )
    )
    job.status = STATUS_SUCCEEDED
    job.last_error = None
    job.next_retry_at = None
    job.finished_at = now
    job.updated_at = now
    session.commit()


def _mark_failure(
    session: Session, job_id: int, error: str, *, now: datetime
) -> None:
    job = session.get(ReportJob, job_id)
    if job is None:
        logger.error("report job %d disappeared; cannot record failure", job_id)
        return
    job.status = STATUS_FAILED
    job.last_error = error
    job.next_retry_at = now + RETRY_DELAY
    job.updated_at = now
    # Record the failure for the report list (§20.2), but NEVER overwrite a
    # success row: the old DOCX stays current/downloadable (§4.4).
    session.execute(
        pg_insert(WeeklyReport)
        .values(
            week_code=job.week_code,
            period_start=job.period_start,
            period_end=job.period_end,
            status=REPORT_STATUS_FAILED,
            file_path=None,
            generated_at=None,
            last_error=error,
        )
        .on_conflict_do_nothing(index_elements=["week_code"])
    )
    session.commit()


def _record_failure(
    session_factory: sessionmaker, job_id: int, error: str, *, now: datetime
) -> bool:
    """Record a failed attempt; when even that cannot be persisted, requeue.

    The generation DID fail, but a database outage can also swallow the
    failure update — and a job left in `running` with `next_retry_at = NULL`
    would never be picked up again. The fallback resets it to `pending`
    with the 10-minute `next_retry_at` (§4.3), so the loop keeps retrying
    without a worker restart; if even the reset fails, the loop's recovery
    pass re-collects the stranded `running` row later (§4.2). Returns True
    once the job is out of `running`.
    """

    try:
        with session_factory() as session:
            _mark_failure(session, job_id, error, now=now)
        return True
    except SQLAlchemyError as exc:
        logger.error(
            "report job %d failure could not be recorded (%s); queueing a retry",
            job_id,
            type(exc).__name__,
        )
    try:
        with session_factory() as session:
            job = session.get(ReportJob, job_id)
            if job is None or job.status != STATUS_RUNNING:
                return False
            job.status = STATUS_PENDING
            job.last_error = error
            job.next_retry_at = now + RETRY_DELAY
            job.updated_at = now
            session.commit()
            return True
    except SQLAlchemyError as exc:
        logger.error(
            "report job %d could not be queued for retry (%s); recovery retries next pass",
            job_id,
            type(exc).__name__,
        )
        return False


def execute_report_job(
    session_factory: sessionmaker,
    job_id: int,
    output_dir: Path,
    *,
    now: datetime | None = None,
    render: Callable[[WeeklyReportData, Path], RenderedReport] = render_report_docx,
) -> ReportOutcome:
    """Run one report job end-to-end (§4/§5/§27.10).

    Marks the job running, builds the statistics from persisted data,
    renders the DOCX through the atomic §5 flow and records success — or
    records a sanitized failure with a 10-minute `next_retry_at` (§4.3).
    Any failure leaves an existing successful report untouched (§4.4).
    """

    at = now or datetime.now(UTC)
    with session_factory() as session:
        job = session.get(ReportJob, job_id)
        if job is None:
            raise ReportJobError(f"report job {job_id} does not exist")
        if job.status == STATUS_SUCCEEDED:
            return ReportOutcome(job_id=job.id, week_code=job.week_code, status=STATUS_SUCCEEDED)
        week_code = job.week_code
        job.status = STATUS_RUNNING
        job.attempts += 1
        if job.started_at is None:
            job.started_at = at
        job.next_retry_at = None
        job.updated_at = at
        session.commit()

    try:
        with session_factory() as session:
            job = session.get(ReportJob, job_id)
            assert job is not None
            period = _load_period(job)
            data = build_weekly_report_data(session, period, generated_at=at)
        rendered = render(data, Path(output_dir))
        with session_factory() as session:
            _mark_success(session, job_id, rendered, now=at)
        logger.info("report job %d succeeded: %s", job_id, rendered.path.name)
        return ReportOutcome(
            job_id=job_id,
            week_code=week_code,
            status=STATUS_SUCCEEDED,
            report_path=str(rendered.path),
        )
    except Exception as exc:  # noqa: BLE001 — every failure must be recorded (§4.3)
        summary = _error_summary(exc)
        logger.exception("report job %d failed: %s", job_id, summary)
        _record_failure(session_factory, job_id, summary, now=at)
        return ReportOutcome(
            job_id=job_id, week_code=week_code, status=STATUS_FAILED, error=summary
        )


def due_jobs(session: Session, *, now: datetime) -> list[ReportJob]:
    """Active jobs whose retry time has come (§4.3), oldest first."""

    return list(
        session.execute(
            select(ReportJob)
            .where(
                ReportJob.status.in_((STATUS_PENDING, STATUS_FAILED)),
                (ReportJob.next_retry_at.is_(None)) | (ReportJob.next_retry_at <= now),
            )
            .order_by(ReportJob.created_at.asc(), ReportJob.id.asc())
        )
        .scalars()
        .all()
    )


def load_weekly_reports(session: Session) -> list[WeeklyReport]:
    """All weekly report rows, newest week first (§20.2 ordering)."""

    return list(
        session.execute(
            select(WeeklyReport).order_by(WeeklyReport.period_start.desc())
        ).scalars()
    )


def period_of_job(job: ReportJob) -> ReportPeriod:
    """The period a job refers to (business-timezone-safe, §3)."""

    return _load_period(job)


def report_period_for(week_code: str) -> ReportPeriod:
    """Parse an ISO week code back into its period (e.g. for regenerate)."""

    year_text, week_text = week_code.split("-W")
    return period_for_iso_week(int(year_text), int(week_text))
