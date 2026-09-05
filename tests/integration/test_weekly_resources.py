"""Integration tests for weekly CPU/memory statistics and the interface
utilization Top 10 (W03-T002, §14/§15.4) over migrated PostgreSQL.

P95 values in these tests use PostgreSQL ``percentile_cont(0.95)`` (linear
interpolation between closest ranks) — the expectations are computed with
the same formula by hand.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend.db.models import Device, DeviceMetric, DevicePollRun, Interface, InterfaceMetric
from backend.monitoring.sustained import max_direction_utilization
from backend.reporting.period import period_for_iso_week
from backend.reporting.resources import (
    device_resource_statistics,
    interface_top_entries,
    metric_statistics,
)

pytestmark = pytest.mark.integration

# ISO 2026-W36: [2026-08-31, 2026-09-07) Asia/Shanghai.
PERIOD = period_for_iso_week(2026, 36)
STEP = timedelta(minutes=5)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        for model in (InterfaceMetric, DeviceMetric, DevicePollRun, Interface, Device):
            session.query(model).delete()
        session.commit()
    yield


def _device(session: Session, name: str) -> Device:
    device = Device(
        name=name,
        management_ip=f"192.0.2.{abs(hash(name)) % 200 + 1}",
        model_family="s10500x",
        credential_profile="default",
    )
    session.add(device)
    session.flush()
    return device


def _seed_device_cycle(
    session: Session,
    device_id: int,
    cycle: datetime,
    *,
    cpu: float | None = None,
    memory: float | None = None,
) -> DevicePollRun:
    run = DevicePollRun(
        device_id=device_id,
        cycle_started_at=cycle,
        status="SUCCESS",
    )
    session.add(run)
    session.flush()
    if cpu is not None or memory is not None:
        session.add(
            DeviceMetric(
                poll_run_id=run.id,
                device_id=device_id,
                collected_at=cycle,
                cpu_usage_percent=cpu,
                memory_usage_percent=memory,
            )
        )
    return run


def _seed_interface_metric(
    session: Session,
    run: DevicePollRun,
    interface_id: int,
    collected_at: datetime,
    *,
    in_util: float | None = None,
    out_util: float | None = None,
) -> None:
    session.add(
        InterfaceMetric(
            poll_run_id=run.id,
            device_id=run.device_id,
            interface_id=interface_id,
            collected_at=collected_at,
            speed_bps=10_000_000_000,
            in_utilization_percent=in_util,
            out_utilization_percent=out_util,
        )
    )


def _interface(session: Session, device_id: int, display_name: str) -> Interface:
    interface = Interface(
        device_id=device_id,
        normalized_name=display_name.lower(),
        display_name=display_name,
        speed_bps=10_000_000_000,
    )
    session.add(interface)
    session.flush()
    return interface


def test_p95_uses_percentile_cont_linear_interpolation(db_engine: Engine) -> None:
    """20 samples 1..20: rank 0.95*(20-1)=18.05 -> 19 + 0.05*(20-19) = 19.05."""

    with Session(db_engine) as session:
        device = _device(session, "core-1")
        for i in range(20):
            _seed_device_cycle(
                session, device.id, PERIOD.start + i * STEP, cpu=float(i + 1)
            )
        session.commit()

        stats = metric_statistics(session, device.id, PERIOD, "cpu_usage_percent")
        assert stats is not None
        assert stats.sample_count == 20
        assert stats.average == pytest.approx(10.5)
        assert stats.maximum == 20.0
        assert stats.p95 == pytest.approx(19.05)


def test_missing_samples_and_null_fields_stay_out(db_engine: Engine) -> None:
    """Cycles without metrics do not count; NULL cpu is not 0 (§6.2)."""

    with Session(db_engine) as session:
        device = _device(session, "core-1")
        # Two cpu samples, one memory-only cycle, one fully missing cycle.
        _seed_device_cycle(session, device.id, PERIOD.start, cpu=40.0, memory=55.0)
        _seed_device_cycle(session, device.id, PERIOD.start + STEP, cpu=60.0)
        _seed_device_cycle(session, device.id, PERIOD.start + 2 * STEP, memory=65.0)
        # cycle 3: no run at all.
        _seed_device_cycle(session, device.id, PERIOD.start + 4 * STEP, memory=45.0)
        session.commit()

        stats = device_resource_statistics(session, device.id, "core-1", PERIOD)
        assert stats.cpu is not None
        assert stats.cpu.sample_count == 2
        assert stats.cpu.average == pytest.approx(50.0)
        assert stats.cpu.maximum == 60.0
        assert stats.cpu.p95 == pytest.approx(59.0)  # N=2: 40 + 0.95*20

        assert stats.memory is not None
        assert stats.memory.sample_count == 3
        assert stats.memory.average == pytest.approx(55.0)


def test_samples_outside_period_excluded(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        _seed_device_cycle(session, device.id, PERIOD.start - STEP, cpu=99.0)
        _seed_device_cycle(session, device.id, PERIOD.end, cpu=98.0)
        _seed_device_cycle(session, device.id, PERIOD.end + STEP, cpu=97.0)
        _seed_device_cycle(session, device.id, PERIOD.start, cpu=10.0)
        session.commit()

        stats = metric_statistics(session, device.id, PERIOD, "cpu_usage_percent")
        assert stats is not None
        assert stats.sample_count == 1
        assert stats.maximum == 10.0


def test_whole_week_without_data_is_none_not_zero(db_engine: Engine) -> None:
    """§6.2: a device without any sample reports 数据缺失 (None), never 0."""

    with Session(db_engine) as session:
        device = _device(session, "silent-core")
        session.commit()
        stats = device_resource_statistics(session, device.id, "silent-core", PERIOD)
        assert stats.cpu is None
        assert stats.memory is None


def test_metric_statistics_rejects_unknown_field(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        session.commit()
        with pytest.raises(ValueError, match="unknown device metric field"):
            metric_statistics(session, device.id, PERIOD, "temperature")


def test_top10_ranks_by_p95_of_unified_sample_value(db_engine: Engine) -> None:
    """§15.4: per-sample max(in, out), then P95 per interface, descending."""

    with Session(db_engine) as session:
        device_a = _device(session, "core-1")
        device_b = _device(session, "core-2")

        # iface-a: samples (10, 40), (None, 30), (25, None) -> unified
        # [25, 30, 40]: p95 = 30 + 0.9*(40-30) = 39.0, max 40.
        iface_a = _interface(session, device_a.id, "XGE1/0/1")
        samples = ((10.0, 40.0), (None, 30.0), (25.0, None))
        for i, (in_util, out_util) in enumerate(samples):
            cycle = PERIOD.start + i * STEP
            run = _seed_device_cycle(session, device_a.id, cycle, cpu=10.0)
            _seed_interface_metric(
                session, run, iface_a.id, cycle, in_util=in_util, out_util=out_util
            )

        # iface-b: constant 80 -> p95 80.0 (rank 1).
        iface_b = _interface(session, device_b.id, "XGE1/0/2")
        for i in range(3):
            cycle = PERIOD.start + i * STEP
            run_b = _seed_device_cycle(session, device_b.id, cycle, cpu=10.0)
            _seed_interface_metric(
                session, run_b, iface_b.id, cycle, in_util=80.0, out_util=80.0
            )

        session.commit()

        # The SQL max-direction expression must agree with the Python
        # max_direction_utilization (one statistical implementation, §15.4).
        unified = [max_direction_utilization(i, o) for i, o in samples]
        assert unified == [40.0, 30.0, 25.0]

        top = interface_top_entries(session, PERIOD)
        assert [entry.interface_id for entry in top] == [iface_b.id, iface_a.id]

        first, second = top
        assert first.p95 == pytest.approx(80.0)
        assert first.sample_count == 3
        assert first.device_name == "core-2"
        assert first.display_name == "XGE1/0/2"

        assert second.p95 == pytest.approx(39.0)
        assert second.sample_count == 3
        assert second.average == pytest.approx((40.0 + 30.0 + 25.0) / 3.0)
        assert second.maximum == 40.0
        assert second.description is None


def test_top10_excludes_interfaces_without_valid_samples(db_engine: Engine) -> None:
    """Both directions NULL (e.g. rebaselined samples) contribute nothing."""

    with Session(db_engine) as session:
        device = _device(session, "core-1")
        iface = _interface(session, device.id, "XGE1/0/9")
        run = _seed_device_cycle(session, device.id, PERIOD.start, cpu=10.0)
        _seed_interface_metric(session, run, iface.id, PERIOD.start, in_util=None, out_util=None)
        session.commit()

        assert interface_top_entries(session, PERIOD) == []


def test_top10_limit_and_deterministic_tie_break(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        run = _seed_device_cycle(session, device.id, PERIOD.start, cpu=10.0)
        ifaces = []
        for i in range(12):
            iface = _interface(session, device.id, f"XGE1/0/{i + 1}")
            ifaces.append(iface)
            _seed_interface_metric(
                session, run, iface.id, PERIOD.start, in_util=50.0, out_util=50.0
            )
        session.commit()

        top = interface_top_entries(session, PERIOD, limit=10)
        assert len(top) == 10
        assert all(entry.p95 == 50.0 for entry in top)
        # Same P95: deterministic order by device name, then interface name.
        names = [entry.normalized_name for entry in top]
        assert names == sorted(names)[:10]
