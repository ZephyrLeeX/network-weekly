"""Integration tests for report candidate/attempt consistency (W03-AUDIT-2/-3,
§4.4/§5) over migrated PostgreSQL.

The invariants under test:

- a failed regenerate never destroys the previous successful DOCX: one
  attempt renders a validated candidate DOCX, commits the database
  success, and only then atomically switches the current file;
- every candidate is bound to its exact generating `report_jobs` id
  (`...docx.candidate.<job_id>`) and reconciliation installs it only for
  THAT job's own committed success — a previous success of the week
  never authorizes a failed attempt's candidate, and a superseded
  candidate never overwrites a newer success;
- a success commit whose reply was lost resolves to SUCCEEDED (the
  candidate is installed, no FAILED downgrade, no retry);
- an install failure after the commit is diagnosable (job `last_error`,
  STATUS_INSTALL_PENDING outcome) and is completed by the next pass's
  reconciliation — never reported as an unqualified full success.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.db.engine import get_session_factory
from backend.db.models import ReportJob, WeeklyReport
from backend.reporting import jobs as reporting_jobs
from backend.reporting.docx import RenderedReport, candidate_file_name, render_report_candidate
from backend.reporting.jobs import (
    RETRY_DELAY,
    STATUS_FAILED,
    STATUS_INSTALL_PENDING,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    due_jobs,
    execute_report_job,
    reconcile_report_files,
    request_regenerate,
)
from backend.reporting.period import (
    ReportPeriod,
    period_for_iso_week,
    report_file_name,
)
from backend.reporting.schedule import WeeklyReportLoop

pytestmark = pytest.mark.integration

PERIOD = period_for_iso_week(2026, 36)  # [2026-08-31, 2026-09-07) Shanghai
NOW = datetime(2026, 9, 7, 0, 11, 0, tzinfo=UTC)  # Monday 00:11 +08
CURRENT_NAME = report_file_name(PERIOD)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(ReportJob).delete()
        session.query(WeeklyReport).delete()
        session.commit()
        yield


def _current_report(db_engine: Engine) -> WeeklyReport:
    with Session(db_engine) as session:
        return session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()


class _OutageSession:
    """A session whose every database call fails (database outage)."""

    def __enter__(self) -> _OutageSession:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, *args: object, **kwargs: object) -> None:
        raise SQLAlchemyError("connection refused")

    def execute(self, *args: object, **kwargs: object) -> None:
        raise SQLAlchemyError("connection refused")

    def commit(self) -> None:
        raise SQLAlchemyError("connection refused")


class _OutageAfterOpenFactory:
    """Session factory that models a database outage from open N onward.

    Opens before `fail_from_open` reach the real database; from that open
    on, every call fails — the wire dies between two statements, exactly
    like a real outage mid-generation.
    """

    def __init__(self, healthy: sessionmaker) -> None:
        self._healthy = healthy
        self.opens = 0
        self.fail_from_open: int | None = None

    def __call__(self) -> object:
        self.opens += 1
        if self.fail_from_open is not None and self.opens >= self.fail_from_open:
            return _OutageSession()
        return self._healthy()


def test_success_db_failure_keeps_old_docx_until_retry_succeeds(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The required end-to-end consistency scenario (§4.4).

    A  ->  regenerate renders candidate B  ->  the success update fails
    ->  job FAILED, the registry still reports A, the current DOCX bytes
    are still A, no candidate/temp leftovers  ->  retry  ->  only the
    succeeded retry switches the current DOCX to B.
    """

    # Existing successful report A.
    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=NOW)
        first_id = job.id
    first = execute_report_job(get_session_factory(), first_id, tmp_path, now=NOW)
    assert first.status == STATUS_SUCCEEDED
    current = tmp_path / CURRENT_NAME
    bytes_a = current.read_bytes()

    # Manual regenerate one hour later.
    later = NOW + timedelta(hours=1)
    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=later)
        assert job.trigger == "manual"
        regenerate_id = job.id

    def exploding_mark_success(
        session: Session, job_id: int, rendered: RenderedReport, *, now: datetime
    ) -> None:
        raise SQLAlchemyError("connection lost")

    monkeypatch.setattr(reporting_jobs, "_mark_success", exploding_mark_success)
    outcome = execute_report_job(get_session_factory(), regenerate_id, tmp_path, now=later)
    assert outcome.status == STATUS_FAILED
    assert "database error" in (outcome.error or "")

    # The candidate B was rendered, but the database failure must not have
    # replaced anything: registry and bytes still report A, no leftovers.
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"
        assert report.file_path == str(current)
        assert report.generated_at == NOW  # still A's generation time
        failed_job = session.get(ReportJob, regenerate_id)
        assert failed_job is not None
        assert failed_job.status == STATUS_FAILED
        assert failed_job.next_retry_at == later + RETRY_DELAY  # §4.3 cadence
    assert current.read_bytes() == bytes_a  # current DOCX bytes == A
    assert sorted(p.name for p in tmp_path.iterdir()) == [CURRENT_NAME]  # no residue

    # The retry (DB healthy again) succeeds and only now switches to B.
    monkeypatch.undo()
    retry_at = later + RETRY_DELAY
    retried = execute_report_job(get_session_factory(), regenerate_id, tmp_path, now=retry_at)
    assert retried.status == STATUS_SUCCEEDED
    assert current.read_bytes() != bytes_a  # current DOCX bytes are now B
    assert sorted(p.name for p in tmp_path.iterdir()) == [CURRENT_NAME]  # one current DOCX
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"
        assert report.file_path == str(current)
        assert report.generated_at == retry_at  # registry points at B
        retried_job = session.get(ReportJob, regenerate_id)
        assert retried_job is not None
        assert retried_job.status == STATUS_SUCCEEDED


