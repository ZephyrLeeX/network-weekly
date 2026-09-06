"""W05-AUDIT-2 + W05-PRE-ACCEPTANCE-HARDENING: the A09 collector logic.

The DB-facing half is pinned against real PostgreSQL in
`tests/integration/test_report_job_evidence.py`; these tests pin the pure
judgment helpers: the ±5 min on-schedule tolerance around Monday 00:10
(period END + 10 min, §4.1), the ISO-week consecutiveness used by the A09
summary, and the shape of one evidence line.

The HARDENING fix is pinned here too: the A09 summary counts a week only
when its scheduled job is `status=succeeded` AND `is_on_schedule` — two
consecutive LATE scheduled successes must never be reported as the A09
shape (docs/ACCEPTANCE.md requires the created_at inside Monday 00:10
±5 min).
"""

from datetime import date, datetime, timedelta

from backend.ops.report_job_evidence import (
    BUSINESS_TIMEZONE,
    ON_SCHEDULE_TOLERANCE,
    SCHEDULE_OFFSET,
    ScheduledJobEvidence,
    consecutive_scheduled_pairs,
    format_job_line,
    is_on_schedule,
    next_week_code,
    render_scheduled_job_evidence,
    scheduled_on_time_succeeded_weeks,
)

#: Monday 00:10 Asia/Shanghai wall clock, as an instant.
MONDAY_0010 = datetime(2026, 9, 7, 0, 10, 0, tzinfo=BUSINESS_TIMEZONE)


def _evidence(
    created: datetime,
    *,
    week: str = "2026-W36",
    status: str = "succeeded",
    expected: datetime | None = None,
) -> ScheduledJobEvidence:
    return ScheduledJobEvidence(
        job_id=1,
        week_code=week,
        status=status,
        attempts=2,
        created_at=created,
        started_at=created,
        finished_at=created + timedelta(seconds=10),
        expected_at=expected if expected is not None else MONDAY_0010,
    )


def _pair(week: str, *, offset_minutes: float, status: str = "succeeded") -> ScheduledJobEvidence:
    """One scheduled job for `week`, created `offset_minutes` after ITS OWN
    Monday 00:10 — so the expected instant always follows the week code."""

    year, number = (int(part) for part in week.split("-W"))
    monday = date.fromisocalendar(year, number, 1)
    expected = datetime(
        monday.year, monday.month, monday.day, 0, 10, tzinfo=BUSINESS_TIMEZONE
    )
    return _evidence(
        expected + timedelta(minutes=offset_minutes),
        week=week,
        status=status,
        expected=expected,
    )


def test_schedule_offset_is_period_end_plus_ten_minutes() -> None:
    assert SCHEDULE_OFFSET == timedelta(minutes=10)
    assert ON_SCHEDULE_TOLERANCE == timedelta(minutes=5)


def test_on_schedule_tolerates_the_scheduling_loop_jitter() -> None:
    assert is_on_schedule(_evidence(MONDAY_0010))
    assert is_on_schedule(_evidence(MONDAY_0010 - ON_SCHEDULE_TOLERANCE))
    assert is_on_schedule(_evidence(MONDAY_0010 + ON_SCHEDULE_TOLERANCE))
    assert not is_on_schedule(_evidence(MONDAY_0010 + ON_SCHEDULE_TOLERANCE + timedelta(seconds=1)))
    assert not is_on_schedule(_evidence(MONDAY_0010 - ON_SCHEDULE_TOLERANCE - timedelta(seconds=1)))


def test_on_time_succeeded_weeks_are_unique_and_ordered() -> None:
    evidence = [
        _pair("2026-W37", offset_minutes=0),
        _pair("2026-W36", offset_minutes=0, status="failed"),
        _pair("2026-W36", offset_minutes=1),
        _pair("2026-W38", offset_minutes=0),
    ]
    assert scheduled_on_time_succeeded_weeks(evidence) == [
        "2026-W36",
        "2026-W37",
        "2026-W38",
    ]


def test_two_late_scheduled_successes_are_not_the_a09_shape() -> None:
    """The HARDENING regression: W36 and W37 both created 00:20 — scheduled,
    succeeded, and 10 minutes late. The summary must say no, matching
    docs/ACCEPTANCE.md A09 (created_at within Monday 00:10 ±5 min)."""

    evidence = [_pair("2026-W36", offset_minutes=10), _pair("2026-W37", offset_minutes=10)]
    assert scheduled_on_time_succeeded_weeks(evidence) == []
    rendered = render_scheduled_job_evidence(evidence)
    assert "weeks with an on-time succeeded scheduled job: none" in rendered
    assert "two consecutive on-time scheduled+succeeded weeks (A09): no" in rendered
    assert "on_schedule=no" in rendered  # the rows stay honest evidence


