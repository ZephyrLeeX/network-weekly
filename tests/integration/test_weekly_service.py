"""Integration tests for the WeeklyStatisticsService overall-status rules
(W03-T006, §19) over migrated PostgreSQL.

Seeds one scenario per §19 condition and pins the resulting status plus
the deterministic summary text.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from backend.db.models import (
    Device,
    DeviceMetric,
    DevicePollRun,
    DeviceReachabilityIncident,
    Interface,
    InterfaceMetric,
    InterfaceMonitoringState,
    InterfaceStateIncident,
)
from backend.reporting.period import period_for_iso_week
from backend.reporting.service import (
    STATUS_ABNORMAL,
    STATUS_ATTENTION,
    STATUS_NORMAL,
    build_weekly_report_data,
)

pytestmark = pytest.mark.integration

PERIOD = period_for_iso_week(2026, 36)  # [2026-08-31, 2026-09-07) Shanghai
STEP = timedelta(minutes=5)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        for model in (
            InterfaceMetric,
            DeviceMetric,
            DevicePollRun,
            DeviceReachabilityIncident,
            InterfaceStateIncident,
            InterfaceMonitoringState,
            Interface,
            Device,
        ):
            session.query(model).delete()
        session.commit()
        yield


def _device(session: Session, name: str, *, expected_members: int | None = None) -> Device:
    device = Device(
        name=name,
        management_ip=f"192.0.2.{abs(hash(name)) % 200 + 1}",
        model_family="s10500x",
        expected_irf_member_count=expected_members,
        credential_profile="default",
    )
    session.add(device)
    session.flush()
    return device


def _good_week(session: Session, device_id: int, cycles: int = 10) -> None:
    """Low, healthy CPU/memory SUCCESS cycles so coverage/sustained pass."""

    runs: list[DevicePollRun] = []
    metrics: list[DeviceMetric] = []
    for i in range(cycles):
        cycle = PERIOD.start + i * STEP
        run = DevicePollRun(device_id=device_id, cycle_started_at=cycle, status="SUCCESS")
        runs.append(run)
        metrics.append(
            DeviceMetric(
                poll_run=run,
                device_id=device_id,
                collected_at=cycle,
                cpu_usage_percent=20.0,
                memory_usage_percent=30.0,
            )
        )
    session.add_all([*runs, *metrics])
    session.flush()


_FULL_WEEK_CYCLES = 7 * 24 * 12  # §18.1: 2016 planned cycles


def _full_good_week(session: Session, device_id: int) -> None:
    """A complete, healthy week: every planned cycle SUCCESS and normal."""

    _good_week(session, device_id, cycles=_FULL_WEEK_CYCLES)


def _run_for_cycle(session: Session, device_id: int, cycle: datetime) -> DevicePollRun:
    """The cycle's poll run, created (SUCCESS) only when missing."""

    run = session.execute(
        select(DevicePollRun).where(
            DevicePollRun.device_id == device_id,
            DevicePollRun.cycle_started_at == cycle,
        )
    ).scalar_one_or_none()
    if run is None:
        run = DevicePollRun(device_id=device_id, cycle_started_at=cycle, status="SUCCESS")
        session.add(run)
        session.flush()
    return run


def test_fully_empty_week_is_attention_with_missing_data(db_engine: Engine) -> None:
    """No data at all: coverage 0% → 关注 + 数据完整性不足 (§6.2/§18.3)."""

    with Session(db_engine) as session:
        _device(session, "core-1")
        session.commit()

        data = build_weekly_report_data(session, PERIOD)
        assert data.overall_status == STATUS_ATTENTION
        assert "数据完整性不足" in data.summary_text
        assert data.coverage.coverage_percent == 0.0
        assert all(d.cpu is None and d.memory is None for d in data.device_resources)
        assert data.interface_top10 == ()
        assert data.counter_top10 == ()


