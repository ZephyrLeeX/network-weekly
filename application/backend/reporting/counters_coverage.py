"""Weekly CRC/Error/Drop deltas and Monitoring Coverage
(W03-T005, SYSTEM_SPEC.md §16/§18).

Counter deltas (§16): per interface, the increment of each cumulative error
counter over the statistics week. Reset-safe by construction — only
intervals whose counter moved forward by a non-negative amount contribute;
a reset (or an unreliable wrap, or a missing sample) rebaselines and
contributes nothing, so a fake huge increment is impossible (§16). An
interface whose counters never produce one valid interval has no delta for
that column — 数据缺失, never 0. These values are observation-only: they
never change the overall status (§19).

Coverage (§18): per logical device,

    expected = planned 5-minute DEVICE_POLL cycles in [start, end)
    coverage = (SUCCESS + PARTIAL) / expected * 100%

with expected taken from the planned-cycle grid (7*24*12 = 2016 for a full
week; a disabled device has no planned cycles). PARTIAL is always shown
separately so the percentage can never hide partial collection failures.
Below 95% the report states 数据完整性不足 — and still generates (§18.3).

Both statistics live ONLY here; the report service composes them.
"""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, case, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from backend.db.models import Device, DevicePollRun, Interface, InterfaceMetric
from backend.monitoring.thresholds import cycle_slots
from backend.reporting.period import ReportPeriod

# §18.3 warning threshold.
COVERAGE_TARGET_PERCENT = 95.0

STATUS_SUCCESS = "SUCCESS"
STATUS_PARTIAL = "PARTIAL"

# §16 counters (InterfaceMetric columns), CRC first.
CRC_FIELD = "fcs_errors"
COUNTER_FIELDS: tuple[str, ...] = (
    CRC_FIELD,
    "in_errors",
    "out_errors",
    "in_discards",
    "out_discards",
)


@dataclass(frozen=True)
class InterfaceCounterDelta:
    """One interface's weekly error-counter increments (§16).

    Each delta is None when the week produced no valid interval for that
    counter (no samples, counters never reported, or every interval was a
    reset) — the report renders 数据缺失, never 0 (§6.2).
    """

    interface_id: int
    device_id: int
    device_name: str
    display_name: str
    normalized_name: str
    description: str | None
    sample_count: int
    crc_delta: int | None
    in_errors_delta: int | None
    out_errors_delta: int | None
    in_discards_delta: int | None
    out_discards_delta: int | None

    def _deltas(self) -> tuple[int | None, ...]:
        return (
            self.crc_delta,
            self.in_errors_delta,
            self.out_errors_delta,
            self.in_discards_delta,
            self.out_discards_delta,
        )

    @property
    def total_delta(self) -> int | None:
        """Sum of the deltas that have data; None when none do (§16 ranking)."""

        present = [delta for delta in self._deltas() if delta is not None]
        if not present:
            return None
        return sum(present)


def _reset_safe_delta(
    column: InstrumentedAttribute[int] | ColumnElement[int],
    prev_column: InstrumentedAttribute[int] | ColumnElement[int],
) -> ColumnElement[int]:
    """sum() of forward-only increments; NULL when no valid interval exists.

    A negative difference (reset / unreliable wrap) or a missing side of the
    interval contributes nothing — the counter is rebaselined, §16.
    """

    return func.sum(
        case(
            (
                column.isnot(None)
                & prev_column.isnot(None)
                & (column >= prev_column),
                column - prev_column,
            )
        )
    )


def weekly_counter_deltas(session: Session, period: ReportPeriod) -> list[InterfaceCounterDelta]:
    """All interfaces' weekly error-counter deltas, total-descending (§16)."""

    weekly = (
        select(
            InterfaceMetric.interface_id.label("interface_id"),
            *[
                getattr(InterfaceMetric, field).label(field)
                for field in COUNTER_FIELDS
            ],
            *[
                func.lag(getattr(InterfaceMetric, field))
                .over(
                    partition_by=InterfaceMetric.interface_id,
                    order_by=(InterfaceMetric.collected_at, InterfaceMetric.id),
                )
                .label(f"prev_{field}")
                for field in COUNTER_FIELDS
            ],
        )
        .join(DevicePollRun, InterfaceMetric.poll_run_id == DevicePollRun.id)
        .where(
            DevicePollRun.cycle_started_at >= period.start,
            DevicePollRun.cycle_started_at < period.end,
        )
        .cte("weekly_counters")
    )

    rows = session.execute(
        select(
            Interface.id,
            Interface.device_id,
            Device.name,
            Interface.display_name,
            Interface.normalized_name,
            Interface.description,
            func.count().label("sample_count"),
            *[
                _reset_safe_delta(
                    getattr(weekly.c, field), getattr(weekly.c, f"prev_{field}")
                ).label(f"{field}_delta")
                for field in COUNTER_FIELDS
            ],
        )
        .select_from(
            weekly.join(Interface, weekly.c.interface_id == Interface.id).join(
                Device, Interface.device_id == Device.id
            )
        )
        .group_by(Interface.id, Device.name)
        .order_by(
            Device.name.asc(),
            Interface.normalized_name.asc(),
        )
    ).all()

    deltas = [
        InterfaceCounterDelta(
            interface_id=row[0],
            device_id=row[1],
            device_name=row[2],
            display_name=row[3],
            normalized_name=row[4],
            description=row[5],
            sample_count=int(row[6]),
            crc_delta=_int_or_none(row[7]),
            in_errors_delta=_int_or_none(row[8]),
            out_errors_delta=_int_or_none(row[9]),
            in_discards_delta=_int_or_none(row[10]),
            out_discards_delta=_int_or_none(row[11]),
        )
        for row in rows
    ]
    # Observation ranking (§16): total increments descending, deterministic
    # tie-break by device then interface name. Interfaces without any counter
    # data (total None) sort last and are never ranked as top incrementers.
    deltas.sort(
        key=lambda d: (
            -(d.total_delta if d.total_delta is not None else 0),
            d.device_name,
            d.normalized_name,
        )
    )
    return deltas


