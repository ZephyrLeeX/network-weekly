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
  an attempt renders a separate, validated candidate DOCX and the
  current file is replaced only after the database recorded the success,
  so ANY database failure leaves the previous bytes untouched (§4.4/§5);
- every candidate is bound to its own generation: its name carries the
  `report_jobs` id (`...docx.candidate.<job_id>`), and only that exact
  job can ever authorize its install (W03-AUDIT-3);
- the committed success and the file switch are crash-consistent: a
  success row always names the current path, and a candidate left behind
  by an interrupted install is completed (or discarded) by
  `reconcile_report_files` on the next loop pass — installed only when
  the bound job is the one behind the week's CURRENT success
  (job succeeded and its `finished_at` equals the row's `generated_at`),
  never for an older success of the week;
- a lost success reply (the commit landed, the caller saw an error)
  resolves to SUCCEEDED: the job's own candidate is installed and no
  retry is scheduled — the job is never degraded back to FAILED;
- an undeterminable commit state (the database is unreachable when the
  attempt's success is queried after the success update failed) resolves
  to nothing at all: the job-bound candidate is kept, no FAILED is
  recorded, no retry is scheduled and the row keeps its current state —
  the next pass's recovery/reconciliation arbitrates (W03-AUDIT-4), and
  `_mark_failure` additionally refuses to ever downgrade a `succeeded`
  job if a failure record reaches it anyway;
- an install failure after the committed success is a diagnosable
  non-terminal state: the success stays, the candidate survives, the
  job row records the pending switch in `last_error`, and reconciliation
  completes (and clears) it on a later pass; a marker whose switch
  demonstrably completed (or was superseded) but whose clearing commit
  was lost — no candidate left to resolve — is cleared by the next
  pass's reconciliation too, so the diagnosable state never outlives
  its cause (W03-AUDIT-4);
- report statistics read only persisted data (§27.10) — the executor
  never contacts a device.

`next_retry_at` carries the 10-minute retry schedule (§4.3); a fresh
pending job has `next_retry_at = now` (runnable immediately).
"""

import logging
import os
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
from backend.reporting.docx import (
    CANDIDATE_SUFFIX,
    RenderedReport,
    install_report,
    is_unbound_candidate_name,
    parse_candidate_name,
    render_report_candidate,
)
from backend.reporting.period import (
    BUSINESS_TIMEZONE,
    ReportPeriod,
    is_complete,
    period_for_iso_week,
)
from backend.reporting.service import build_weekly_report_data

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

# Outcome of one execution attempt. STATUS_INSTALL_PENDING marks the
# crash-consistency recovery window: the success IS committed, but the
# current DOCX is not switched yet (reconciliation owns the switch) — it
# must never be reported as an unqualified full success (W03-AUDIT-3).
STATUS_INSTALL_PENDING = "succeeded_install_pending"
# Outcome of one attempt whose success-commit state could not be
# determined: the database was unreachable when the attempt's success was
# queried after the success update failed, so the attempt is neither
# succeeded nor verifiably failed. Nothing is recorded and nothing is
# scheduled — the job-bound candidate is kept and the next pass's
# recovery/reconciliation arbitrates (W03-AUDIT-4).
STATUS_COMMIT_UNKNOWN = "commit_unknown"

TRIGGER_SCHEDULED = "scheduled"
TRIGGER_MANUAL = "manual"

REPORT_STATUS_SUCCESS = "success"
REPORT_STATUS_FAILED = "failed"

_MAX_ERROR_LENGTH = 500
# Database error text can embed SQL or DSN details; the persisted summary
# stays generic — full detail goes to the (secret-redacted) worker log.
_DATABASE_ERROR_TEXT = "database error during report generation (see worker logs)"
# Persisted on the job row while the committed success's file switch is
# still owed (no schema change — the existing job error field carries it);
# cleared by reconciliation once the switch completes.
_INSTALL_PENDING_ERROR = (
    "success committed but the current DOCX is not switched yet "
    "(install pending; reconciliation will retry)"
)
# Returned (never persisted — the database is unreachable by definition)
# when the success-commit state is undeterminable, so the loop sees an
# unresolved attempt instead of an ordinary failure.
_COMMIT_UNKNOWN_ERROR = (
    "success commit state unknown (database unavailable); the job-bound "
    "candidate is kept for the next pass to arbitrate"
)


class ReportJobError(RuntimeError):
    """Raised when a report job cannot be requested (e.g. week incomplete)."""


@dataclass(frozen=True)
class ReportOutcome:
    """The result of one execution attempt of a report job."""

    job_id: int
    week_code: str
    # STATUS_SUCCEEDED / STATUS_FAILED / STATUS_INSTALL_PENDING /
    # STATUS_COMMIT_UNKNOWN.
    status: str
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
    if job.status == STATUS_SUCCEEDED:
        # A terminal success is never degraded — a landed success commit
        # whose reply was lost must not become FAILED + 10-minute retry
        # just because the database recovered before a failure record was
        # attempted (W03-AUDIT-4). Defensive: the commit-unknown path no
        # longer records failures at all.
        logger.error(
            "report job %d is already succeeded; refusing to record a failure (%s)",
            job_id,
            error,
        )
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


def _install_committed(
    session_factory: sessionmaker, rendered: RenderedReport, job_id: int, *, now: datetime
) -> bool:
    """Switch the current DOCX after the success commit (§4.4/§5).

    A switch failure here (e.g. a volume hiccup) does NOT undo the
    terminal success: the validated candidate survives on disk and
    `reconcile_report_files` completes the switch on a later pass, so the
    committed success never outlives its file bytes. The unfinished
    switch is made diagnosable on the job row (`last_error`, existing
    field — no schema change); returns True only when the file actually
    switched.
    """

    try:
        install_report(rendered)
        return True
    except OSError as exc:
        logger.error(
            "report job %d: switching the current DOCX %s failed (%s); the validated "
            "candidate stays for the next reconciliation pass",
            job_id,
            rendered.path.name,
            type(exc).__name__,
        )
        _record_install_pending(session_factory, job_id, rendered, exc, now=now)
        return False


def _record_install_pending(
    session_factory: sessionmaker,
    job_id: int,
    rendered: RenderedReport,
    exc: OSError,
    *,
    now: datetime,
) -> None:
    """Persist the owed file switch on the job row (best effort, §4.4).

    The job stays `succeeded` — the success is terminal and reconciliation
    owns the retry — but the row says plainly that the current DOCX is not
    switched yet, so the state is never mistaken for fully complete.
    """

    try:
        with session_factory() as session:
            job = session.get(ReportJob, job_id)
            if job is None:
                return
            job.last_error = (
                f"{_INSTALL_PENDING_ERROR} (candidate {rendered.candidate.name} kept; "
                f"last switch error: {type(exc).__name__})"
            )[:_MAX_ERROR_LENGTH]
            job.updated_at = now
            session.commit()
    except SQLAlchemyError:
        logger.error(
            "report job %d: could not persist the install-pending state; "
            "reconciliation still retries the switch from the candidate",
            job_id,
        )


def _success_commit_state(
    session_factory: sessionmaker, job_id: int, week_code: str, *, now: datetime
) -> str:
    """Whether THIS attempt's success is durably committed ("committed"),
    verifiably not ("absent"), or undeterminable because the database is
    still unreachable ("unknown") — after the success update raised (§4.4).

    "committed" requires the exact pair `_mark_success` writes in one
    transaction: the job `succeeded` with `finished_at == now` AND the
    week's `weekly_reports` success row with `generated_at == now`. An
    older success of the week never satisfies this — a failed attempt's
    candidate must not be authorized by it (W03-AUDIT-3).
    """

    try:
        with session_factory() as session:
            job = session.get(ReportJob, job_id)
            if (
                job is None
                or job.status != STATUS_SUCCEEDED
                or job.finished_at is None
                or job.finished_at != now
            ):
                return "absent"
            row = session.execute(
                select(WeeklyReport).where(WeeklyReport.week_code == week_code)
            ).scalar_one_or_none()
            if (
                row is not None
                and row.status == REPORT_STATUS_SUCCESS
                and row.generated_at is not None
                and row.generated_at == now
            ):
                return "committed"
            return "absent"
    except SQLAlchemyError:
        return "unknown"


def _discard_candidate(rendered: RenderedReport, job_id: int) -> None:
    """Remove the candidate of a verifiably uncommitted attempt (§4.4).

    A failed attempt leaves no files behind and the current report keeps
    its old bytes. If the discard itself fails the candidate survives on
    disk — job-bound, so reconciliation discards it on a later pass.
    """

    try:
        rendered.candidate.unlink(missing_ok=True)
        logger.info(
            "report job %d: discarded the abandoned candidate %s",
            job_id,
            rendered.candidate.name,
        )
    except OSError:
        logger.error(
            "report job %d: could not discard candidate %s; reconciliation will resolve it",
            job_id,
            rendered.candidate.name,
        )


def _candidate_authorizes_install(
    job: ReportJob | None, week_code: str, row: WeeklyReport | None
) -> bool:
    """True only when `job` is the generation behind the week's CURRENT
    success — the only authority that may install its candidate (§4.4).

    Requires the job to exist, to belong to the candidate's week, to be
    `succeeded`, and the week's success row to be exactly this job's
    commit (`generated_at == job.finished_at`, the pair `_mark_success`
    writes in one transaction). A previous success of the week therefore
    never authorizes a failed/running regeneration's candidate, and a
    candidate superseded by a newer success is never installed over it
    (W03-AUDIT-3).
    """

    return (
        job is not None
        and job.week_code == week_code
        and job.status == STATUS_SUCCEEDED
        and job.finished_at is not None
        and row is not None
        and row.status == REPORT_STATUS_SUCCESS
        and row.file_path is not None
        and row.generated_at is not None
        and row.generated_at == job.finished_at
    )


def _candidate_exists(output_dir: Path, job_id: int) -> bool:
    """True when a candidate bound to `job_id` is still in `output_dir`."""

    return any(
        parsed is not None and parsed[1] == job_id
        for parsed in (
            parse_candidate_name(candidate.name)
            for candidate in output_dir.glob("*" + CANDIDATE_SUFFIX + "*")
        )
    )


def _clear_completed_install_pending(
    session: Session, output_dir: Path, *, now: datetime
) -> bool:
    """Clear a succeeded job's install-pending marker once nothing is owed.

    The atomic switch and the clearing of the marker are two separate
    commits, and the marker itself is written best-effort: a database
    failure in between leaves a job whose candidate is already gone (the
    `os.replace` succeeded) while the row still claims a switch is owed —
    and with no candidate left, the candidate resolution above can never
    see it again, so the diagnosable state would survive forever. The
    next pass therefore clears the marker when the database and the
    directory agree nothing is owed anymore (W03-AUDIT-4):

    - the job's own commit IS the week's current success
      (`_candidate_authorizes_install`) and its recorded current DOCX
      exists with no candidate of this job beside it — the switch
      completed;
    - a strictly newer success of the week superseded the job — its
      candidate can never be installed, so nothing is owed.

    A missing current DOCX keeps the marker: there the diagnosis is
    still true. Returns True when a row was cleared.
    """

    pending = (
        session.execute(
            select(ReportJob).where(
                ReportJob.status == STATUS_SUCCEEDED,
                ReportJob.last_error.is_not(None),
            )
        )
        .scalars()
        .all()
    )
    changed = False
    for job in pending:
        if job.last_error is None or not job.last_error.startswith(_INSTALL_PENDING_ERROR):
            continue
        row = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == job.week_code)
        ).scalar_one_or_none()
        if _candidate_authorizes_install(job, job.week_code, row):
            assert row is not None and row.file_path is not None  # authorized above
            if not Path(row.file_path).exists():
                continue  # the current DOCX is gone — the diagnosis still holds
        elif not (
            row is not None
            and row.status == REPORT_STATUS_SUCCESS
            and row.generated_at is not None
            and job.finished_at is not None
            and row.generated_at > job.finished_at
        ):
            continue  # not the job's own success and not superseded — keep it
        if _candidate_exists(output_dir, job.id):
            continue  # the candidate resolution above owns it
        logger.info(
            "report job %d: install-pending marker cleared (nothing is owed for "
            "%s anymore)",
            job.id,
            job.week_code,
        )
        job.last_error = None
        job.updated_at = now
        changed = True
    return changed


def reconcile_report_files(
    session: Session, output_dir: Path, *, now: datetime | None = None
) -> int:
    """Complete or discard candidate DOCXs left by an interrupted install.

    A crash (or a lost terminal update) between the committed success and
    the atomic file switch leaves the validated candidate beside the
    current report. Each candidate is resolved strictly through its bound
    job: installed only when the job it names is the one behind the
    week's CURRENT success (succeeded job, `generated_at == finished_at`
    on the success row) — completing the interrupted switch, including
    for a first generation that has no current file yet (§4.4).
    Anything else is not authorized to become current and is removed,
    never left behind and never installed over a newer success:

    - a candidate whose job never committed (failed/running/recovered
      attempt, e.g. after a database outage);
    - a candidate superseded by a newer success of the week;
    - an unbound legacy candidate (no job binding to verify);
    - a candidate whose job row disappeared.

    An installed candidate clears the job's install-pending `last_error`
    (the owed switch is done); a discarded one clears it too (nothing is
    owed anymore). A marker whose candidate is already gone — the switch
    (or the superseding discard) happened but its clearing commit was
    lost — is resolved as well, strictly from the database and the
    directory (W03-AUDIT-4). Returns how many candidates were resolved.
    Safe to run at every pass: with no candidates and no stale marker it
    is a directory listing and one indexed query only.
    """

    output_dir = Path(output_dir)
    resolved = 0
    changed = False
    for candidate in sorted(output_dir.glob("*" + CANDIDATE_SUFFIX + "*")):
        parsed = parse_candidate_name(candidate.name)
        if parsed is None:
            if not is_unbound_candidate_name(candidate.name):
                logger.warning("ignoring unrecognized report candidate %s", candidate.name)
                continue
            logger.warning(
                "discarding unbound legacy report candidate %s (no job to authorize it)",
                candidate.name,
            )
            candidate.unlink(missing_ok=True)
            resolved += 1
            continue
        week_code, job_id = parsed
        job = session.get(ReportJob, job_id)
        row = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == week_code)
        ).scalar_one_or_none()
        if _candidate_authorizes_install(job, week_code, row):
            assert row is not None and row.file_path is not None  # authorized above
            logger.info(
                "installing committed report candidate %s for job %d", candidate.name, job_id
            )
            os.replace(candidate, Path(row.file_path))
        else:
            logger.info(
                "discarding report candidate %s (job %d is not the week's committed "
                "success)",
                candidate.name,
                job_id,
            )
            candidate.unlink(missing_ok=True)
        if job is not None and job.status == STATUS_SUCCEEDED and job.last_error is not None:
            job.last_error = None
            job.updated_at = now or datetime.now(UTC)
            changed = True
        resolved += 1
    if _clear_completed_install_pending(session, output_dir, now=now or datetime.now(UTC)):
        changed = True
    if changed:
        session.commit()
    return resolved


def execute_report_job(
    session_factory: sessionmaker,
    job_id: int,
    output_dir: Path,
    *,
    now: datetime | None = None,
    render: Callable[..., RenderedReport] = render_report_candidate,
) -> ReportOutcome:
    """Run one report job end-to-end (§4/§5/§27.10).

    Marks the job running, builds the statistics from persisted data,
    renders the DOCX as a validated, job-bound candidate (§4.4), commits
    the database success and only THEN atomically installs the candidate
    as the current report. The order is the consistency guarantee: any
    database failure happens while the previous report bytes are still
    untouched (§4.4/§5), and a failed attempt records a sanitized error
    with a 10-minute `next_retry_at` (§4.3).

    A success commit whose reply was lost resolves to SUCCEEDED — the
    job's own candidate is installed and nothing is retried; when the
    commit state cannot be determined (the database is unreachable for
    the verification query) the attempt resolves to STATUS_COMMIT_UNKNOWN
    — the candidate stays job-bound and nothing is recorded or scheduled,
    so the next pass's recovery/reconciliation arbitrates; an install
    failure keeps the success terminal but reports
    STATUS_INSTALL_PENDING until reconciliation switches the file.
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
        rendered = render(data, Path(output_dir), job_id=job_id)
    except Exception as exc:  # noqa: BLE001 — every failure must be recorded (§4.3)
        return _record_failed_attempt(session_factory, job_id, week_code, exc, now=at)

    try:
        with session_factory() as session:
            _mark_success(session, job_id, rendered, now=at)
    except Exception as exc:  # noqa: BLE001 — every failure must be recorded (§4.3)
        # The current DOCX was never touched. First establish what the
        # database actually holds for THIS attempt: a commit that landed
        # despite the lost reply is terminal — install this job's own
        # candidate and report success (never FAILED, never a retry,
        # W03-AUDIT-3). When the database is unreachable the commit state
        # is undeterminable: keep the job-bound candidate, record nothing,
        # schedule nothing and leave the row exactly as it is — the next
        # pass's recovery (a stranded `running` row re-enters the retry)
        # and reconciliation (a landed success gets its candidate
        # installed) arbitrate, and a FAILED downgrade becomes impossible
        # even for the window where the database is back first (§4.4,
        # W03-AUDIT-4). Otherwise the candidate belongs to a verifiably
        # failed attempt: discarded, and the failure recorded (§4.3).
        commit_state = _success_commit_state(session_factory, job_id, week_code, now=at)
        if commit_state == "committed":
            logger.warning(
                "report job %d: success commit for %s landed despite the lost reply; "
                "installing its candidate",
                job_id,
                week_code,
            )
            installed = _install_committed(session_factory, rendered, job_id, now=at)
            return ReportOutcome(
                job_id=job_id,
                week_code=week_code,
                status=STATUS_SUCCEEDED if installed else STATUS_INSTALL_PENDING,
                error=None if installed else _INSTALL_PENDING_ERROR,
                report_path=str(rendered.path),
            )
        if commit_state == "unknown":
            logger.warning(
                "report job %d: commit state for %s unknown (database unavailable); "
                "candidate %s kept, nothing recorded, the next pass arbitrates",
                job_id,
                week_code,
                rendered.candidate.name,
            )
            return ReportOutcome(
                job_id=job_id,
                week_code=week_code,
                status=STATUS_COMMIT_UNKNOWN,
                error=_COMMIT_UNKNOWN_ERROR,
            )
        _discard_candidate(rendered, job_id)
        return _record_failed_attempt(session_factory, job_id, week_code, exc, now=at)

    installed = _install_committed(session_factory, rendered, job_id, now=at)
    if installed:
        logger.info("report job %d succeeded: %s", job_id, rendered.path.name)
    return ReportOutcome(
        job_id=job_id,
        week_code=week_code,
        status=STATUS_SUCCEEDED if installed else STATUS_INSTALL_PENDING,
        error=None if installed else _INSTALL_PENDING_ERROR,
        report_path=str(rendered.path),
    )


def _record_failed_attempt(
    session_factory: sessionmaker,
    job_id: int,
    week_code: str,
    exc: BaseException,
    *,
    now: datetime,
) -> ReportOutcome:
    """Sanitized failure summary + persisted failure for one attempt (§4.3)."""

    summary = _error_summary(exc)
    logger.error("report job %d failed: %s", job_id, summary, exc_info=exc)
    _record_failure(session_factory, job_id, summary, now=now)
    return ReportOutcome(job_id=job_id, week_code=week_code, status=STATUS_FAILED, error=summary)


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
