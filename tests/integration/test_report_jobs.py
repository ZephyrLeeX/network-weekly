"""Integration tests for persistent report jobs: schedule, retry, restart
recovery and the one-active-job-per-week guarantee (W03-T009, §4/§27)
over migrated PostgreSQL.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from docx import Document
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.engine import get_session_factory
from backend.db.models import ReportJob, WeeklyReport
from backend.reporting.docx import RenderedReport
from backend.reporting.jobs import (
    ACTIVE_STATUSES,
    RETRY_DELAY,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    ReportJobError,
    ReportOutcome,
    due_jobs,
    ensure_scheduled_job,
    execute_report_job,
    load_weekly_reports,
    recover_stale_running_jobs,
    request_regenerate,
)
from backend.reporting.period import period_for_iso_week
from backend.reporting.schedule import (
    DEFAULT_CHECK_INTERVAL,
    WeeklyReportLoop,
    is_due_for_schedule,
)

pytestmark = pytest.mark.integration

PERIOD = period_for_iso_week(2026, 36)  # [2026-08-31, 2026-09-07) Shanghai
NOW = datetime(2026, 9, 7, 0, 11, 0, tzinfo=UTC)  # Monday 00:11 +08


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(ReportJob).delete()
        session.query(WeeklyReport).delete()
        session.commit()
        yield


def test_schedule_is_idempotent_one_active_job(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        first = ensure_scheduled_job(session, PERIOD, now=NOW)
        assert first is not None
        assert first.status == STATUS_PENDING
        assert first.trigger == "scheduled"
        assert first.next_retry_at is not None  # immediately runnable

        second = ensure_scheduled_job(session, PERIOD, now=NOW)
        assert second is not None
        assert second.id == first.id
        assert session.query(ReportJob).count() == 1


def test_schedule_skips_week_with_successful_report(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        session.add(
            WeeklyReport(
                week_code=PERIOD.week_code,
                period_start=PERIOD.start,
                period_end=PERIOD.end,
                status="success",
                file_path="/data/reports/network-weekly-report-2026-W36.docx",
                generated_at=NOW,
            )
        )
        session.commit()
        assert ensure_scheduled_job(session, PERIOD, now=NOW) is None


def test_schedule_rejects_incomplete_week(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        with pytest.raises(ReportJobError, match="incomplete week"):
            ensure_scheduled_job(session, PERIOD, now=datetime(2026, 9, 5, tzinfo=UTC))


def test_execute_success_persists_report_and_file(db_engine: Engine, tmp_path: Path) -> None:
    with Session(db_engine) as session:
        job = ensure_scheduled_job(session, PERIOD, now=NOW)
        assert job is not None
        job_id = job.id

    outcome = execute_report_job(get_session_factory(), job_id, tmp_path, now=NOW)
    assert outcome.status == STATUS_SUCCEEDED
    assert outcome.report_path is not None
    assert Path(outcome.report_path).exists()

    with Session(db_engine) as session:
        job = session.get(ReportJob, job_id)
        assert job is not None
        assert job.status == STATUS_SUCCEEDED
        assert job.attempts == 1
        assert job.finished_at is not None
        assert job.last_error is None
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"
        assert report.file_path == outcome.report_path
        assert report.generated_at is not None
        # The rendered file is a real, readable DOCX with 8 sections.
        document = Document(outcome.report_path or "")
        headings = [p.text for p in document.paragraphs if p.style and p.style.name == "Heading 1"]
        assert len(headings) == 8


def test_execute_failure_then_retry_after_ten_minutes(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Session(db_engine) as session:
        job = ensure_scheduled_job(session, PERIOD, now=NOW)
        assert job is not None
        job_id = job.id

    attempts = 0

    def failing_render(data: object, output_dir: Path) -> RenderedReport:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("render exploded")

    failed = execute_report_job(
        get_session_factory(), job_id, tmp_path, now=NOW, render=failing_render
    )
    assert failed.status == STATUS_FAILED
    assert "render exploded" in (failed.error or "")

    with Session(db_engine) as session:
        job = session.get(ReportJob, job_id)
        assert job is not None
        assert job.status == STATUS_FAILED
        assert job.attempts == 1
        assert job.last_error is not None and "render exploded" in job.last_error
        assert job.next_retry_at == NOW + RETRY_DELAY  # §4.3: 10 minutes
        # Failure visible in the report registry (§20.2 last_error).
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "failed"
        assert report.file_path is None

    # Not runnable before the retry time (§4.3: every 10 minutes).
    with Session(db_engine) as session:
        assert due_jobs(session, now=NOW + timedelta(minutes=5)) == []
        due = due_jobs(session, now=NOW + RETRY_DELAY)
        assert [j.id for j in due] == [job_id]

    succeeded = execute_report_job(get_session_factory(), job_id, tmp_path, now=NOW + RETRY_DELAY)
    assert succeeded.status == STATUS_SUCCEEDED
    assert attempts == 1  # the failing renderer ran once; retry used the real one

    with Session(db_engine) as session:
        job = session.get(ReportJob, job_id)
        assert job is not None
        assert job.status == STATUS_SUCCEEDED
        assert job.attempts == 2  # two recorded attempts (§4.3)
        assert job.next_retry_at is None
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"
        assert Path(report.file_path or "").exists()


def test_regeneration_failure_keeps_previous_success(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4.4: old DOCX stays downloadable until a new attempt succeeds."""

    with Session(db_engine) as session:
        job = ensure_scheduled_job(session, PERIOD, now=NOW)
        assert job is not None
        job_id = job.id
    first = execute_report_job(get_session_factory(), job_id, tmp_path, now=NOW)
    assert first.report_path is not None
    first_path = Path(first.report_path)
    original_bytes = first_path.read_bytes()

    # Manual regenerate creates a NEW job (previous one succeeded).
    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=NOW)
        assert job.trigger == "manual"
        assert job.status == STATUS_PENDING
        regenerate_id = job.id
        assert job_id is not None and regenerate_id != job_id

    def failing_render(data: object, output_dir: Path) -> RenderedReport:
        raise RuntimeError("regenerate boom")

    outcome = execute_report_job(
        get_session_factory(), regenerate_id, tmp_path, now=NOW, render=failing_render
    )
    assert outcome.status == STATUS_FAILED
    # The old file and its success row are untouched.
    assert first_path.read_bytes() == original_bytes
    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"
        assert report.file_path == str(first_path)
        regenerated = session.get(ReportJob, regenerate_id)
        assert regenerated is not None
        assert regenerated.status == STATUS_FAILED


