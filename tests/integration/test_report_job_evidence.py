"""W05-AUDIT-2 + W05-PRE-ACCEPTANCE-HARDENING: A09 scheduled-report evidence
from `report_jobs` history.

A09 (two consecutive Monday 00:10 Asia/Shanghai automatic reports) must be
judged from `report_jobs` rows with `trigger='scheduled'` — never from
`weekly_reports.generated_at`, which a manual regenerate overwrites. These
tests pin, against real PostgreSQL:

- the core regression: a scheduled job succeeds, a later manual regenerate
  updates the `weekly_reports` registry — and the scheduled job's history
  (status, created_at) is untouched, still proving the automatic generation;
- a manual-only week never counts as scheduled evidence — not even two
  consecutive manual weeks created exactly on time;
- a failed scheduled job never counts as succeeded evidence;
- HARDENING: the A09 summary counts a week only when the succeeded
  scheduled job's created_at is on time (Monday 00:10 ±5 min) — two
  consecutive LATE successes must be summarized as NO, one on-time + one
  late as NO, two consecutive on-time successes as YES;
- ISO year wrap: the last week of a year pairs with the next year's W01.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from backend.db.models import ReportJob, WeeklyReport
from backend.ops.report_job_evidence import (
    is_on_schedule,
    next_week_code,
    render_scheduled_job_evidence,
    scheduled_jobs,
    scheduled_on_time_succeeded_weeks,
)
from backend.reporting.jobs import STATUS_FAILED, STATUS_SUCCEEDED
from backend.reporting.period import ReportPeriod, period_for_iso_week

pytestmark = pytest.mark.integration

W36 = period_for_iso_week(2026, 36)  # [2026-08-31, 2026-09-07) Asia/Shanghai
W37 = period_for_iso_week(2026, 37)
W35 = period_for_iso_week(2026, 35)

#: §4.1: Monday 00:10 Asia/Shanghai after W36 ends (2026-09-07 00:10 +08).
SCHEDULED_MONDAY_0010 = datetime(2026, 9, 6, 16, 10, 0, tzinfo=UTC)
SCHEDULED_CREATED = SCHEDULED_MONDAY_0010 + timedelta(seconds=41)
#: 10 minutes late: outside the ±5 min loop tolerance.
LATE_CREATED = SCHEDULED_MONDAY_0010 + timedelta(minutes=10)
MANUAL_CREATED = datetime(2026, 9, 6, 18, 30, 0, tzinfo=UTC)  # same day, later


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(ReportJob).delete()
        session.query(WeeklyReport).delete()
        session.commit()
        yield


def _add_job(
    db_engine: Engine,
    period: ReportPeriod,
    *,
    trigger: str,
    status: str,
    created: datetime,
    finished: datetime | None,
    attempts: int = 1,
) -> None:
    with Session(db_engine) as session:
        session.add(
            ReportJob(
                week_code=period.week_code,
                period_start=period.start,
                period_end=period.end,
                status=status,
                trigger=trigger,
                attempts=attempts,
                created_at=created,
                started_at=created,
                finished_at=finished,
            )
        )
        session.commit()


def _add_success_report(db_engine: Engine, period: ReportPeriod, generated: datetime) -> None:
    with Session(db_engine) as session:
        session.add(
            WeeklyReport(
                week_code=period.week_code,
                period_start=period.start,
                period_end=period.end,
                status="success",
                file_path=f"/reports/network-weekly-report-{period.week_code}.docx",
                generated_at=generated,
            )
        )
        session.commit()


def _read_scheduled(db_engine: Engine) -> list:
    with db_engine.connect() as conn:
        return scheduled_jobs(conn)


def test_manual_regenerate_does_not_destroy_scheduled_evidence(
    db_engine: Engine,
) -> None:
    """The A09 regression: scheduled job succeeds at Monday 00:10, a later
    manual regenerate overwrites weekly_reports.generated_at — the
    scheduled ReportJob history (and thus the A09 evidence) survives."""

    _add_job(
        db_engine,
        W36,
        trigger="scheduled",
        status=STATUS_SUCCEEDED,
        created=SCHEDULED_CREATED,
        finished=SCHEDULED_CREATED + timedelta(seconds=19),
    )
    _add_success_report(db_engine, W36, generated=SCHEDULED_CREATED + timedelta(seconds=19))

    # The manual regenerate of the SAME week, ~2.5 hours later: a new
    # manual job succeeds and the registry's generated_at moves to it.
    _add_job(
        db_engine,
        W36,
        trigger="manual",
        status=STATUS_SUCCEEDED,
        created=MANUAL_CREATED,
        finished=MANUAL_CREATED + timedelta(seconds=17),
    )
    with Session(db_engine) as session:
        row = session.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == W36.week_code)
        ).scalar_one()
        row.generated_at = MANUAL_CREATED + timedelta(seconds=17)
        session.commit()

    # The scheduled job row is exactly as it was.
    with Session(db_engine) as session:
        scheduled_row = session.execute(
            select(ReportJob).where(
                ReportJob.week_code == W36.week_code,
                ReportJob.trigger == "scheduled",
            )
        ).scalar_one()
        assert scheduled_row.status == STATUS_SUCCEEDED
        assert scheduled_row.created_at == SCHEDULED_CREATED

    evidence = _read_scheduled(db_engine)
    assert [item.week_code for item in evidence] == [W36.week_code]
    assert evidence[0].created_at == SCHEDULED_CREATED
    assert evidence[0].status == STATUS_SUCCEEDED
    assert is_on_schedule(evidence[0])

    rendered = render_scheduled_job_evidence(evidence)
    assert f"week={W36.week_code} trigger=scheduled status=succeeded" in rendered
    assert "2026-09-07T00:10:41+08:00" in rendered  # Asia/Shanghai wall clock
    assert "trigger=manual" not in rendered
    assert "2026-09-07T02:30" not in rendered  # the manual attempt is absent
    assert f"weeks with an on-time succeeded scheduled job: {W36.week_code}" in rendered


def test_manual_only_week_is_not_scheduled_evidence(db_engine: Engine) -> None:
    """A week whose only job is a manual regenerate must never satisfy A09 —
    the collector neither lists it nor counts it as a scheduled week."""

    _add_job(
        db_engine,
        W36,
        trigger="manual",
        status=STATUS_SUCCEEDED,
        created=MANUAL_CREATED,
        finished=MANUAL_CREATED,
    )
    _add_success_report(db_engine, W36, generated=MANUAL_CREATED)

    evidence = _read_scheduled(db_engine)
    assert evidence == []
    assert scheduled_on_time_succeeded_weeks(evidence) == []
    rendered = render_scheduled_job_evidence(evidence)
    assert "no scheduled report jobs recorded" in rendered
    assert "weeks with an on-time succeeded scheduled job: none" in rendered
    assert "two consecutive on-time scheduled+succeeded weeks (A09): no" in rendered


def test_two_on_time_manual_successes_are_not_the_a09_shape(
    db_engine: Engine,
) -> None:
    """HARDENING: trigger is judged at the query level, so even two
    consecutive weeks whose MANUAL jobs were created exactly at Monday
    00:10 (on time, succeeded, registered in weekly_reports) never reach
    the A09 summary."""

    for period, created in (
        (W36, SCHEDULED_MONDAY_0010),
        (W37, SCHEDULED_MONDAY_0010 + timedelta(days=7)),
    ):
        _add_job(
            db_engine,
            period,
            trigger="manual",
            status=STATUS_SUCCEEDED,
            created=created,
            finished=created,
        )
        _add_success_report(db_engine, period, generated=created)

    evidence = _read_scheduled(db_engine)
    assert evidence == []
    rendered = render_scheduled_job_evidence(evidence)
    assert "two consecutive on-time scheduled+succeeded weeks (A09): no" in rendered


def test_failed_scheduled_job_is_not_succeeded_evidence(db_engine: Engine) -> None:
    """A scheduled job that never succeeded is listed (honest evidence) but
    must not count towards the A09 succeeded-weeks set."""

    _add_job(
        db_engine,
        W36,
        trigger="scheduled",
        status=STATUS_FAILED,
        created=SCHEDULED_CREATED,
        finished=SCHEDULED_CREATED + timedelta(seconds=5),
        attempts=3,
    )

    evidence = _read_scheduled(db_engine)
    assert [item.status for item in evidence] == [STATUS_FAILED]
    assert scheduled_on_time_succeeded_weeks(evidence) == []
    rendered = render_scheduled_job_evidence(evidence)
    assert "status=failed" in rendered
    assert "attempts=3" in rendered
    assert "on_schedule=yes" in rendered  # on time, but still not A09 evidence
    assert "two consecutive on-time scheduled+succeeded weeks (A09): no" in rendered


def test_two_late_scheduled_successes_do_not_satisfy_a09(db_engine: Engine) -> None:
    """The HARDENING false positive: W36 and W37 both have scheduled
    succeeded jobs created at 00:20 — outside Monday 00:10 ±5 min. The old
    summary reported the A09 shape from status alone; it must now say no."""

    for period, created in ((W36, LATE_CREATED), (W37, LATE_CREATED + timedelta(days=7))):
        _add_job(
            db_engine,
            period,
            trigger="scheduled",
            status=STATUS_SUCCEEDED,
            created=created,
            finished=created,
        )
        _add_success_report(db_engine, period, generated=created)

    evidence = _read_scheduled(db_engine)
    assert scheduled_on_time_succeeded_weeks(evidence) == []
    rendered = render_scheduled_job_evidence(evidence)
    assert rendered.count("on_schedule=no") == 2
    assert "weeks with an on-time succeeded scheduled job: none" in rendered
    assert "two consecutive on-time scheduled+succeeded weeks (A09): no" in rendered


def test_one_on_time_and_one_late_success_do_not_satisfy_a09(
    db_engine: Engine,
) -> None:
    _add_job(
        db_engine, W36, trigger="scheduled", status=STATUS_SUCCEEDED,
        created=SCHEDULED_CREATED, finished=SCHEDULED_CREATED,
    )
    _add_job(
        db_engine, W37, trigger="scheduled", status=STATUS_SUCCEEDED,
        created=LATE_CREATED + timedelta(days=7), finished=None,
    )

    evidence = _read_scheduled(db_engine)
    assert scheduled_on_time_succeeded_weeks(evidence) == [W36.week_code]
    rendered = render_scheduled_job_evidence(evidence)
    assert "two consecutive on-time scheduled+succeeded weeks (A09): no" in rendered


def test_two_consecutive_on_time_succeeded_weeks_are_identified(
    db_engine: Engine,
) -> None:
    """A09's shape: two DISTINCT consecutive week codes, each with an
    on-time succeeded scheduled job, are identifiable in the output."""

    for period, monday in ((W36, SCHEDULED_CREATED), (W37, SCHEDULED_CREATED + timedelta(days=7))):
        _add_job(
            db_engine,
            period,
            trigger="scheduled",
            status=STATUS_SUCCEEDED,
            created=monday,
            finished=monday,
        )
        _add_success_report(db_engine, period, generated=monday)

    evidence = _read_scheduled(db_engine)
    assert scheduled_on_time_succeeded_weeks(evidence) == [W36.week_code, W37.week_code]
    rendered = render_scheduled_job_evidence(evidence)
    assert (
        f"two consecutive on-time scheduled+succeeded weeks (A09): yes "
        f"({W36.week_code} -> {W37.week_code})" in rendered
    )


def test_non_consecutive_succeeded_weeks_are_not_claimed_consecutive(
    db_engine: Engine,
) -> None:
    _add_job(
        db_engine, W35, trigger="scheduled", status=STATUS_SUCCEEDED,
        created=SCHEDULED_CREATED - timedelta(days=7), finished=None,
    )
    _add_job(
        db_engine, W37, trigger="scheduled", status=STATUS_SUCCEEDED,
        created=SCHEDULED_CREATED + timedelta(days=7), finished=None,
    )

    evidence = _read_scheduled(db_engine)
    assert scheduled_on_time_succeeded_weeks(evidence) == [W35.week_code, W37.week_code]
    rendered = render_scheduled_job_evidence(evidence)
    assert "two consecutive on-time scheduled+succeeded weeks (A09): no" in rendered


def test_next_week_code_wraps_the_iso_year() -> None:
    assert next_week_code("2026-W36") == "2026-W37"
    assert next_week_code("2026-W01") == "2026-W02"
    # 2026-W53 -> 2027-W01 (2026 is a 53-week ISO year, W03-T001).
    assert next_week_code("2026-W53") == "2027-W01"