def _int_or_none(value: object) -> int | None:
    return None if value is None else int(value)  # type: ignore[call-overload]


def counter_delta_top_entries(
    session: Session, period: ReportPeriod, limit: int = 10
) -> list[InterfaceCounterDelta]:
    """§16: the Top-10 weekly incrementers (observation only)."""

    return weekly_counter_deltas(session, period)[:limit]


@dataclass(frozen=True)
class DeviceCoverage:
    """§18 counts for one logical device."""

    device_id: int
    device_name: str
    expected: int
    success: int
    partial: int
    failed: int

    @property
    def coverage_percent(self) -> float | None:
        """(SUCCESS + PARTIAL) / expected * 100; None with no planned cycles."""

        if self.expected == 0:
            return None
        return (self.success + self.partial) / self.expected * 100.0

    @property
    def below_target(self) -> bool:
        """True when coverage is computable and < 95% (§18.3)."""

        coverage = self.coverage_percent
        return coverage is not None and coverage < COVERAGE_TARGET_PERCENT


@dataclass(frozen=True)
class CoverageSummary:
    """§18 Coverage across all logical devices + the overall view."""

    devices: tuple[DeviceCoverage, ...]

    @property
    def expected(self) -> int:
        return sum(d.expected for d in self.devices)

    @property
    def success(self) -> int:
        return sum(d.success for d in self.devices)

    @property
    def partial(self) -> int:
        return sum(d.partial for d in self.devices)

    @property
    def failed(self) -> int:
        return sum(d.failed for d in self.devices)

    @property
    def coverage_percent(self) -> float | None:
        if self.expected == 0:
            return None
        return (self.success + self.partial) / self.expected * 100.0

    @property
    def below_target(self) -> bool:
        """§18.3: overall OR any single device below 95% → 数据完整性不足."""

        overall = self.coverage_percent
        overall_low = overall is not None and overall < COVERAGE_TARGET_PERCENT
        return overall_low or any(d.below_target for d in self.devices)


def weekly_coverage(session: Session, period: ReportPeriod) -> CoverageSummary:
    """§18 expected/success/partial/failed per device + overall summary.

    `expected` is the planned 5-minute cycle grid over the period; the
    scheduler plans cycles for enabled devices only, so a disabled device
    has expected = 0 (coverage not computable → 数据缺失, not 0%).
    """

    devices = session.execute(
        select(Device.id, Device.name, Device.enabled).order_by(Device.name.asc())
    ).all()

    status_counts: dict[tuple[int, str], int] = {
        (device_id, status): count
        for device_id, status, count in session.execute(
            select(DevicePollRun.device_id, DevicePollRun.status, func.count())
            .where(
                DevicePollRun.cycle_started_at >= period.start,
                DevicePollRun.cycle_started_at < period.end,
            )
            .group_by(DevicePollRun.device_id, DevicePollRun.status)
        ).all()
    }

    expected_per_device = len(cycle_slots(period.start, period.end))
    device_coverages = []
    for device_id, device_name, enabled in devices:
        success = status_counts.get((device_id, STATUS_SUCCESS), 0)
        partial = status_counts.get((device_id, STATUS_PARTIAL), 0)
        total = sum(count for (dev, _), count in status_counts.items() if dev == device_id)
        # Any status that is not SUCCESS/PARTIAL can only count as failure.
        device_coverages.append(
            DeviceCoverage(
                device_id=device_id,
                device_name=device_name,
                expected=expected_per_device if enabled else 0,
                success=success,
                partial=partial,
                failed=total - success - partial,
            )
        )
    return CoverageSummary(devices=tuple(device_coverages))