def test_one_active_job_per_week_enforced_by_database(db_engine: Engine) -> None:
    from sqlalchemy.exc import IntegrityError

    with Session(db_engine) as session:
        job = ensure_scheduled_job(session, PERIOD, now=NOW)
        assert job is not None
        duplicate = ReportJob(
            week_code=PERIOD.week_code,
            period_start=PERIOD.start,
            period_end=PERIOD.end,
            status=STATUS_PENDING,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        # succeeded job is NOT active: another active one may now exist
        done = session.get(ReportJob, job.id)
        assert done is not None
        done.status = STATUS_SUCCEEDED
        done.finished_at = NOW
        session.commit()
        fresh = ReportJob(
            week_code=PERIOD.week_code,
            period_start=PERIOD.start,
            period_end=PERIOD.end,
            status=STATUS_PENDING,
        )
        session.add(fresh)
        session.commit()  # no violation: only one active row again
        active = session.execute(
            select(ReportJob).where(ReportJob.status.in_(ACTIVE_STATUSES))
        ).scalars().all()
        assert [j.id for j in active] == [fresh.id]


def test_regenerate_rejects_incomplete_week(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        with pytest.raises(ReportJobError, match="incomplete week"):
            request_regenerate(session, PERIOD, now=datetime(2026, 9, 4, tzinfo=UTC))


def test_recover_stale_running_jobs(db_engine: Engine, tmp_path: Path) -> None:
    with Session(db_engine) as session:
        job = ensure_scheduled_job(session, PERIOD, now=NOW)
        assert job is not None
        job_id = job.id
        job.status = STATUS_RUNNING  # worker died mid-generation
        session.commit()

        recovered = recover_stale_running_jobs(session, now=NOW)
        assert recovered == 1
        row = session.get(ReportJob, job_id)
        assert row is not None
        assert row.status == STATUS_PENDING
        assert row.next_retry_at == NOW  # retry immediately after restart

    # The recovered job then completes (§27.1: restart keeps responsibility).
    outcome = execute_report_job(get_session_factory(), job_id, tmp_path, now=NOW)
    assert outcome.status == STATUS_SUCCEEDED


def test_worker_loop_schedules_at_monday_0010_and_recovers(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full loop: no job before 00:10, job created+executed after, restart safe."""

    loop = WeeklyReportLoop(get_session_factory(), tmp_path)

    # Monday 2026-09-07 00:05 +08 (= 2026-09-06 16:05 UTC): W36 complete,
    # but the 00:10 generation instant has not been reached.
    before_due = datetime(2026, 9, 6, 16, 5, tzinfo=UTC)
    assert not is_due_for_schedule(before_due, PERIOD.end)
    loop._clock = lambda: before_due
    loop.run_pass()
    with Session(db_engine) as session:
        assert session.query(ReportJob).count() == 0

    # Monday 00:12: the pass creates and executes the job (§4.1).
    loop_now = datetime(2026, 9, 6, 16, 12, tzinfo=UTC)  # == Monday 00:12 +08
    loop._clock = lambda: loop_now
    loop.run_pass()
    with Session(db_engine) as session:
        jobs = session.query(ReportJob).all()
        assert len(jobs) == 1
        assert jobs[0].status == STATUS_SUCCEEDED
        report = session.query(WeeklyReport).one()
        assert report.status == "success"

    # Restart at a later instant: no duplicate job, no re-execution (§27.1).
    loop2 = WeeklyReportLoop(get_session_factory(), tmp_path)
    loop2._clock = lambda: datetime(2026, 9, 7, 3, 0, tzinfo=UTC)
    loop2.run_pass()
    with Session(db_engine) as session:
        assert session.query(ReportJob).count() == 1
        assert session.query(WeeklyReport).one().status == "success"


def test_worker_loop_executes_failed_retry_when_due(
    db_engine: Engine, tmp_path: Path
) -> None:
    """The loop picks up a failed job only after its 10-minute retry time."""

    real_execute = execute_report_job
    calls: list[int] = []

    def _exploding_render(data: object, output_dir: Path) -> RenderedReport:
        raise RuntimeError("first attempt fails")

    now_holder = {"now": datetime(2026, 9, 6, 16, 12, tzinfo=UTC)}

    def counting_execute(
        factory: sessionmaker,
        job_id: int,
        output_dir: Path,
        **kwargs: Any,
    ) -> ReportOutcome:
        calls.append(job_id)
        kwargs.setdefault("now", now_holder["now"])  # keep the clock logical
        if len(calls) == 1:
            kwargs["render"] = _exploding_render
        return real_execute(factory, job_id, output_dir, **kwargs)


    loop = WeeklyReportLoop(get_session_factory(), tmp_path, execute=counting_execute)
    loop._clock = lambda: now_holder["now"]

    loop.run_pass()
    assert len(calls) == 1
    with Session(db_engine) as session:
        job = session.query(ReportJob).one()
        assert job.status == STATUS_FAILED
        retry_at = job.next_retry_at
        assert retry_at is not None

    # Still before the retry time: the pass must NOT re-run it.
    now_holder["now"] = retry_at - timedelta(seconds=1)
    loop.run_pass()
    assert len(calls) == 1

    # After the retry time: re-run succeeds (§4.3: retry until success).
    now_holder["now"] = retry_at + timedelta(seconds=1)
    loop.run_pass()
    assert len(calls) == 2
    with Session(db_engine) as session:
        assert session.query(ReportJob).one().status == STATUS_SUCCEEDED
        assert session.query(WeeklyReport).one().status == "success"


def test_default_check_interval_is_one_minute() -> None:
    """Sanity pin: the shipped loop checks every minute (00:10 precision)."""

    assert DEFAULT_CHECK_INTERVAL == timedelta(seconds=60)


def test_load_weekly_reports_ordering(db_engine: Engine, tmp_path: Path) -> None:
    with Session(db_engine) as session:
        for week in (37, 35):
            period = period_for_iso_week(2026, week)
            session.add(
                WeeklyReport(
                    week_code=period.week_code,
                    period_start=period.start,
                    period_end=period.end,
                    status="failed",
                    last_error="x",
                )
            )
        session.commit()
        rows = load_weekly_reports(session)
        assert [r.week_code for r in rows] == ["2026-W37", "2026-W35"]