def test_outage_kept_candidate_is_never_installed_and_retry_redoes_it(
    db_engine: Engine, tmp_path: Path
) -> None:
    """The required W03-AUDIT-3 scenario: a candidate of an attempt whose
    success never committed survives a database outage on disk, but the
    old success A never authorizes it (§4.4).

    A  ->  regenerate B  ->  B's candidate rendered  ->  `_mark_success`
    uncommitted AND the database stays unreachable (the kept candidate
    cannot be resolved in-process)  ->  next pass, DB recovered:
    reconciliation must NOT install B — the current bytes stay A and B
    re-enters the normal retry, whose own success is what switches the
    file to B.
    """

    factory = _OutageAfterOpenFactory(get_session_factory())

    # Existing successful report A (job 1).
    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=NOW)
        first_id = job.id
    first = execute_report_job(factory, first_id, tmp_path, now=NOW)  # type: ignore[arg-type]
    assert first.status == STATUS_SUCCEEDED
    current = tmp_path / CURRENT_NAME
    bytes_a = current.read_bytes()

    # Manual regenerate B (job 2).
    later = NOW + timedelta(hours=1)
    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=later)
        regenerate_id = job.id

    # The database dies the moment B's candidate exists: `_mark_success`
    # cannot commit and even the failure record is swallowed by the
    # outage, so the job stays `running` and the job-bound candidate is
    # kept on disk for reconciliation.
    def render_then_outage(data: object, output_dir: Path, *, job_id: int) -> RenderedReport:
        rendered = render_report_candidate(data, output_dir, job_id=job_id)  # type: ignore[arg-type]
        factory.fail_from_open = factory.opens + 1  # first failing open = next one
        return rendered

    outcome = execute_report_job(
        factory,  # type: ignore[arg-type]
        regenerate_id, tmp_path, now=later, render=render_then_outage
    )
    assert outcome.status == STATUS_FAILED
    assert "database error" in (outcome.error or "")
    candidate_b = tmp_path / candidate_file_name(PERIOD, regenerate_id)
    assert candidate_b.exists()  # kept — resolution was impossible
    assert current.read_bytes() == bytes_a  # A untouched
    with Session(db_engine) as session:
        stuck = session.get(ReportJob, regenerate_id)
        assert stuck is not None
        assert stuck.status == STATUS_RUNNING  # terminal update lost
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.generated_at == NOW  # the row still names A only

    # Next pass with the database recovered: the running row is recovered,
    # the kept candidate is resolved by its job binding — job 2 is NOT the
    # committed success, so reconciliation must discard it, never install.
    recovery = later + RETRY_DELAY
    loop = WeeklyReportLoop(get_session_factory(), tmp_path, clock=lambda: recovery)
    assert loop.recover_once() == 1
    assert loop.reconcile_files() == 1
    assert not candidate_b.exists()  # discarded, NOT installed
    assert current.read_bytes() == bytes_a  # current bytes still A
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"
        assert report.file_path == str(current)
        assert report.generated_at == NOW  # A — never B's uncommitted bytes
        requeued = session.get(ReportJob, regenerate_id)
        assert requeued is not None
        assert requeued.status == STATUS_PENDING  # back into the retry queue
        assert requeued.next_retry_at == recovery

    # B's retry then succeeds normally and only now switches A -> B.
    retried = execute_report_job(get_session_factory(), regenerate_id, tmp_path, now=recovery)
    assert retried.status == STATUS_SUCCEEDED
    assert current.read_bytes() != bytes_a
    assert sorted(p.name for p in tmp_path.iterdir()) == [CURRENT_NAME]
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.generated_at == recovery
        done = session.get(ReportJob, regenerate_id)
        assert done is not None
        assert done.status == STATUS_SUCCEEDED


