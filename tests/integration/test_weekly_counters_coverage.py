"""Integration tests for weekly CRC/Error/Drop deltas and Monitoring
Coverage (W03-T005, §16/§18) over migrated PostgreSQL.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend.db.models import Device, DevicePollRun, Interface, InterfaceMetric
from backend.reporting.counters_coverage import (
    counter_delta_top_entries,
    weekly_counter_deltas,
    weekly_coverage,
)
from backend.reporting.period import period_for_iso_week

pytestmark = pytest.mark.integration

# ISO 2026-W36: [2026-08-31, 2026-09-07) Asia/Shanghai.
PERIOD = period_for_iso_week(2026, 36)
STEP = timedelta(minutes=5)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        for model in (InterfaceMetric, DevicePollRun, Interface, Device):
            session.query(model).delete()
        session.commit()
        yield


def _device(session: Session, name: str, *, enabled: bool = True) -> Device:
    device = Device(
        name=name,
        management_ip=f"192.0.2.{abs(hash(name)) % 200 + 1}",
        model_family="s10500x",
        credential_profile="default",
        enabled=enabled,
    )
    session.add(device)
    session.flush()
    return device


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


def _cycle(
    session: Session, device_id: int, cycle: datetime, status: str = "SUCCESS"
) -> DevicePollRun:
    run = DevicePollRun(device_id=device_id, cycle_started_at=cycle, status=status)
    session.add(run)
    session.flush()
    return run


def _sample(
    session: Session,
    run: DevicePollRun,
    interface_id: int,
    collected_at: datetime,
    *,
    crc: int | None = None,
    in_errors: int | None = None,
    out_errors: int | None = None,
    in_discards: int | None = None,
    out_discards: int | None = None,
) -> None:
    session.add(
        InterfaceMetric(
            poll_run_id=run.id,
            device_id=run.device_id,
            interface_id=interface_id,
            collected_at=collected_at,
            speed_bps=10_000_000_000,
            fcs_errors=crc,
            in_errors=in_errors,
            out_errors=out_errors,
            in_discards=in_discards,
            out_discards=out_discards,
        )
    )


def test_weekly_counter_delta_simple_accumulation(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        iface = _interface(session, device.id, "XGE1/0/1")
        for i, crc in enumerate((100, 150, 200)):
            cycle = PERIOD.start + i * STEP
            _sample(session, _cycle(session, device.id, cycle), iface.id, cycle, crc=crc)
        session.commit()

        deltas = weekly_counter_deltas(session, PERIOD)
        assert len(deltas) == 1
        delta = deltas[0]
        assert delta.crc_delta == 100  # 150-100 + 200-150
        assert delta.sample_count == 3
        assert delta.total_delta == 100


def test_counter_reset_never_produces_fake_delta(db_engine: Engine) -> None:
    """A reset rebaselines: only forward increments count (§16)."""

    with Session(db_engine) as session:
        device = _device(session, "core-1")
        iface = _interface(session, device.id, "XGE1/0/1")
        counters = (1_000, 1_500, 5, 60)  # reset between 1500 and 5
        for i, crc in enumerate(counters):
            cycle = PERIOD.start + i * STEP
            _sample(session, _cycle(session, device.id, cycle), iface.id, cycle, crc=crc)
        session.commit()

        delta = weekly_counter_deltas(session, PERIOD)[0]
        # 500 before the reset + 55 after the reset; the wrap is dropped.
        assert delta.crc_delta == 555


def test_missing_counter_column_is_none_not_zero(db_engine: Engine) -> None:
    """Counters the device never reports stay 数据缺失 (§6.2)."""

    with Session(db_engine) as session:
        device = _device(session, "core-1")
        iface = _interface(session, device.id, "XGE1/0/1")
        for i in range(3):
            cycle = PERIOD.start + i * STEP
            _sample(session, _cycle(session, device.id, cycle), iface.id, cycle, crc=10)
        session.commit()

        delta = weekly_counter_deltas(session, PERIOD)[0]
        assert delta.crc_delta == 0  # constant counter: genuinely no increment
        assert delta.in_errors_delta is None  # never reported: 数据缺失
        assert delta.out_errors_delta is None
        assert delta.total_delta == 0  # crc contributes 0, missing columns skipped


def test_interface_without_any_counter_data_not_ranked(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        iface = _interface(session, device.id, "XGE1/0/1")
        cycle = PERIOD.start
        _sample(session, _cycle(session, device.id, cycle), iface.id, cycle)
        session.commit()

        deltas = weekly_counter_deltas(session, PERIOD)
        assert deltas[0].total_delta is None
        # total_delta is None = no rankable observation: it must not fill a
        # Top-10 slot (数据缺失 is not a value, §6.2) — the Top 10 stays empty.
        assert counter_delta_top_entries(session, PERIOD) == []


def test_top_entries_exclude_missing_data_but_keep_true_zero(db_engine: Engine) -> None:
    """Boundary: total None never ranks; a genuine 0 total is a real rank."""

    with Session(db_engine) as session:
        device = _device(session, "core-1")
        zero = _interface(session, device.id, "XGE1/0/1")  # constant counter
        silent = _interface(session, device.id, "XGE1/0/2")  # counters never reported
        for i in range(2):
            cycle = PERIOD.start + i * STEP
            run = _cycle(session, device.id, cycle)
            _sample(session, run, zero.id, cycle, crc=10)
            _sample(session, run, silent.id, cycle)
        session.commit()

        top = counter_delta_top_entries(session, PERIOD)
        assert [d.normalized_name for d in top] == ["xge1/0/1"]  # None-total excluded
        assert top[0].crc_delta == 0  # one valid interval, genuinely zero increment
        assert top[0].total_delta == 0


def test_top_entries_filled_only_by_interfaces_with_data(db_engine: Engine) -> None:
    """With fewer rankable interfaces than 10, None-total ones stay out."""

    with Session(db_engine) as session:
        device = _device(session, "core-1")
        with_data = _interface(session, device.id, "XGE1/0/1")
        without_data = _interface(session, device.id, "XGE1/0/2")
        for i in range(2):
            cycle = PERIOD.start + i * STEP
            run = _cycle(session, device.id, cycle)
            _sample(session, run, with_data.id, cycle, crc=100 + 10 * i)
            _sample(session, run, without_data.id, cycle)
        session.commit()

        top = counter_delta_top_entries(session, PERIOD)
        assert [d.normalized_name for d in top] == ["xge1/0/1"]
        assert top[0].total_delta == 10


def test_top_entries_ranked_by_total_delta(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        small = _interface(session, device.id, "XGE1/0/1")
        big = _interface(session, device.id, "XGE1/0/2")
        for i in range(2):
            cycle = PERIOD.start + i * STEP
            run = _cycle(session, device.id, cycle)
            _sample(session, run, small.id, cycle, crc=10 + i)
            _sample(session, run, big.id, cycle, crc=100 + 500 * i, in_errors=7 * i)
        session.commit()

        top = counter_delta_top_entries(session, PERIOD)
        assert [d.normalized_name for d in top] == ["xge1/0/2", "xge1/0/1"]
        assert top[0].total_delta == 507  # crc 500 + in_errors 7
        assert top[0].in_errors_delta == 7
        assert top[1].total_delta == 1  # crc 10 -> 11


def test_coverage_counts_statuses_over_full_week(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        # 2 SUCCESS, 1 PARTIAL, 1 FAILED inside the week; one outside.
        _cycle(session, device.id, PERIOD.start, "SUCCESS")
        _cycle(session, device.id, PERIOD.start + STEP, "SUCCESS")
        _cycle(session, device.id, PERIOD.start + 2 * STEP, "PARTIAL")
        _cycle(session, device.id, PERIOD.start + 3 * STEP, "FAILED")
        _cycle(session, device.id, PERIOD.end + STEP, "SUCCESS")  # next week
        session.commit()

        summary = weekly_coverage(session, PERIOD)
        assert len(summary.devices) == 1
        coverage = summary.devices[0]
        assert coverage.expected == 7 * 24 * 12  # §18.1 full week
        assert coverage.success == 2
        assert coverage.partial == 1
        assert coverage.failed == 1
        assert summary.partial == 1  # PARTIAL shown separately (§18.2)
        assert summary.coverage_percent == pytest.approx(3 / 2016 * 100)
        assert summary.below_target  # far below 95%


def test_coverage_below_target_marks_data_integrity_warning(db_engine: Engine) -> None:
    """§18.3: 99% overall is fine; one 94% device still trips the warning."""

    with Session(db_engine) as session:
        good = _device(session, "core-good")
        bad = _device(session, "core-bad")
        # good: SUCCESS for every planned cycle is 2016 rows — simulate a
        # near-perfect week with 2000 SUCCESS + 16 FAILED (>= 99%).
        for i in range(2000):
            _cycle(session, good.id, PERIOD.start + i * STEP, "SUCCESS")
        for i in range(2000, 2016):
            _cycle(session, good.id, PERIOD.start + i * STEP, "FAILED")
        # bad: 94% success+partial (1895 of 2016).
        for i in range(1800):
            _cycle(session, bad.id, PERIOD.start + i * STEP, "SUCCESS")
        for i in range(1800, 1895):
            _cycle(session, bad.id, PERIOD.start + i * STEP, "PARTIAL")
        session.commit()

        summary = weekly_coverage(session, PERIOD)
        by_name = {d.device_name: d for d in summary.devices}
        good_cov, bad_cov = by_name["core-good"], by_name["core-bad"]
        assert good_cov.coverage_percent == pytest.approx(2000 / 2016 * 100)
        assert good_cov.below_target is False
        assert bad_cov.coverage_percent == pytest.approx(1895 / 2016 * 100)
        assert bad_cov.below_target is True
        assert summary.below_target is True  # single-device breach is enough


def test_coverage_exactly_at_target_is_not_below(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        # 1916 of 2016 = 95.04% >= 95%.
        for i in range(1916):
            _cycle(session, device.id, PERIOD.start + i * STEP, "SUCCESS")
        session.commit()

        summary = weekly_coverage(session, PERIOD)
        assert summary.devices[0].coverage_percent == pytest.approx(1916 / 2016 * 100)
        assert summary.devices[0].below_target is False
        assert summary.below_target is False


def test_coverage_full_week_without_data(db_engine: Engine) -> None:
    """§6.2/§18: a silent week is 0% coverage (all planned cycles failed), shown."""

    with Session(db_engine) as session:
        _device(session, "silent")
        session.commit()

        summary = weekly_coverage(session, PERIOD)
        coverage = summary.devices[0]
        assert coverage.expected == 2016
        assert coverage.success == 0
        assert coverage.partial == 0
        assert coverage.failed == 0  # no rows at all — distinct from FAILED runs
        assert coverage.coverage_percent == 0.0
        assert summary.below_target


def test_coverage_disabled_device_has_no_planned_cycles(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        _device(session, "disabled", enabled=False)
        session.commit()

        summary = weekly_coverage(session, PERIOD)
        coverage = summary.devices[0]
        assert coverage.expected == 0
        assert coverage.coverage_percent is None
        assert coverage.below_target is False
