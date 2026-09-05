"""Configurable monitoring thresholds and sustained-high detection entry
points (W02-T007, SYSTEM_SPEC.md §14/§15.3).

Defaults per spec:

    CPU >= 80% for 15 minutes        (3 consecutive 5-minute samples)
    Memory >= 80% for 15 minutes     (3 consecutive 5-minute samples)
    Priority interface utilization >= 80% for 15 minutes

Thresholds are stored in `system_settings` (migration 0006) and are
therefore runtime-configurable without a redeploy. Series are read from the
W02-T001 raw tables over the planned 5-minute cycle grid, so a cycle with no
row is a None point — a missing sample that breaks continuity (§14) — and
is never interpolated.

The interval detection itself is the W02-T006 pure core
(:func:`backend.monitoring.sustained.find_sustained_high_intervals`); this
module only binds it to configured thresholds and persisted series.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import (
    DeviceMetric,
    DevicePollRun,
    InterfaceMetric,
    SystemSetting,
)
from backend.monitoring.sustained import (
    SustainedHighInterval,
    find_sustained_high_intervals,
    max_direction_utilization,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(seconds=300)

CPU_KEY = "cpu_sustained_high_percent"
MEMORY_KEY = "memory_sustained_high_percent"
INTERFACE_UTIL_KEY = "interface_high_utilization_percent"
REQUIRED_SAMPLES_KEY = "sustained_high_required_samples"

# key -> (default, minimum-exclusive/inclusive bounds)
_THRESHOLDS: dict[str, tuple[float, float, float]] = {
    CPU_KEY: (80.0, 0.0, 100.0),
    MEMORY_KEY: (80.0, 0.0, 100.0),
    INTERFACE_UTIL_KEY: (80.0, 0.0, 100.0),
    REQUIRED_SAMPLES_KEY: (3.0, 1.0, 288.0),  # at most one day of 5-min cycles
}

PERCENT_KEYS = (CPU_KEY, MEMORY_KEY, INTERFACE_UTIL_KEY)
CPU_FIELD = "cpu_usage_percent"
MEMORY_FIELD = "memory_usage_percent"


class UnknownThresholdKeyError(KeyError):
    """Raised when setting a threshold key that does not exist."""


class ThresholdValueError(ValueError):
    """Raised when a threshold value is outside its valid range."""


def defaults() -> dict[str, float]:
    return {key: default for key, (default, _, _) in _THRESHOLDS.items()}


def validate_threshold(key: str, value: float) -> None:
    if key not in _THRESHOLDS:
        raise UnknownThresholdKeyError(key)
    _, low, high = _THRESHOLDS[key]
    if not low < value <= high:
        raise ThresholdValueError(
            f"{key} must be in ({low}, {high}]; got {value}"
        )


def set_threshold(session: Session, key: str, value: float) -> None:
    """Persist one threshold override; validates key and range."""

    validate_threshold(key, value)
    row = session.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key, value=repr(float(value)))
        session.add(row)
    else:
        row.value = repr(float(value))
        row.updated_at = datetime.now(UTC)


def load_thresholds(session: Session) -> dict[str, float]:
    """Defaults merged with persisted overrides; corrupt rows fall back."""

    thresholds = defaults()
    for row in session.execute(select(SystemSetting)).scalars():
        if row.key not in _THRESHOLDS:
            continue
        try:
            thresholds[row.key] = float(row.value)
        except (TypeError, ValueError):
            logger.warning(
                "system setting %s has a non-numeric value; using the default",
                row.key,
            )
    return thresholds


def cycle_slots(start: datetime, end: datetime) -> list[datetime]:
    """Aligned 5-minute planned-cycle timestamps in [start, end)."""

    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("cycle_slots requires timezone-aware datetimes")
    seconds = int(POLL_INTERVAL.total_seconds())
    epoch = datetime(1970, 1, 1, tzinfo=start.tzinfo)
    remainder = (start - epoch).total_seconds() % seconds
    offset = (seconds - remainder) if remainder else 0.0
    slots: list[datetime] = []
    current = start + timedelta(seconds=offset)
    while current < end:
        slots.append(current)
        current += POLL_INTERVAL
    return slots


def device_metric_series(
    session: Session,
    device_id: int,
    field: str,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, float | None]]:
    """CPU or memory values over the planned cycle grid (None = missing).

    `field` is `cpu_usage_percent` or `memory_usage_percent`. Cycles without
    a metric row (no run, or the section missing) yield None.
    """

    if field not in (CPU_FIELD, MEMORY_FIELD):
        raise ValueError(f"unknown device metric field: {field!r}")
    rows = session.execute(
        select(DevicePollRun.cycle_started_at, getattr(DeviceMetric, field))
        .outerjoin(DeviceMetric, DeviceMetric.poll_run_id == DevicePollRun.id)
        .where(
            DevicePollRun.device_id == device_id,
            DevicePollRun.cycle_started_at >= start,
            DevicePollRun.cycle_started_at < end,
        )
    ).all()
    by_cycle: dict[datetime, float | None] = {cycle: value for cycle, value in rows}
    return [(cycle, by_cycle.get(cycle)) for cycle in cycle_slots(start, end)]


def interface_utilization_series(
    session: Session,
    interface_id: int,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, float | None]]:
    """Per-cycle max(ingress, egress) utilization (§15.4); None when missing."""

    rows = session.execute(
        select(
            DevicePollRun.cycle_started_at,
            InterfaceMetric.in_utilization_percent,
            InterfaceMetric.out_utilization_percent,
        )
        .join(InterfaceMetric, InterfaceMetric.poll_run_id == DevicePollRun.id)
        .where(
            InterfaceMetric.interface_id == interface_id,
            DevicePollRun.cycle_started_at >= start,
            DevicePollRun.cycle_started_at < end,
        )
    ).all()
    by_cycle: dict[datetime, float | None] = {
        cycle: max_direction_utilization(in_util, out_util) for cycle, in_util, out_util in rows
    }
    return [(cycle, by_cycle.get(cycle)) for cycle in cycle_slots(start, end)]


def detect_device_sustained_high(
    session: Session,
    device_id: int,
    start: datetime,
    end: datetime,
    thresholds: dict[str, float] | None = None,
) -> dict[str, list[SustainedHighInterval]]:
    """CPU and memory sustained-high intervals for one device (§14)."""

    thresholds = thresholds or load_thresholds(session)
    return {
        "cpu": find_sustained_high_intervals(
            device_metric_series(session, device_id, CPU_FIELD, start, end),
            thresholds[CPU_KEY],
            required_samples=int(thresholds[REQUIRED_SAMPLES_KEY]),
        ),
        "memory": find_sustained_high_intervals(
            device_metric_series(session, device_id, MEMORY_FIELD, start, end),
            thresholds[MEMORY_KEY],
            required_samples=int(thresholds[REQUIRED_SAMPLES_KEY]),
        ),
    }


def detect_interface_high_utilization(
    session: Session,
    interface_id: int,
    start: datetime,
    end: datetime,
    thresholds: dict[str, float] | None = None,
) -> list[SustainedHighInterval]:
    """Priority-interface sustained high-utilization intervals (§15.3)."""

    thresholds = thresholds or load_thresholds(session)
    return find_sustained_high_intervals(
        interface_utilization_series(session, interface_id, start, end),
        thresholds[INTERFACE_UTIL_KEY],
        required_samples=int(thresholds[REQUIRED_SAMPLES_KEY]),
    )
