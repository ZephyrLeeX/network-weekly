"""Unit tests for §15 utilization + counter rebaseline semantics (W02-T003)."""

from datetime import UTC, datetime, timedelta

import pytest

from backend.monitoring.utilization import (
    MAX_SAMPLE_INTERVAL_SECONDS,
    PreviousSample,
    compute_utilization,
)

PREV_TIME = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
CUR_TIME = PREV_TIME + timedelta(minutes=5)
SPEED = 10_000_000_000  # 10 Gb/s


def _prev(
    in_octets: int | None = 1_000,
    out_octets: int | None = 2_000,
    speed_bps: int | None = SPEED,
    collected_at: datetime = PREV_TIME,
) -> PreviousSample:
    return PreviousSample(
        collected_at=collected_at,
        in_octets=in_octets,
        out_octets=out_octets,
        speed_bps=speed_bps,
    )


def test_first_sample_rebaselines() -> None:
    result = compute_utilization(None, CUR_TIME, 5_000, 6_000, SPEED)
    assert result.rebaselined is True
    assert result.in_utilization_percent is None
    assert result.out_utilization_percent is None
    assert result.elapsed_seconds is None


def test_normal_delta_uses_actual_elapsed_and_speed() -> None:
    # 750 octets in 300 s over 10 Gb/s -> 750*8 / (300 * 10e9) * 100
    expected_in = 750 * 8 / (300 * SPEED) * 100
    result = compute_utilization(_prev(), CUR_TIME, 1_750, 2_000, SPEED)
    assert result.rebaselined is False
    assert result.elapsed_seconds == 300.0
    assert result.in_utilization_percent == pytest.approx(expected_in)
    assert result.out_utilization_percent == 0.0  # zero delta is a valid 0%


def test_counter_reset_rebaselines_and_cannot_spike() -> None:
    result = compute_utilization(_prev(in_octets=10**12), CUR_TIME, 500, 2_000, SPEED)
    assert result.rebaselined is True
    assert result.in_utilization_percent is None  # no fake spike (§15.2)
    # The unaffected direction still computes.
    assert result.out_utilization_percent == 0.0


def test_missing_current_counters_rebaseline_their_direction() -> None:
    result = compute_utilization(_prev(), CUR_TIME, None, 2_500, SPEED)
    assert result.in_utilization_percent is None
    assert result.out_utilization_percent == pytest.approx(
        500 * 8 / (300 * SPEED) * 100
    )
    assert result.rebaselined is True


def test_zero_speed_rebaselines() -> None:
    result = compute_utilization(_prev(), CUR_TIME, 1_750, 2_500, 0)
    assert result.rebaselined is True
    assert result.in_utilization_percent is None
    assert result.out_utilization_percent is None


def test_negative_speed_rebaselines() -> None:
    assert compute_utilization(_prev(), CUR_TIME, 1_750, 2_500, -1).rebaselined is True


def test_missing_speed_rebaselines() -> None:
    result = compute_utilization(_prev(), CUR_TIME, 1_750, 2_500, None)
    assert result.rebaselined is True
    assert result.in_utilization_percent is None


def test_speed_change_between_samples_rebaselines() -> None:
    result = compute_utilization(_prev(), CUR_TIME, 1_750, 2_500, SPEED * 10)
    assert result.rebaselined is True
    assert result.in_utilization_percent is None


def test_non_positive_interval_rebaselines() -> None:
    same_time = compute_utilization(
        _prev(collected_at=CUR_TIME), CUR_TIME, 1_750, 2_500, SPEED
    )
    assert same_time.rebaselined is True

    future_prev = compute_utilization(
        _prev(collected_at=CUR_TIME + timedelta(seconds=1)), CUR_TIME, 1_750, 2_500, SPEED
    )
    assert future_prev.rebaselined is True


def test_stale_gap_beyond_max_interval_rebaselines() -> None:
    # Exactly three poll cycles (900 s) is still a valid averaging window.
    beyond = PREV_TIME + timedelta(seconds=MAX_SAMPLE_INTERVAL_SECONDS + 1)
    result = compute_utilization(_prev(), beyond, 1_750, 2_500, SPEED)
    assert result.rebaselined is True
    assert result.in_utilization_percent is None

    just_within = PREV_TIME + timedelta(seconds=MAX_SAMPLE_INTERVAL_SECONDS)
    ok = compute_utilization(_prev(), just_within, 1_750, 2_500, SPEED)
    assert ok.rebaselined is False
    assert ok.elapsed_seconds == MAX_SAMPLE_INTERVAL_SECONDS


def test_impossible_utilization_rebaselines() -> None:
    # Delta implying far more bits than the wire can carry in the interval.
    huge = 10**12
    result = compute_utilization(_prev(in_octets=0), CUR_TIME, huge, 2_000, SPEED)
    assert result.in_utilization_percent is None
    assert result.rebaselined is True


def test_equal_counters_compute_zero_utilization() -> None:
    result = compute_utilization(_prev(), CUR_TIME, 1_000, 2_000, SPEED)
    assert result.rebaselined is False
    assert result.in_utilization_percent == 0.0
    assert result.out_utilization_percent == 0.0