def test_one_on_time_and_one_late_success_do_not_pair() -> None:
    evidence = [_pair("2026-W36", offset_minutes=0), _pair("2026-W37", offset_minutes=10)]
    assert scheduled_on_time_succeeded_weeks(evidence) == ["2026-W36"]
    rendered = render_scheduled_job_evidence(evidence)
    assert "two consecutive on-time scheduled+succeeded weeks (A09): no" in rendered


def test_two_consecutive_on_time_successes_are_the_a09_shape() -> None:
    evidence = [_pair("2026-W36", offset_minutes=0), _pair("2026-W37", offset_minutes=1)]
    assert scheduled_on_time_succeeded_weeks(evidence) == ["2026-W36", "2026-W37"]
    rendered = render_scheduled_job_evidence(evidence)
    assert (
        "two consecutive on-time scheduled+succeeded weeks (A09): yes "
        "(2026-W36 -> 2026-W37)" in rendered
    )


def test_a_late_success_never_shadows_an_on_time_one() -> None:
    """A week whose succeeded attempt was late does not count even when the
    same week also had an on-time FAILED attempt (the succeeded row's own
    created_at is what A09 judges)."""

    evidence = [
        _pair("2026-W36", offset_minutes=0, status="failed"),
        _pair("2026-W36", offset_minutes=9),
        _pair("2026-W37", offset_minutes=0),
    ]
    assert scheduled_on_time_succeeded_weeks(evidence) == ["2026-W37"]
    assert "two consecutive on-time scheduled+succeeded weeks (A09): no" in (
        render_scheduled_job_evidence(evidence)
    )


def test_failed_scheduled_jobs_never_count_even_on_time() -> None:
    evidence = [
        _pair("2026-W36", offset_minutes=0, status="failed"),
        _pair("2026-W37", offset_minutes=0, status="failed"),
    ]
    assert scheduled_on_time_succeeded_weeks(evidence) == []
    rendered = render_scheduled_job_evidence(evidence)
    assert "weeks with an on-time succeeded scheduled job: none" in rendered
    assert "two consecutive on-time scheduled+succeeded weeks (A09): no" in rendered


def test_iso_year_wrap_pairs_through_the_on_time_summary() -> None:
    evidence = [_pair("2026-W53", offset_minutes=0), _pair("2027-W01", offset_minutes=1)]
    assert scheduled_on_time_succeeded_weeks(evidence) == ["2026-W53", "2027-W01"]
    rendered = render_scheduled_job_evidence(evidence)
    assert (
        "two consecutive on-time scheduled+succeeded weeks (A09): yes "
        "(2026-W53 -> 2027-W01)" in rendered
    )


def test_consecutive_pairs_only_link_adjacent_iso_weeks() -> None:
    assert consecutive_scheduled_pairs(["2026-W36", "2026-W37"]) == [
        ("2026-W36", "2026-W37")
    ]
    assert consecutive_scheduled_pairs(["2026-W35", "2026-W37"]) == []
    assert consecutive_scheduled_pairs(["2026-W53", "2027-W01"]) == [
        ("2026-W53", "2027-W01")
    ]
    assert consecutive_scheduled_pairs([]) == []


def test_next_week_code_wraps_the_iso_year() -> None:
    assert next_week_code("2026-W36") == "2026-W37"
    assert next_week_code("2026-W01") == "2026-W02"
    # 2026-W53 -> 2027-W01 (2026 is a 53-week ISO year, W03-T001).
    assert next_week_code("2026-W53") == "2027-W01"


def test_evidence_line_carries_the_a09_fields_and_nothing_free_form() -> None:
    line = format_job_line(_evidence(MONDAY_0010))
    assert "week=2026-W36" in line
    assert "trigger=scheduled" in line
    assert "status=succeeded" in line
    assert "attempts=2" in line
    assert "created=2026-09-07T00:10:00+08:00" in line
    assert "finished=2026-09-07T00:10:10+08:00" in line
    assert "on_schedule=yes" in line

    late = format_job_line(_evidence(MONDAY_0010 + timedelta(minutes=7)))
    assert "on_schedule=no" in late
