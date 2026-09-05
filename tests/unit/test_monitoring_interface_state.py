"""Unit tests for §13 interface state transitions and sustained-high runs
(W02-T006).
"""

from datetime import UTC, datetime, timedelta

import pytest

from backend.monitoring.interface_state import (
    InterfaceTracking,
    advance_interface_state,
)
from backend.monitoring.sustained import (
    find_sustained_high_intervals,
    max_direction_utilization,
)

T0 = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
STEP = timedelta(minutes=5)
FRESH = InterfaceTracking()


def test_two_valid_down_samples_confirm_down() -> None:
    d1 = advance_interface_state(FRESH, "down", T0)
    assert d1.tracking.state == "normal"
    assert d1.tracking.consecutive_down_samples == 1
    assert d1.incident_started_at is None

    d2 = advance_interface_state(d1.tracking, "down", T0 + STEP)
    assert d2.tracking.state == "down"
    assert d2.incident_started_at == T0  # Down began at the first down sample


def test_single_down_sample_does_not_confirm() -> None:
    d1 = advance_interface_state(FRESH, "down", T0)
    up = advance_interface_state(d1.tracking, "up", T0 + STEP)
    d2 = advance_interface_state(up.tracking, "down", T0 + 2 * STEP)
    assert d2.incident_started_at is None
    assert d2.tracking.state == "normal"


def test_recovery_requires_two_valid_up_samples() -> None:
    d1 = advance_interface_state(FRESH, "down", T0)
    d2 = advance_interface_state(d1.tracking, "down", T0 + STEP)
    r1 = advance_interface_state(d2.tracking, "up", T0 + 2 * STEP)
    assert r1.tracking.state == "down"
    assert r1.incident_recovered_at is None
    r2 = advance_interface_state(r1.tracking, "up", T0 + 3 * STEP)
    assert r2.tracking.state == "normal"
    assert r2.incident_recovered_at == T0 + 2 * STEP


def test_missing_sample_neither_counts_nor_breaks_valid_run() -> None:
    """§13.3: missing samples do not participate in the valid-sample count."""

    d1 = advance_interface_state(FRESH, "down", T0)
    # A missing/undetermined sample in between (None, or any other state).
    for invalid in (None, "testing", "unknown", "dormant", "notpresent", "lowerlayerdown"):
        decision = advance_interface_state(d1.tracking, invalid, T0 + STEP)
        assert decision == advance_interface_state(d1.tracking, invalid, T0 + STEP)
        assert decision.tracking.consecutive_down_samples == 1  # untouched
        assert decision.tracking.state == "normal"
    d2 = advance_interface_state(d1.tracking, "down", T0 + 2 * STEP)
    assert d2.tracking.state == "down"
    assert d2.incident_started_at == T0  # the two valid Downs confirm


def test_down_sample_while_already_down_keeps_incident_open() -> None:
    d1 = advance_interface_state(FRESH, "down", T0)
    d2 = advance_interface_state(d1.tracking, "down", T0 + STEP)
    d3 = advance_interface_state(d2.tracking, "down", T0 + 2 * STEP)
    assert d3.tracking.state == "down"
    assert d3.incident_started_at is None  # no duplicate incident signal
    assert d3.tracking.down_run_started_at == T0


def test_failed_recovery_run_interrupted_by_down() -> None:
    d1 = advance_interface_state(FRESH, "down", T0)
    d2 = advance_interface_state(d1.tracking, "down", T0 + STEP)
    r1 = advance_interface_state(d2.tracking, "up", T0 + 2 * STEP)
    broken = advance_interface_state(r1.tracking, "down", T0 + 3 * STEP)
    assert broken.tracking.consecutive_up_samples == 0
    assert broken.tracking.state == "down"
    # Recovery needs two fresh valid Up samples again.
    r2 = advance_interface_state(broken.tracking, "up", T0 + 4 * STEP)
    assert r2.tracking.state == "down"
    r3 = advance_interface_state(r2.tracking, "up", T0 + 5 * STEP)
    assert r3.tracking.state == "normal"
    assert r3.incident_recovered_at == T0 + 4 * STEP


# --- sustained-high runs (§14/§15.3) ---------------------------------------------


def _series(values: list[float | None]) -> list[tuple[datetime, float | None]]:
    return [(T0 + i * STEP, value) for i, value in enumerate(values)]


def test_three_consecutive_samples_at_threshold_confirm() -> None:
    """§14: 3 consecutive 5-minute samples at >=80% last 15 minutes."""

    intervals = find_sustained_high_intervals(_series([85, 80, 80, 40]), 80.0)
    assert len(intervals) == 1
    assert intervals[0].start == T0
    assert intervals[0].end == T0 + 3 * STEP  # exclusive end of the last slot
    assert intervals[0].sample_count == 3
    assert intervals[0].duration_seconds == 900.0


def test_interval_end_is_exclusive_per_sample_slot() -> None:
    """duration = sample_count * sample_interval, not (last - first)."""

    intervals = find_sustained_high_intervals(_series([90, 90, 90]), 80.0)
    assert intervals[0].duration_seconds == 3 * STEP.total_seconds()
    assert intervals[0].end - intervals[0].start == 3 * STEP


def test_custom_sample_interval_is_honored() -> None:
    """A 1-minute series slot: 3 samples cover 3 minutes, not 2."""

    points = [(T0 + i * timedelta(minutes=1), 90.0) for i in range(3)]
    intervals = find_sustained_high_intervals(
        points, 80.0, sample_interval=timedelta(minutes=1)
    )
    assert intervals[0].duration_seconds == 180.0


def test_non_positive_sample_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="sample_interval"):
        find_sustained_high_intervals(_series([90] * 3), 80.0, sample_interval=timedelta(0))


def test_two_samples_are_not_enough() -> None:
    assert find_sustained_high_intervals(_series([90, 90, 10, 90, 90]), 80.0) == []


def test_missing_sample_breaks_continuity() -> None:
    """§14: a missing sample breaks the run — it is never interpolated."""

    intervals = find_sustained_high_intervals(_series([90, None, 90, 90, 90]), 80.0)
    assert len(intervals) == 1
    assert intervals[0].start == T0 + 2 * STEP
    assert intervals[0].end == T0 + 5 * STEP


def test_long_run_is_one_interval() -> None:
    intervals = find_sustained_high_intervals(_series([90] * 6), 80.0)
    assert len(intervals) == 1
    assert intervals[0].sample_count == 6
    assert intervals[0].start == T0
    assert intervals[0].end == T0 + 6 * STEP
    assert intervals[0].duration_seconds == 1800.0


def test_two_separate_runs_are_two_intervals() -> None:
    intervals = find_sustained_high_intervals(_series([90, 90, 90, None, 95, 90, 90]), 80.0)
    assert len(intervals) == 2


def test_custom_required_samples() -> None:
    intervals = find_sustained_high_intervals(_series([90, 90]), 80.0, required_samples=2)
    assert len(intervals) == 1


def test_max_direction_utilization() -> None:
    assert max_direction_utilization(30.0, 70.0) == 70.0
    assert max_direction_utilization(None, 70.0) == 70.0
    assert max_direction_utilization(30.0, None) == 30.0
    assert max_direction_utilization(None, None) is None