def test_lost_success_reply_returns_succeeded_without_retry(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A success commit whose confirmation was lost resolves to SUCCEEDED.

    The database row is the terminal authority: when the commit actually
    landed, the rendered candidate is the only copy of the succeeded
    report — it is installed, the job stays `succeeded`, and the attempt
    is neither recorded as FAILED nor given a 10-minute retry
    (W03-AUDIT-3).
    """

    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=NOW)
        first_id = job.id
    first = execute_report_job(get_session_factory(), first_id, tmp_path, now=NOW)
    assert first.status == STATUS_SUCCEEDED
    current = tmp_path / CURRENT_NAME
    bytes_a = current.read_bytes()

    later = NOW + timedelta(hours=1)
    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=later)
        regenerate_id = job.id

    real_mark_success = reporting_jobs._mark_success

    def commit_then_lose_reply(
        session: Session, job_id: int, rendered: RenderedReport, *, now: datetime
    ) -> None:
        real_mark_success(session, job_id, rendered, now=now)  # commit lands…
        raise SQLAlchemyError("connection lost after commit")  # …reply is lost

    monkeypatch.setattr(reporting_jobs, "_mark_success", commit_then_lose_reply)
    outcome = execute_report_job(get_session_factory(), regenerate_id, tmp_path, now=later)
    assert outcome.status == STATUS_SUCCEEDED  # never degraded to FAILED
    assert outcome.error is None

    # The committed success is visible and its candidate was installed.
    assert current.read_bytes() != bytes_a  # switched to B, not destroyed
    assert sorted(p.name for p in tmp_path.iterdir()) == [CURRENT_NAME]  # no residue
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"
        assert report.generated_at == later
        succeeded_job = session.get(ReportJob, regenerate_id)
        assert succeeded_job is not None
        assert succeeded_job.status == STATUS_SUCCEEDED  # stayed terminal
        assert succeeded_job.next_retry_at is None  # no retry scheduled
        assert succeeded_job.last_error is None
        assert due_jobs(session, now=later + RETRY_DELAY) == []  # nothing to retry

    # A later regenerate still runs cleanly on top of the recovered state.
    even_later = later + timedelta(hours=1)
    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=even_later)
        third_id = job.id
    third = execute_report_job(get_session_factory(), third_id, tmp_path, now=even_later)
    assert third.status == STATUS_SUCCEEDED
    assert sorted(p.name for p in tmp_path.iterdir()) == [CURRENT_NAME]


def test_install_failure_after_commit_is_diagnosable_and_reconciled(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The required W03-AUDIT-3 scenario: an `os.replace` failure after the
    DB success is a diagnosable recovery window, not a full success.

    B's success commits  ->  install_report raises OSError  ->  the
    current DOCX stays A, candidate B survives, the job row says plainly
    that the switch is owed  ->  the next pass's reconciliation installs
    B and clears the diagnostic — only then do the current bytes change.
    """

    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=NOW)
        first_id = job.id
    first = execute_report_job(get_session_factory(), first_id, tmp_path, now=NOW)
    assert first.status == STATUS_SUCCEEDED
    current = tmp_path / CURRENT_NAME
    bytes_a = current.read_bytes()

    later = NOW + timedelta(hours=1)
    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=later)
        regenerate_id = job.id

    def failing_switch(rendered: RenderedReport) -> None:
        raise OSError("reports volume hiccup")

    monkeypatch.setattr(reporting_jobs, "install_report", failing_switch)
    outcome = execute_report_job(get_session_factory(), regenerate_id, tmp_path, now=later)
    # The DB success stays terminal, but the unfinished switch is NEVER
    # reported as an unqualified success.
    assert outcome.status == STATUS_INSTALL_PENDING
    assert outcome.error is not None and "install pending" in outcome.error

    # Candidate B survives beside the still-current A (recovery window).
    candidate_b = tmp_path / candidate_file_name(PERIOD, regenerate_id)
    assert current.read_bytes() == bytes_a  # old bytes untouched
    assert candidate_b.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == [CURRENT_NAME, candidate_b.name]
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"
        assert report.generated_at == later  # committed, file switch pending
        assert report.file_path == str(current)
        pending_job = session.get(ReportJob, regenerate_id)
        assert pending_job is not None
        assert pending_job.status == STATUS_SUCCEEDED  # terminal, NOT retried by due_jobs
        assert pending_job.next_retry_at is None
        assert pending_job.last_error is not None
        assert "install pending" in pending_job.last_error  # diagnosable on the row
        assert due_jobs(session, now=later + RETRY_DELAY) == []

    # The next pass reconciles: installs B from the candidate, clears the
    # owed-switch diagnostic, and schedules nothing new.
    monkeypatch.undo()
    loop = WeeklyReportLoop(get_session_factory(), tmp_path, clock=lambda: later)
    loop.run_pass()
    assert current.read_bytes() != bytes_a  # only NOW the switch completed
    assert sorted(p.name for p in tmp_path.iterdir()) == [CURRENT_NAME]  # no residue
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"
        assert report.file_path == str(current)
        assert report.generated_at == later  # the committed success, now on disk
        done = session.get(ReportJob, regenerate_id)
        assert done is not None
        assert done.status == STATUS_SUCCEEDED
        assert done.last_error is None  # diagnostic cleared by the install
        assert session.query(ReportJob).count() == 2  # no duplicate job scheduled


