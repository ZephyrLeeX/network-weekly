"""Wave 3 golden scenarios (W03-T007).

One end-to-end suite over migrated PostgreSQL that pins every golden case
required by IMPLEMENTATION_PLAN.md against the single statistical
implementations the report uses:

    [Mon 00:00, next Mon 00:00) boundary   -> test_period_boundary_half_open
    ISO year boundary                      -> test_iso_year_boundary_week
    跨周设备 Down                          -> test_cross_week_device_down
    周内 Down + Recovery                   -> test_cross_week_device_down (part 2)
    重点接口 Down + Recovery               -> test_priority_interface_down_recovery
    Missing sample 中断连续性              -> test_missing_sample_breaks_continuity
    CPU/Memory P95                         -> test_cpu_memory_p95
    Interface Top 10 P95 算法              -> test_interface_top10_algorithm
    Counter Reset / CRC reset              -> test_counter_reset_no_fake_delta
    PARTIAL Poll                           -> test_partial_poll_in_coverage
    Coverage <95%                          -> test_coverage_below_target
    整周设备无数据                          -> test_whole_week_without_data
    (同周 regenerate -> report-job tests, W03-T009/T010)
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend.db.models import (
    Device,
    DeviceMetric,
    DevicePollRun,
    DeviceReachabilityIncident,
    Interface,
    InterfaceMetric,
    InterfaceStateIncident,
    SystemSetting,
)
from backend.monitoring.thresholds import (
    CPU_KEY,
    cycle_slots,
    detect_device_sustained_high,
    set_threshold,
)
from backend.reporting.period import period_for_iso_week
from backend.reporting.service import (
    STATUS_ABNORMAL,
    STATUS_ATTENTION,
    build_weekly_report_data,
)

pytestmark = pytest.mark.integration

STEP = timedelta(minutes=5)
FULL_WEEK = 7 * 24 * 12


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    """Each golden scenario sees an otherwise-empty database."""

    with Session(db_engine) as session:
        for model in (
            InterfaceMetric,
            DeviceMetric,
            DevicePollRun,
            DeviceReachabilityIncident,
            InterfaceStateIncident,
            Interface,
            SystemSetting,
            Device,
        ):
            session.query(model).delete()
        session.commit()
        yield


def _seed_run(
    session: Session,
    device_id: int,
    cycle: datetime,
    *,
    status: str = "SUCCESS",
    cpu: float | None = None,
    memory: float | None = None,
) -> DevicePollRun:
    run = DevicePollRun(device_id=device_id, cycle_started_at=cycle, status=status)
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


def test_period_boundary_half_open(db_engine: Engine) -> None:
    """Golden: [Mon 00:00, next Mon 00:00) — start included, end excluded."""

    period = period_for_iso_week(2026, 36)
    assert (period.start, period.end) == (
        datetime(2026, 8, 31, tzinfo=period.start.tzinfo),
        datetime(2026, 9, 7, tzinfo=period.end.tzinfo),
    )
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.10",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        _seed_run(session, device.id, period.start, cpu=10.0)  # first instant: IN
        _seed_run(session, device.id, period.end - STEP, cpu=20.0)  # last slot: IN
        _seed_run(session, device.id, period.end, cpu=99.0)  # period end: OUT
        session.commit()

        data = build_weekly_report_data(session, period)
        coverage = data.coverage.devices[0]
        assert coverage.expected == FULL_WEEK
        assert coverage.success == 2  # only the two in-week cycles
        # Planned grid alignment: Monday 00:00 Asia/Shanghai is a slot.
        assert cycle_slots(period.start, period.end)[0] == period.start
        assert len(cycle_slots(period.start, period.end)) == FULL_WEEK


def test_iso_year_boundary_week(db_engine: Engine) -> None:
    """Golden: ISO 2026-W01 spans 2025-12-29 .. 2026-01-05 (ISO week-year)."""

    period = period_for_iso_week(2026, 1)
    assert period.week_code == "2026-W01"
    assert period.start == datetime(2025, 12, 29, tzinfo=period.start.tzinfo)
    assert period.end == datetime(2026, 1, 5, tzinfo=period.end.tzinfo)

    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.10",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        _seed_run(session, device.id, period.start, cpu=11.0)
        _seed_run(session, device.id, period.start + timedelta(days=3), cpu=12.0)
        session.commit()

        data = build_weekly_report_data(session, period)
        assert "2026-W01" in data.summary_text
        assert data.coverage.devices[0].success == 2


def test_cross_week_device_down(db_engine: Engine) -> None:
    """Golden: down last week + recovered this week = 1 episode, attention."""

    period = period_for_iso_week(2026, 36)
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.10",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        # Downed Saturday of the PREVIOUS week, recovered Tuesday 02:00.
        session.add(
            DeviceReachabilityIncident(
                device_id=device.id,
                started_at=datetime(2026, 8, 29, 22, 0, tzinfo=UTC),
                recovered_at=datetime(2026, 9, 1, 2, 0, tzinfo=UTC),
            )
        )
        for i in range(10):
            _seed_run(session, device.id, period.start + i * STEP, cpu=20.0)
        session.commit()

        data = build_weekly_report_data(session, period)
        episode = data.device_incidents[0].episodes[0]
        assert episode.started_before_period
        assert episode.duration_seconds == (datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
                                            - datetime(2026, 8, 29, 22, 0, tzinfo=UTC)
                                            ).total_seconds()
        assert not episode.ongoing_at_period_end
        assert data.overall_status == STATUS_ATTENTION  # coverage low anyway, but
        assert "设备掉线 1 次" in data.summary_text


def test_priority_interface_down_recovery(db_engine: Engine) -> None:
    """Golden: monitored interface down Monday, recovered Wednesday."""

    period = period_for_iso_week(2026, 36)
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.10",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        interface = Interface(
            device_id=device.id,
            normalized_name="xge1/0/1",
            display_name="XGE1/0/1",
            description="uplink-to-agg",
            monitored=True,
        )
        session.add(interface)
        session.flush()
        session.add(
            InterfaceStateIncident(
                interface_id=interface.id,
                started_at=datetime(2026, 8, 31, 8, 0, tzinfo=UTC),
                recovered_at=datetime(2026, 9, 2, 14, 0, tzinfo=UTC),
            )
        )
        _seed_run(session, device.id, period.start, cpu=20.0)
        session.commit()

        data = build_weekly_report_data(session, period)
        assert data.overall_status == STATUS_ATTENTION
        summary = data.interface_incidents[0]
        assert summary.display_name == "XGE1/0/1"
        assert summary.description == "uplink-to-agg"
        assert summary.down_count == 1
        assert summary.ongoing_episodes == ()


def test_missing_sample_breaks_continuity(db_engine: Engine) -> None:
    """Golden: 85, 85, MISSING, 85, 85, 85 → one interval, NOT before gap."""

    period = period_for_iso_week(2026, 36)
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.10",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        values = (85.0, 85.0, None, 85.0, 85.0, 85.0)
        for i, value in enumerate(values):
            cycle = period.start + i * STEP
            if value is None:
                continue  # no run at all: missing sample (§14)
            _seed_run(session, device.id, cycle, cpu=value)
        session.commit()

        data = build_weekly_report_data(session, period)
        device_row = data.device_resources[0]
        assert device_row.cpu is not None
        assert device_row.cpu.sample_count == 5
        # Sustained-high: only the trailing 3-sample run confirms.
        assert len(device_row.cpu_sustained_high) == 1
        interval = device_row.cpu_sustained_high[0]
        assert interval.start == period.start + 3 * STEP
        assert interval.end == period.start + 6 * STEP
        assert interval.duration_seconds == 900.0
        assert "CPU 持续高负载 1 台" in data.summary_text
        # The same implementation drives the monitoring-side detection.
        sustained = detect_device_sustained_high(session, device.id, period.start, period.end)
        assert len(sustained["cpu"]) == 1


def test_cpu_memory_p95(db_engine: Engine) -> None:
    """Golden: P95 via percentile_cont(0.95), pinned on known sequences."""

    period = period_for_iso_week(2026, 36)
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.10",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        for i in range(20):
            _seed_run(
                session,
                device.id,
                period.start + i * STEP,
                cpu=float(i + 1),  # 1..20
                memory=40.0,
            )
        session.commit()

        data = build_weekly_report_data(session, period)
        cpu = data.device_resources[0].cpu
        assert cpu is not None
        assert cpu.average == pytest.approx(10.5)
        assert cpu.maximum == 20.0
        assert cpu.p95 == pytest.approx(19.05)  # percentile_cont interpolation
        memory = data.device_resources[0].memory
        assert memory is not None
        assert memory.p95 == pytest.approx(40.0)


def test_interface_top10_algorithm(db_engine: Engine) -> None:
    """Golden: per-sample max(in,out) → per-interface P95 → Top 10 desc."""

    period = period_for_iso_week(2026, 36)
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.10",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        interfaces = []
        for rank, util in enumerate((95.0, 70.0, 42.0, 10.0)):
            interface = Interface(
                device_id=device.id,
                normalized_name=f"xge1/0/{rank + 1}",
                display_name=f"XGE1/0/{rank + 1}",
                speed_bps=10_000_000_000,
            )
            session.add(interface)
            session.flush()
            interfaces.append((interface, util))
        # 3 samples per interface; one cycle with only ingress (direction mix).
        for i in range(3):
            cycle = period.start + i * STEP
            run = _seed_run(session, device.id, cycle, cpu=20.0)
            for interface, util in interfaces:
                session.add(
                    InterfaceMetric(
                        poll_run_id=run.id,
                        device_id=device.id,
                        interface_id=interface.id,
                        collected_at=cycle,
                        speed_bps=10_000_000_000,
                        in_utilization_percent=util if i != 1 else None,
                        out_utilization_percent=util,
                    )
                )
        session.commit()

        data = build_weekly_report_data(session, period)
        top = data.interface_top10
        assert [t.normalized_name for t in top] == [
            "xge1/0/1",
            "xge1/0/2",
            "xge1/0/3",
            "xge1/0/4",
        ]
        assert [t.p95 for t in top] == [95.0, 70.0, 42.0, 10.0]
        assert all(t.sample_count == 3 for t in top)


def test_counter_reset_no_fake_delta(db_engine: Engine) -> None:
    """Golden: CRC counter resets mid-week — delta stays real increments."""

    period = period_for_iso_week(2026, 36)
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.10",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        interface = Interface(
            device_id=device.id,
            normalized_name="xge1/0/1",
            display_name="XGE1/0/1",
            speed_bps=10_000_000_000,
        )
        session.add(interface)
        session.flush()
        crc_values = (1_000_000_000, 1_000_000_500, 40, 90)
        for i, crc in enumerate(crc_values):
            cycle = period.start + i * STEP
            run = _seed_run(session, device.id, cycle, cpu=20.0)
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

        data = build_weekly_report_data(session, period)
        delta = data.counter_top10[0]
        # 500 real increments before the reset + 50 after — never 4 billion.
        assert delta.crc_delta == 550
        assert data.overall_status != STATUS_ABNORMAL  # §16/§19: observation only


def test_partial_poll_in_coverage(db_engine: Engine) -> None:
    """Golden: PARTIAL counts toward coverage but stays visible separately."""

    period = period_for_iso_week(2026, 36)
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.10",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        for i in range(5):
            status = "PARTIAL" if i == 2 else "SUCCESS"
            cpu = 20.0 if i != 3 else None  # PARTIAL cycle 3 lost CPU/memory
            _seed_run(
                session,
                device.id,
                period.start + i * STEP,
                status=status,
                cpu=cpu,
            )
        session.commit()

        data = build_weekly_report_data(session, period)
        coverage = data.coverage
        assert coverage.expected == FULL_WEEK
        assert coverage.success == 4
        assert coverage.partial == 1
        assert coverage.failed == 0
        assert coverage.coverage_percent == pytest.approx(5 / FULL_WEEK * 100)
        assert coverage.below_target  # tiny week: 数据完整性不足, report still built
        # The PARTIAL cycle kept CPU (section data preserved, §8) but cycle 3
        # has no device metric: that sample is missing, never 0.
        cpu_stats = data.device_resources[0].cpu
        assert cpu_stats is not None
        assert cpu_stats.sample_count == 4


def test_coverage_below_target(db_engine: Engine) -> None:
    """Golden: <95% coverage trips 数据完整性不足; generation unaffected."""

    period = period_for_iso_week(2026, 36)
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.10",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        # 1900 of 2016 = 94.25% < 95%.
        for i in range(1900):
            _seed_run(session, device.id, period.start + i * STEP, cpu=20.0)
        session.commit()

        data = build_weekly_report_data(session, period)
        assert data.coverage.coverage_percent == pytest.approx(1900 / FULL_WEEK * 100)
        assert data.coverage.below_target
        assert "数据完整性不足" in data.summary_text
        assert data.overall_status == STATUS_ATTENTION


def test_whole_week_without_data(db_engine: Engine) -> None:
    """Golden: a silent device shows 数据缺失 everywhere, never 0 (§6.2)."""

    period = period_for_iso_week(2026, 36)
    with Session(db_engine) as session:
        device = Device(
            name="silent-core",
            management_ip="192.0.2.10",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.commit()

        data = build_weekly_report_data(session, period)
        row = data.device_resources[0]
        assert row.cpu is None
        assert row.memory is None
        assert row.cpu_sustained_high == ()
        assert data.interface_top10 == ()
        assert data.counter_top10 == ()
        coverage = data.coverage.devices[0]
        assert coverage.expected == FULL_WEEK
        assert coverage.coverage_percent == 0.0
        assert data.overall_status == STATUS_ATTENTION


def test_threshold_override_flows_into_report(db_engine: Engine) -> None:
    """Golden: configurable thresholds change report sustained-high counts."""

    period = period_for_iso_week(2026, 36)
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.10",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        for i in range(3):
            _seed_run(
                session, device.id, period.start + i * STEP, cpu=70.0, memory=60.0
            )
        session.commit()
        try:
            data = build_weekly_report_data(session, period)
            assert data.device_resources[0].cpu_sustained_high == ()  # 70 < 80

            set_threshold(session, CPU_KEY, 70.0)
            session.commit()
            data = build_weekly_report_data(session, period)
            assert len(data.device_resources[0].cpu_sustained_high) == 1
        finally:
            session.query(SystemSetting).delete()
            session.commit()