def test_healthy_week_is_normal(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        _full_good_week(session, device.id)
        session.commit()

        data = build_weekly_report_data(session, PERIOD)
        assert data.overall_status == STATUS_NORMAL
        assert "数据完整性不足" not in data.summary_text
        stats = data.device_resources[0]
        assert stats.cpu is not None and stats.cpu.average == pytest.approx(20.0)
        assert stats.memory is not None and stats.memory.p95 == pytest.approx(30.0)


def test_open_device_incident_is_abnormal(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        _good_week(session, device.id)
        session.add(
            DeviceReachabilityIncident(
                device_id=device.id,
                started_at=datetime(2026, 9, 3, tzinfo=UTC),
                recovered_at=None,
            )
        )
        session.commit()

        data = build_weekly_report_data(session, PERIOD)
        assert data.overall_status == STATUS_ABNORMAL


def test_device_incident_recovered_after_period_end_is_abnormal(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        _good_week(session, device.id)
        session.add(
            DeviceReachabilityIncident(
                device_id=device.id,
                started_at=datetime(2026, 9, 4, tzinfo=UTC),
                recovered_at=datetime(2026, 9, 8, tzinfo=UTC),  # after period end
            )
        )
        session.commit()

        assert build_weekly_report_data(session, PERIOD).overall_status == STATUS_ABNORMAL


def test_recovered_device_incident_is_attention(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        _good_week(session, device.id)
        session.add(
            DeviceReachabilityIncident(
                device_id=device.id,
                started_at=datetime(2026, 9, 2, tzinfo=UTC),
                recovered_at=datetime(2026, 9, 2, 2, 0, tzinfo=UTC),
            )
        )
        session.commit()

        data = build_weekly_report_data(session, PERIOD)
        assert data.overall_status == STATUS_ATTENTION
        assert "设备掉线 1 次" in data.summary_text


def test_open_interface_incident_is_abnormal(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        _good_week(session, device.id)
        interface = Interface(
            device_id=device.id,
            normalized_name="xge1/0/1",
            display_name="XGE1/0/1",
            monitored=True,
        )
        session.add(interface)
        session.flush()
        session.add(
            InterfaceStateIncident(
                interface_id=interface.id,
                started_at=datetime(2026, 9, 3, tzinfo=UTC),
                recovered_at=None,
            )
        )
        session.commit()

        assert build_weekly_report_data(session, PERIOD).overall_status == STATUS_ABNORMAL


def test_cpu_sustained_high_is_attention(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        _good_week(session, device.id, cycles=2010)
        # 3 consecutive 90% samples at the end confirm sustained high (§14).
        for i in range(3):
            cycle = PERIOD.start + (2010 + i) * STEP
            run = DevicePollRun(device_id=device.id, cycle_started_at=cycle, status="SUCCESS")
            session.add(run)
            session.flush()
            session.add(
                DeviceMetric(
                    poll_run_id=run.id,
                    device_id=device.id,
                    collected_at=cycle,
                    cpu_usage_percent=90.0,
                    memory_usage_percent=30.0,
                )
            )
        session.commit()

        data = build_weekly_report_data(session, PERIOD)
        assert data.overall_status == STATUS_ATTENTION
        assert "CPU 持续高负载 1 台" in data.summary_text
        assert data.overall_status != STATUS_ABNORMAL


def test_interface_high_utilization_is_attention(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        _good_week(session, device.id)
        interface = Interface(
            device_id=device.id,
            normalized_name="xge1/0/1",
            display_name="XGE1/0/1",
            monitored=True,
            speed_bps=10_000_000_000,
        )
        session.add(interface)
        session.flush()
        for i in range(3):
            cycle = PERIOD.start + i * STEP
            run = _run_for_cycle(session, device.id, cycle)
            session.add(
                InterfaceMetric(
                    poll_run_id=run.id,
                    device_id=device.id,
                    interface_id=interface.id,
                    collected_at=cycle,
                    speed_bps=10_000_000_000,
                    in_utilization_percent=85.0,
                    out_utilization_percent=10.0,
                )
            )
        session.commit()

        data = build_weekly_report_data(session, PERIOD)
        assert data.overall_status == STATUS_ATTENTION
        assert len(data.high_utilization) == 1
        assert data.high_utilization[0].intervals[0].sample_count == 3
        assert "重点接口持续高利用率 1 个" in data.summary_text


def test_large_counter_deltas_do_not_change_status(db_engine: Engine) -> None:
    """§19: CRC/Error/Drop observations never change the overall status."""

    with Session(db_engine) as session:
        device = _device(session, "core-1")
        _full_good_week(session, device.id)
        interface = Interface(
            device_id=device.id,
            normalized_name="xge1/0/1",
            display_name="XGE1/0/1",
            speed_bps=10_000_000_000,
        )
        session.add(interface)
        session.flush()
        crc = 0
        for i in range(4):
            cycle = PERIOD.start + i * STEP
            run = _run_for_cycle(session, device.id, cycle)
            crc += 10_000_000
            session.add(
                InterfaceMetric(
                    poll_run_id=run.id,
                    device_id=device.id,
                    interface_id=interface.id,
                    collected_at=cycle,
                    speed_bps=10_000_000_000,
                    fcs_errors=crc,
                )
            )
        session.commit()

        data = build_weekly_report_data(session, PERIOD)
        assert data.overall_status == STATUS_NORMAL  # 30M CRC increment: observation only
        assert data.counter_top10[0].crc_delta == 30_000_000


def test_status_precedence_abnormal_over_attention(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        # Low coverage + recovered incident (关注 conditions) + open incident (异常).
        session.add(
            DeviceReachabilityIncident(
                device_id=device.id,
                started_at=datetime(2026, 9, 2, tzinfo=UTC),
                recovered_at=datetime(2026, 9, 2, 3, 0, tzinfo=UTC),
            )
        )
        session.add(
            DeviceReachabilityIncident(
                device_id=device.id,
                started_at=datetime(2026, 9, 5, tzinfo=UTC),
                recovered_at=None,
            )
        )
        session.commit()

        data = build_weekly_report_data(session, PERIOD)
        assert data.overall_status == STATUS_ABNORMAL
        assert "总体状态：异常" in data.summary_text


def test_summary_text_contains_week_and_period(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        _full_good_week(session, device.id)
        session.commit()

        data = build_weekly_report_data(session, PERIOD)
        assert data.period.week_code == "2026-W36"
        assert "2026-W36" in data.summary_text
        assert "总体状态：正常" in data.summary_text
        assert "Monitoring Coverage" in data.summary_text
        # Deterministic: rebuilding gives byte-identical text.
        rebuilt = build_weekly_report_data(
            session, PERIOD, generated_at=data.generated_at
        )
        assert rebuilt.summary_text == data.summary_text
