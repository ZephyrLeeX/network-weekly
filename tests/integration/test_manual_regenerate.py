"""Integration tests for the manual regenerate service (W03-T010, §4.4).

Covers the service-level regenerate acceptance: repeated requests are
idempotent, a successful regenerate atomically replaces the one current
server DOCX for the week (no duplicates, no temp leftovers), and the
registry row points at the fresh file.

The REAL-report acceptance (a real weekly report manually checked against
source samples) is tracked separately in TASK_GRAPH/EXECUTION_STATE as
BLOCKED — this environment has no reachable real H3C devices, hence no
real weekly data (see W01-T007).
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from backend.db.engine import get_session_factory
from backend.db.models import ReportJob, WeeklyReport
from backend.reporting.jobs import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUCCEEDED,
    execute_report_job,
    request_regenerate,
)
from backend.reporting.period import period_for_iso_week

pytestmark = pytest.mark.integration

PERIOD = period_for_iso_week(2026, 36)
NOW = datetime(2026, 9, 7, 0, 11, 0, tzinfo=UTC)  # Monday 00:11 +08


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(ReportJob).delete()
        session.query(WeeklyReport).delete()
        session.commit()
        yield


def test_regenerate_replaces_the_single_current_docx(db_engine: Engine, tmp_path: Path) -> None:
    """§4.4: regenerate success -> same file name, new content, one file."""

    with Session(db_engine) as session:
        first_job = request_regenerate(session, PERIOD, now=NOW)
        first_id = first_job.id
    first_outcome = execute_report_job(get_session_factory(), first_id, tmp_path, now=NOW)
    assert first_outcome.status == "succeeded"
    first_path = tmp_path / "network-weekly-report-2026-W36.docx"
    assert first_path.exists()
    first_generated = first_path.stat().st_mtime_ns

    # Regenerate ~1 hour later: the report must be REPLACED atomically.
    later = NOW + timedelta(hours=1)
    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=later)
        assert job.trigger == "manual"
        job_id = job.id
    outcome = execute_report_job(get_session_factory(), job_id, tmp_path, now=later)
    assert outcome.status == "succeeded"

    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["network-weekly-report-2026-W36.docx"]  # one current DOCX
    assert first_path.stat().st_mtime_ns != first_generated  # replaced, not reused

    with Session(db_engine) as session:
        report = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == PERIOD.week_code)
        ).scalar_one()
        assert report.status == "success"
        assert report.file_path == str(first_path)
        assert report.generated_at == later  # registry points at the new file
        first_row = session.get(ReportJob, first_id)
        assert first_row is not None and first_row.status == STATUS_SUCCEEDED
        second_row = session.get(ReportJob, job_id)
        assert second_row is not None and second_row.status == STATUS_SUCCEEDED


def test_rapid_double_regenerate_yields_one_job(db_engine: Engine) -> None:
    """§27.4: repeated submissions never create concurrent duplicate jobs."""

    with Session(db_engine) as session:
        first = request_regenerate(session, PERIOD, now=NOW)
        second = request_regenerate(session, PERIOD, now=NOW + timedelta(seconds=2))
        assert second.id == first.id
        assert session.query(ReportJob).count() == 1
        assert first.status == STATUS_PENDING


def test_regenerate_failure_visible_then_success_recovers(
    db_engine: Engine, tmp_path: Path
) -> None:
    """A failed regenerate leaves a visible error and stays retryable."""

    with Session(db_engine) as session:
        job = request_regenerate(session, PERIOD, now=NOW)
        job_id = job.id

    def failing_render(data: object, output_dir: Path) -> object:
        raise RuntimeError("no space left on device")

    outcome = execute_report_job(
        get_session_factory(), job_id, tmp_path, now=NOW, render=failing_render  # type: ignore[arg-type]
    )
    assert outcome.status == STATUS_FAILED
    with Session(db_engine) as session:
        row = session.get(ReportJob, job_id)
        assert row is not None
        assert row.status == STATUS_FAILED
        assert "no space left" in (row.last_error or "")
        assert row.next_retry_at is not None
