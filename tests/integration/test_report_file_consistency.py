"""Integration tests for the regenerate file/database consistency guarantee
(W03-AUDIT follow-up, §4.4/§5) over migrated PostgreSQL.

The invariant under test: a failed regenerate never destroys the previous
successful DOCX. One attempt renders a validated candidate DOCX, commits
the database success, and only then atomically switches the current file —
so every database failure happens while the old bytes are still on disk,
and a switch interrupted after the commit is completed (or its candidate
discarded) by reconciliation.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.db.engine import get_session_factory
from backend.db.models import ReportJob, WeeklyReport
from backend.reporting import jobs as reporting_jobs
from backend.reporting.docx import RenderedReport, candidate_file_name
from backend.reporting.jobs import (
    RETRY_DELAY,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    execute_report_job,
    reconcile_report_files,
    request_regenerate,
)
from backend.reporting.period import period_for_iso_week, report_file_name
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


def test_lost_success_reply_still_installs_the_committed_report(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A success commit whose confirmation was lost installs its candidate.

    The database row is the terminal authority: when the commit actually
    landed, the rendered candidate is the only copy of the succeeded
    report and must be installed, not discarded — even though the attempt
    itself is recorded as failed and retried (§4.4).
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
    assert outcome.status == STATUS_FAILED

    # The committed success is visible and its candidate was installed.
    assert current.read_bytes() != bytes_a  # switched to B, not destroyed
    assert sorted(p.name for p in tmp_path.iterdir()) == [CURRENT_NAME]  # no residue
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"  # never degraded by the failure path
        assert report.generated_at == later
        failed_job = session.get(ReportJob, regenerate_id)
        assert failed_job is not None
        assert failed_job.status == STATUS_FAILED  # attempt retried anyway
        assert failed_job.next_retry_at == later + RETRY_DELAY

    # The retry then completes cleanly and stays consistent.
    monkeypatch.undo()
    retry_at = later + RETRY_DELAY
    retried = execute_report_job(get_session_factory(), regenerate_id, tmp_path, now=retry_at)
    assert retried.status == STATUS_SUCCEEDED
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.generated_at == retry_at
    assert sorted(p.name for p in tmp_path.iterdir()) == [CURRENT_NAME]


def test_interrupted_install_completed_by_next_loop_pass(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A switch failure after the success commit is recovered, not lost.

    The committed success stays terminal with its candidate; the old DOCX
    bytes stay on disk until the next pass's reconciliation completes the
    atomic switch.
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
    assert outcome.status == STATUS_SUCCEEDED  # the DB success stays terminal

    # Candidate B survives beside the still-current A (recovery window).
    assert current.read_bytes() == bytes_a  # old bytes untouched
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        CURRENT_NAME,
        candidate_file_name(PERIOD),
    ]
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"
        assert report.generated_at == later  # committed, file pending

    # The next pass reconciles: installs B from the candidate, cleans up,
    # and schedules nothing new for the already-succeeded week.
    monkeypatch.undo()
    loop = WeeklyReportLoop(get_session_factory(), tmp_path)
    loop._clock = lambda: later
    loop.run_pass()
    assert current.read_bytes() != bytes_a  # the switch completed
    assert sorted(p.name for p in tmp_path.iterdir()) == [CURRENT_NAME]  # no residue
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"
        assert report.file_path == str(current)
        assert report.generated_at == later  # the committed success, now on disk
        assert session.query(ReportJob).count() == 2  # no duplicate job scheduled


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

    def failing_render(data: object, output_dir: Path) -> RenderedReport:
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


def test_reconcile_completes_success_and_discards_abandoned(
    db_engine: Engine, tmp_path: Path
) -> None:
    """reconcile_report_files: success row -> install; no success -> discard;
    unrecognized files are never touched."""

    other_period = period_for_iso_week(2026, 37)
    orphan = tmp_path / candidate_file_name(other_period)
    orphan.write_bytes(b"orphan candidate")
    foreign = tmp_path / "unrelated.candidate"
    foreign.write_bytes(b"not ours")

    with Session(db_engine) as session:
        resolved = reconcile_report_files(session, tmp_path)
    assert resolved == 1
    assert not orphan.exists()  # no success row -> garbage, removed
    assert foreign.exists()  # not a report candidate -> left alone

    # A success row whose install never happened gets completed.
    with Session(db_engine) as session:
        session.add(
            WeeklyReport(
                week_code=other_period.week_code,
                period_start=other_period.start,
                period_end=other_period.end,
                status="success",
                file_path=str(tmp_path / report_file_name(other_period)),
                generated_at=NOW,
            )
        )
        session.commit()
    orphan.write_bytes(b"validated candidate bytes")
    with Session(db_engine) as session:
        resolved = reconcile_report_files(session, tmp_path)
    assert resolved == 1
    assert (tmp_path / report_file_name(other_period)).read_bytes() == b"validated candidate bytes"
    assert not orphan.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        report_file_name(other_period),
        "unrelated.candidate",
    ]
