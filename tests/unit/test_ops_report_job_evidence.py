"""W05-AUDIT-2: the A09 scheduled-report evidence collector logic.

The DB-facing half is pinned against real PostgreSQL in
`tests/integration/test_report_job_evidence.py`; these tests pin the pure
judgment helpers: the ±5 min on-schedule tolerance around Monday 00:10
(period END + 10 min, §4.1), the ISO-week consecutiveness used by the A09
summary, and the shape of one evidence line.
"""

from datetime import datetime, timedelta

from backend.ops.report_job_evidence import (
    BUSINESS_TIMEZONE,
    ON_SCHEDULE_TOLERANCE,
    SCHEDULE_OFFSET,
    ScheduledJobEvidence,
    consecutive_scheduled_pairs,
    format_job_line,
    is_on_schedule,
    scheduled_succeeded_weeks,
)

#: Monday 00:10 Asia/Shanghai wall clock, as an instant.
MONDAY_0010 = datetime(2026, 9, 7, 0, 10, 0, tzinfo=BUSINESS_TIMEZONE)


def _evidence(
    created: datetime,
    *,
    week: str = "2026-W36",
    status: str = "succeeded",
) -> ScheduledJobEvidence:
    return ScheduledJobEvidence(
        job_id=1,
        week_code=week,
        status=status,
        attempts=2,
        created_at=created,
        started_at=created,
        finished_at=created + timedelta(seconds=10),
        expected_at=MONDAY_0010,
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


def test_succeeded_weeks_are_unique_and_ordered() -> None:
    evidence = [
        _evidence(MONDAY_0010, week="2026-W37"),
        _evidence(MONDAY_0010, week="2026-W36", status="failed"),
        _evidence(MONDAY_0010 + timedelta(days=7), week="2026-W36"),
        _evidence(MONDAY_0010, week="2026-W38"),
    ]
    assert scheduled_succeeded_weeks(evidence) == ["2026-W36", "2026-W37", "2026-W38"]


def test_consecutive_pairs_only_link_adjacent_iso_weeks() -> None:
    assert consecutive_scheduled_pairs(["2026-W36", "2026-W37"]) == [
        ("2026-W36", "2026-W37")
    ]
    assert consecutive_scheduled_pairs(["2026-W35", "2026-W37"]) == []
    assert consecutive_scheduled_pairs(["2026-W53", "2027-W01"]) == [
        ("2026-W53", "2027-W01")
    ]
    assert consecutive_scheduled_pairs([]) == []


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
