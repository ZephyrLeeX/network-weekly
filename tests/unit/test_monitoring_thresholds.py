"""Unit tests for threshold configuration validation and cycle slots
(W02-T007).
"""

from datetime import UTC, datetime, timedelta

import pytest

from backend.monitoring.thresholds import (
    CPU_KEY,
    INTERFACE_UTIL_KEY,
    REQUIRED_SAMPLES_KEY,
    ThresholdValueError,
    UnknownThresholdKeyError,
    cycle_slots,
    defaults,
    validate_threshold,
)

T0 = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)


def test_defaults_match_spec() -> None:
    """§14/§15.3: >= 80% for 15 minutes (3 five-minute samples)."""

    thresholds = defaults()
    assert thresholds[CPU_KEY] == 80.0
    assert thresholds["memory_sustained_high_percent"] == 80.0
    assert thresholds[INTERFACE_UTIL_KEY] == 80.0
    assert thresholds[REQUIRED_SAMPLES_KEY] == 3.0


def test_validate_rejects_unknown_key() -> None:
    with pytest.raises(UnknownThresholdKeyError):
        validate_threshold("nonsense_key", 50.0)


def test_validate_rejects_out_of_range_percent() -> None:
    with pytest.raises(ThresholdValueError):
        validate_threshold(CPU_KEY, 0.0)
    with pytest.raises(ThresholdValueError):
        validate_threshold(CPU_KEY, 100.1)
    validate_threshold(CPU_KEY, 100.0)  # valid: exactly 100%


def test_validate_rejects_out_of_range_samples() -> None:
    with pytest.raises(ThresholdValueError):
        validate_threshold(REQUIRED_SAMPLES_KEY, 0.0)
    with pytest.raises(ThresholdValueError):
        validate_threshold(REQUIRED_SAMPLES_KEY, 1.0)  # one sample is not "sustained"
    with pytest.raises(ThresholdValueError):
        validate_threshold(REQUIRED_SAMPLES_KEY, 289.0)
    validate_threshold(REQUIRED_SAMPLES_KEY, 2.0)  # valid


def test_cycle_slots_aligned_and_half_open() -> None:
    # Start mid-interval: the first slot is the NEXT boundary.
    start = T0 + timedelta(seconds=90)
    end = T0 + timedelta(minutes=16)
    assert cycle_slots(start, end) == [
        T0 + timedelta(minutes=5),
        T0 + timedelta(minutes=10),
        T0 + timedelta(minutes=15),
    ]

    # Aligned start includes itself; end is exclusive.
    assert cycle_slots(T0, T0 + timedelta(minutes=5)) == [T0]

    # No slots when the window is empty.
    assert cycle_slots(T0, T0) == []


def test_cycle_slots_requires_aware_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        cycle_slots(datetime(2026, 9, 5, 8, 0, 0), T0)
