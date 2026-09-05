"""Unit tests for report periods and ISO week codes (W03-T001, §3)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.reporting.period import (
    BUSINESS_TIMEZONE,
    ReportPeriod,
    is_complete,
    period_for_iso_week,
    previous_period,
    report_file_name,
    week_containing,
)

SH = BUSINESS_TIMEZONE
# 2026-09-05 is a Saturday; its week is ISO 2026-W36
# [Mon 2026-08-31 00:00 +08, Mon 2026-09-07 00:00 +08).
SATURDAY = datetime(2026, 9, 5, 15, 30, tzinfo=SH)


def test_week_containing_bounds_are_local_mondays() -> None:
    period = week_containing(SATURDAY)
    assert period.start == datetime(2026, 8, 31, 0, 0, 0, tzinfo=SH)
    assert period.end == datetime(2026, 9, 7, 0, 0, 0, tzinfo=SH)
    assert period.iso_year == 2026
    assert period.iso_week == 36
    assert period.week_code == "2026-W36"


def test_period_is_half_open() -> None:
    period = week_containing(SATURDAY)
    assert period.contains(datetime(2026, 8, 31, 0, 0, 0, tzinfo=SH))
    assert period.contains(datetime(2026, 9, 6, 23, 59, 59, 999999, tzinfo=SH))
    assert not period.contains(period.end)  # half-open [start, end)


def test_utc_instant_maps_to_local_week() -> None:
    # 2026-08-31 00:00 +08 == 2026-08-30 16:00 UTC — a Sunday UTC instant that
    # belongs to the Monday-started Shanghai week.
    utc_instant = datetime(2026, 8, 30, 16, 0, tzinfo=UTC)
    period = week_containing(utc_instant)
    assert period.start == datetime(2026, 8, 31, tzinfo=SH)
    assert period.week_code == "2026-W36"


def test_monday_morning_belongs_to_new_week() -> None:
    # Monday 00:00:00 exactly is the first instant of the NEW week.
    period = week_containing(datetime(2026, 9, 7, 0, 0, 0, tzinfo=SH))
    assert period.week_code == "2026-W37"
    assert period.start == datetime(2026, 9, 7, tzinfo=SH)


def test_sunday_last_second_belongs_to_old_week() -> None:
    period = week_containing(datetime(2026, 9, 6, 23, 59, 59, tzinfo=SH))
    assert period.week_code == "2026-W36"


def test_previous_period_is_last_complete_week() -> None:
    # Monday 00:10: the just-finished week (§4.1).
    report_time = datetime(2026, 9, 7, 0, 10, 0, tzinfo=SH)
    period = previous_period(report_time)
    assert period.start == datetime(2026, 8, 31, tzinfo=SH)
    assert period.end == datetime(2026, 9, 7, tzinfo=SH)
    assert period.week_code == "2026-W36"
    assert is_complete(period, report_time)

    # Sunday afternoon: the complete week is the one before.
    period = previous_period(SATURDAY)
    assert period.week_code == "2026-W35"
    assert period.end == datetime(2026, 8, 31, tzinfo=SH)
    assert is_complete(period, SATURDAY)


def test_iso_year_boundary_period() -> None:
    """ISO 2026-W01 spans the calendar-year boundary (§3)."""

    period = period_for_iso_week(2026, 1)
    assert period.start == datetime(2025, 12, 29, tzinfo=SH)
    assert period.end == datetime(2026, 1, 5, tzinfo=SH)
    assert period.week_code == "2026-W01"  # ISO week-year, not calendar year

    # The week containing New Year's Day 2026 is 2026-W01.
    assert week_containing(datetime(2026, 1, 1, 12, 0, tzinfo=SH)).week_code == "2026-W01"


def test_iso_year_boundary_weeks_52_53() -> None:
    # 2024 had 52 ISO weeks; 2020 and 2026 have 53.
    assert period_for_iso_week(2024, 52).week_code == "2024-W52"
    w53_2020 = period_for_iso_week(2020, 53)
    assert w53_2020.start == datetime(2020, 12, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert w53_2020.end == datetime(2021, 1, 4, tzinfo=SH)
    # The week after 2020-W53 starts ISO 2021-W01.
    assert period_for_iso_week(2021, 1).start == datetime(2021, 1, 4, tzinfo=SH)


def test_round_trip_week_code() -> None:
    for iso_year, iso_week in ((2025, 1), (2026, 36), (2026, 53), (2021, 1)):
        period = period_for_iso_week(iso_year, iso_week)
        rebuilt = period_for_iso_week(period.iso_year, period.iso_week)
        assert rebuilt == period


def test_period_for_iso_week_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="invalid ISO week"):
        period_for_iso_week(2026, 54)
    with pytest.raises(ValueError, match="invalid ISO week"):
        period_for_iso_week(2025, 53)  # 2025 has only 52 ISO weeks


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ReportPeriod(start=datetime(2026, 8, 31), end=datetime(2026, 9, 7))


def test_empty_period_rejected() -> None:
    with pytest.raises(ValueError, match="after start"):
        ReportPeriod(start=datetime(2026, 9, 7, tzinfo=SH), end=datetime(2026, 9, 7, tzinfo=SH))


def test_overlaps_interval_cross_week_semantics() -> None:
    period = week_containing(SATURDAY)  # [08-31, 09-07)

    # Down before the week, still down: belongs to this week.
    assert period.overlaps_interval(datetime(2026, 8, 20, tzinfo=SH), None)
    # Down before the week, recovered exactly at period start: previous week.
    assert not period.overlaps_interval(datetime(2026, 8, 20, tzinfo=SH), period.start)
    # Down before the week, recovered inside the week: belongs.
    assert period.overlaps_interval(
        datetime(2026, 8, 20, tzinfo=SH), datetime(2026, 9, 1, tzinfo=SH)
    )
    # Started inside the week: belongs.
    assert period.overlaps_interval(datetime(2026, 9, 2, tzinfo=SH), None)
    # Started exactly at period end: belongs to the NEXT week only.
    assert not period.overlaps_interval(period.end, None)
    # Recovered exactly at period end: still belongs (recovery is exclusive).
    assert period.overlaps_interval(datetime(2026, 9, 1, tzinfo=SH), period.end)
    # Fully before the week: does not belong.
    assert not period.overlaps_interval(
        datetime(2026, 8, 1, tzinfo=SH), datetime(2026, 8, 2, tzinfo=SH)
    )


def test_report_file_name() -> None:
    assert report_file_name(week_containing(SATURDAY)) == "network-weekly-report-2026-W36.docx"


def test_period_is_frozen() -> None:
    period = week_containing(SATURDAY)
    with pytest.raises(Exception):  # noqa: B017, PT011 — FrozenInstanceError
        period.start = period.start + timedelta(days=1)  # type: ignore[misc]