def test_stale_candidate_cannot_override_a_newer_success(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An old job's never-installed candidate is discarded once a newer
    success of the week exists — it must never be installed over it
    (W03-AUDIT-3)."""

    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=NOW)
        first_id = job.id
    first = execute_report_job(get_session_factory(), first_id, tmp_path, now=NOW)
    assert first.status == STATUS_SUCCEEDED
    current = tmp_path / CURRENT_NAME
    bytes_a = current.read_bytes()

    # B commits its success but its install fails (candidate kept).
    later = NOW + timedelta(hours=1)
    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=later)
        stale_id = job.id

    def failing_switch(rendered: RenderedReport) -> None:
        raise OSError("reports volume hiccup")

    monkeypatch.setattr(reporting_jobs, "install_report", failing_switch)
    stale = execute_report_job(get_session_factory(), stale_id, tmp_path, now=later)
    assert stale.status == STATUS_INSTALL_PENDING
    stale_candidate = tmp_path / candidate_file_name(PERIOD, stale_id)
    assert stale_candidate.exists()

    # C regenerates later and succeeds fully: current bytes become C.
    monkeypatch.undo()
    even_later = later + timedelta(hours=1)
    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=even_later)
        fresh_id = job.id
    fresh = execute_report_job(get_session_factory(), fresh_id, tmp_path, now=even_later)
    assert fresh.status == STATUS_SUCCEEDED
    bytes_c = current.read_bytes()
    assert bytes_c != bytes_a
    assert stale_candidate.exists()  # B's candidate still lingers

    # Reconciliation resolves the stale candidate through its job binding:
    # job B succeeded, but the week's success row is C's commit — B is not
    # authorized, its candidate is discarded and C stays current.
    loop = WeeklyReportLoop(get_session_factory(), tmp_path, clock=lambda: even_later)
    assert loop.reconcile_files() == 1
    assert not stale_candidate.exists()
    assert current.read_bytes() == bytes_c  # the newer success is untouched
    assert sorted(p.name for p in tmp_path.iterdir()) == [CURRENT_NAME]
    with Session(db_engine) as session:
        superseded = session.get(ReportJob, stale_id)
        assert superseded is not None
        assert superseded.status == STATUS_SUCCEEDED
        assert superseded.last_error is None  # the owed switch resolved by supersede
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.generated_at == even_later  # still C's commit


def test_first_generation_db_failure_leaves_no_files(db_engine: Engine, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """First generation (no old report): a database failure leaves nothing
    behind, and the retry creates the first current DOCX correctly."""

    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=NOW)
        job_id = job.id

    def exploding_mark_success(
        session: Session, job_id: int, rendered: RenderedReport, *, now: datetime
    ) -> None:
        raise SQLAlchemyError("connection lost")

    monkeypatch.setattr(reporting_jobs, "_mark_success", exploding_mark_success)
    outcome = execute_report_job(get_session_factory(), job_id, tmp_path, now=NOW)
    assert outcome.status == STATUS_FAILED
    # No current DOCX existed, none was created, no candidate/temp remains.
    assert list(tmp_path.iterdir()) == []
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "failed"
        assert report.file_path is None

    monkeypatch.undo()
    retried = execute_report_job(
        get_session_factory(), job_id, tmp_path, now=NOW + RETRY_DELAY
    )
    assert retried.status == STATUS_SUCCEEDED
    assert sorted(p.name for p in tmp_path.iterdir()) == [CURRENT_NAME]  # the first DOCX
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"
        assert Path(report.file_path or "").read_bytes()  # complete DOCX on disk


def test_render_failure_keeps_old_report_without_candidate(
    db_engine: Engine, tmp_path: Path
) -> None:
    """An ordinary render failure never touches the old report and never
    leaves a candidate (the candidate only exists once render succeeded)."""

    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=NOW)
        first_id = job.id
    first = execute_report_job(get_session_factory(), first_id, tmp_path, now=NOW)
    assert first.status == STATUS_SUCCEEDED
    current = tmp_path / CURRENT_NAME
    bytes_a = current.read_bytes()

    later = NOW + timedelta(hours=1)
    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=later)
        regenerate_id = job.id

    def failing_render(data: object, output_dir: Path, *, job_id: int) -> RenderedReport:
        raise RuntimeError("render exploded")

    outcome = execute_report_job(
        get_session_factory(), regenerate_id, tmp_path, now=later, render=failing_render
    )
    assert outcome.status == STATUS_FAILED
    assert current.read_bytes() == bytes_a
    assert sorted(p.name for p in tmp_path.iterdir()) == [CURRENT_NAME]
    report = _current_report(db_engine)
    assert report.status == "success"
    assert report.file_path == str(current)
    assert report.generated_at == NOW


def test_reconcile_resolves_candidates_strictly_through_their_job(
    db_engine: Engine, tmp_path: Path
) -> None:
    """reconcile_report_files (W03-AUDIT-3): a candidate is installed only
    for the exact succeeded job behind the week's CURRENT success row;
    uncommitted, superseded, orphaned and unbound candidates are
    discarded; foreign files are never touched."""

    other = period_for_iso_week(2026, 37)
    newer = period_for_iso_week(2026, 38)
    failed_week = period_for_iso_week(2026, 39)
    legacy_week = period_for_iso_week(2026, 40)

    def plant_job(week_code: str, period: ReportPeriod, status: str,
                  finished_at: datetime | None) -> int:
        with Session(db_engine) as session:
            job = ReportJob(
                week_code=week_code,
                period_start=period.start,
                period_end=period.end,
                status=status,
                trigger="manual",
                attempts=1,
                finished_at=finished_at,
            )
            session.add(job)
            session.commit()
            return int(job.id)

    t1 = NOW
    t2 = NOW + timedelta(hours=1)

    # Week 37: job succeeded and IS the week's current success -> install.
    j37 = plant_job(other.week_code, other, STATUS_SUCCEEDED, t1)
    with Session(db_engine) as session:
        session.add(
            WeeklyReport(
                week_code=other.week_code,
                period_start=other.start,
                period_end=other.end,
                status="success",
                file_path=str(tmp_path / report_file_name(other)),
                generated_at=t1,  # == job37.finished_at: authorized
            )
        )
        # Week 38: job succeeded but a NEWER success superseded it -> discard.
        session.add(
            WeeklyReport(
                week_code=newer.week_code,
                period_start=newer.start,
                period_end=newer.end,
                status="success",
                file_path=str(tmp_path / report_file_name(newer)),
                generated_at=t2,  # != job38.finished_at (t1): superseded
            )
        )
        # Week 39: only a failed row -> nothing authorizes its candidate.
        session.add(
            WeeklyReport(
                week_code=failed_week.week_code,
                period_start=failed_week.start,
                period_end=failed_week.end,
                status="failed",
                file_path=None,
                generated_at=None,
                last_error="boom",
            )
        )
        session.commit()

    j38 = plant_job(newer.week_code, newer, STATUS_SUCCEEDED, t1)
    j39 = plant_job(failed_week.week_code, failed_week, STATUS_FAILED, None)

    authorized = tmp_path / candidate_file_name(other, j37)
    authorized.write_bytes(b"committed week-37 bytes")
    superseded = tmp_path / candidate_file_name(newer, j38)
    superseded.write_bytes(b"stale week-38 bytes")
    uncommitted = tmp_path / candidate_file_name(failed_week, j39)
    uncommitted.write_bytes(b"never-committed week-39 bytes")
    orphaned = tmp_path / candidate_file_name(period_for_iso_week(2026, 41), 99999)
    orphaned.write_bytes(b"job row gone")
    legacy = tmp_path / f"{report_file_name(legacy_week)}.candidate"
    legacy.write_bytes(b"legacy unbound candidate")
    (tmp_path / report_file_name(newer)).write_bytes(b"current week-38 bytes")
    foreign = tmp_path / "unrelated.candidate"
    foreign.write_bytes(b"not ours")

    with Session(db_engine) as session:
        resolved = reconcile_report_files(session, tmp_path, now=t2)
    assert resolved == 5
    # Authorized: installed onto the recorded current path.
    assert (tmp_path / report_file_name(other)).read_bytes() == b"committed week-37 bytes"
    # Everything unauthorized: discarded, never installed over the current.
    assert not superseded.exists()
    assert not uncommitted.exists()
    assert not orphaned.exists()
    assert not legacy.exists()
    assert (tmp_path / report_file_name(newer)).read_bytes() == b"current week-38 bytes"
    assert foreign.exists()  # not a report candidate -> left alone
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        report_file_name(other),
        report_file_name(newer),
        "unrelated.candidate",
    ]
