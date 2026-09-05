"""Integration tests for CPU/memory sustained-high detection over persisted
series (W02-T007), including threshold overrides in system_settings.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend.collect.session import DeviceCollectionOutcome, SectionResult
from backend.db.models import (
    Device,
    DeviceMetric,
    DevicePollRun,
    Interface,
    InterfaceMetric,
    InterfaceMonitoringState,
    InterfaceStateIncident,
    SystemSetting,
)
from backend.monitoring.pipeline import persist_poll_result
from backend.monitoring.thresholds import (
    CPU_KEY,
    REQUIRED_SAMPLES_KEY,
    ThresholdValueError,
    UnknownThresholdKeyError,
    detect_device_sustained_high,
    load_thresholds,
    set_threshold,
)

pytestmark = pytest.mark.integration

STEP = timedelta(seconds=300)
T0 = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
WINDOW_END = T0 + timedelta(minutes=30)  # six planned cycles


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        for model in (
            InterfaceMetric,
            DeviceMetric,
            DevicePollRun,
            InterfaceStateIncident,
            InterfaceMonitoringState,
            Interface,
            SystemSetting,
            Device,
        ):
            session.query(model).delete()
        session.commit()
    yield


@pytest.fixture
def device_id(db_engine: Engine) -> int:
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.61",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.commit()
        return device.id


def _seed_cycle(
    session: Session,
    device_id: int,
    cycle: datetime,
    *,
    cpu: float | None,
    memory: float | None = None,
) -> None:
    """Persist one cycle's device metric directly (SUCCESS run)."""

    outcome = DeviceCollectionOutcome(
        device_name="core-1",
        sections=[SectionResult("identity", "SUCCESS")],
    )
    persisted = persist_poll_result(session, device_id, cycle, outcome, cycle)
    assert persisted is not None
    run_id = persisted.run_id
    if cpu is not None or memory is not None:
        session.add(
            DeviceMetric(
                poll_run_id=run_id,
                device_id=device_id,
                collected_at=cycle,
                cpu_usage_percent=cpu,
                memory_usage_percent=memory,
            )
        )


def test_load_thresholds_defaults_when_empty(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        thresholds = load_thresholds(session)
        assert thresholds[CPU_KEY] == 80.0
        assert thresholds[REQUIRED_SAMPLES_KEY] == 3.0


def test_set_and_reload_threshold_override(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        set_threshold(session, CPU_KEY, 90.0)
        session.commit()
        assert load_thresholds(session)[CPU_KEY] == 90.0

        set_threshold(session, CPU_KEY, 75.5)
        session.commit()
        assert load_thresholds(session)[CPU_KEY] == 75.5

        with pytest.raises(ThresholdValueError):
            set_threshold(session, CPU_KEY, 120.0)
        with pytest.raises(UnknownThresholdKeyError):
            set_threshold(session, "nope", 1.0)


def test_sustained_high_requires_three_consecutive_valid_cycles(
    db_engine: Engine, device_id: int
) -> None:
    with Session(db_engine) as session:
        # cpu: 85, 90, [MISSING CYCLE], 85, 85, 85
        _seed_cycle(session, device_id, T0, cpu=85.0)
        _seed_cycle(session, device_id, T0 + STEP, cpu=90.0)
        # T0 + 2*STEP: no run at all -> missing sample breaks continuity.
        _seed_cycle(session, device_id, T0 + 3 * STEP, cpu=85.0)
        _seed_cycle(session, device_id, T0 + 4 * STEP, cpu=85.0)
        _seed_cycle(session, device_id, T0 + 5 * STEP, cpu=85.0)
        session.commit()

        result = detect_device_sustained_high(session, device_id, T0, WINDOW_END)
        assert result["cpu"] is not None
        # The 85/90 run died at the missing cycle; the last three confirm.
        assert len(result["cpu"]) == 1
        interval = result["cpu"][0]
        assert interval.start == T0 + 3 * STEP
        # Exclusive end of the last sample's 5-minute slot: 3 samples = 15 min.
        assert interval.end == T0 + 6 * STEP
        assert interval.sample_count == 3
        assert interval.duration_seconds == 900.0
        assert result["memory"] == []


def test_below_threshold_and_single_spikes_do_not_confirm(
    db_engine: Engine, device_id: int
) -> None:
    with Session(db_engine) as session:
        _seed_cycle(session, device_id, T0, cpu=95.0)
        _seed_cycle(session, device_id, T0 + STEP, cpu=40.0)
        _seed_cycle(session, device_id, T0 + 2 * STEP, cpu=95.0)
        _seed_cycle(session, device_id, T0 + 3 * STEP, cpu=70.0)
        session.commit()

        result = detect_device_sustained_high(session, device_id, T0, WINDOW_END)
        assert result["cpu"] == []


def test_memory_series_missing_rows_are_none_not_zero(
    db_engine: Engine, device_id: int
) -> None:
    """A PARTIAL cycle (cpu only) leaves memory None — never 0 (§6.2)."""

    with Session(db_engine) as session:
        _seed_cycle(session, device_id, T0, cpu=20.0)  # memory None
        _seed_cycle(session, device_id, T0 + STEP, cpu=20.0, memory=85.0)
        session.commit()

        from backend.monitoring.thresholds import device_metric_series

        series = device_metric_series(session, device_id, "memory_usage_percent", T0, WINDOW_END)
        assert series[0] == (T0, None)
        assert series[1] == (T0 + STEP, 85.0)
        assert all(value is None for _, value in series[2:])

        result = detect_device_sustained_high(session, device_id, T0, WINDOW_END)
        assert result["memory"] == []  # single sample cannot confirm


def test_configured_threshold_changes_detection(
    db_engine: Engine, device_id: int
) -> None:
    with Session(db_engine) as session:
        for offset, value in ((0 * STEP, 70.0), (STEP, 70.0), (2 * STEP, 70.0)):
            _seed_cycle(session, device_id, T0 + offset, cpu=value)
        session.commit()

        # At the default 80%: no sustained high.
        assert detect_device_sustained_high(session, device_id, T0, WINDOW_END)["cpu"] == []

        # Operator lowers the CPU threshold to 70%: the run now confirms.
        set_threshold(session, CPU_KEY, 70.0)
        session.commit()
        result = detect_device_sustained_high(session, device_id, T0, WINDOW_END)
        assert len(result["cpu"]) == 1
        assert result["cpu"][0].sample_count == 3

        # Operator requires 4 samples instead of 3: no longer confirmed.
        set_threshold(session, REQUIRED_SAMPLES_KEY, 4.0)
        session.commit()
        thresholds = load_thresholds(session)
        result = detect_device_sustained_high(
            session, device_id, T0, WINDOW_END, thresholds=thresholds
        )
        assert result["cpu"] == []


def test_all_device_metrics_series_helper(db_engine: Engine, device_id: int) -> None:
    with Session(db_engine) as session:
        _seed_cycle(session, device_id, T0, cpu=50.0, memory=60.0)
        session.commit()
        from backend.monitoring.thresholds import device_metric_series

        cpu_series = device_metric_series(session, device_id, "cpu_usage_percent", T0, WINDOW_END)
        assert cpu_series[0] == (T0, 50.0)
        assert len(cpu_series) == 6  # 30-minute window / 5-minute cycles

        with pytest.raises(ValueError, match="unknown device metric field"):
            device_metric_series(session, device_id, "bogus", T0, WINDOW_END)
