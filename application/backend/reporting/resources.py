"""Weekly CPU/memory statistics and interface utilization Top 10
(W03-T002, SYSTEM_SPEC.md §14/§15.4).

This module is THE single statistical implementation for weekly report
values of these kinds (§15.4: 所有页面和报告使用同一统计实现) — the
WeeklyStatisticsService composes it, nothing else recomputes it.

- Average / Maximum / P95 per logical device over the period, with the P95
  computed by PostgreSQL ``percentile_cont(0.95)`` (§14) — never in Python.
- Interface Top 10 (§15.4): candidates are interfaces with valid weekly
  utilization samples (a sample only exists when the interval had an
  effective speed, §15.2); each sample contributes the unified
  ``max(ingress, egress)`` value (:func:`backend.monitoring.sustained.max_direction_utilization`,
  mirrored 1:1 in the SQL expression below); the interface's P95 over those
  samples ranks the Top 10, descending.

Windows filter on the *planned* cycle time (`device_poll_runs.cycle_started_at`),
the same grid every other weekly consumer uses. Missing data stays None —
it is never replaced by 0 (§6.2).
"""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, case, desc, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from backend.db.models import Device, DeviceMetric, DevicePollRun, Interface, InterfaceMetric
from backend.reporting.period import ReportPeriod

CPU_FIELD = "cpu_usage_percent"
MEMORY_FIELD = "memory_usage_percent"

_P95 = 0.95


@dataclass(frozen=True)
class MetricStats:
    """Average / maximum / P95 over the valid weekly samples (§14)."""

    average: float
    maximum: float
    p95: float
    sample_count: int


@dataclass(frozen=True)
class DeviceResourceStats:
    """Weekly CPU and memory statistics for one logical device (§14).

    `cpu`/`memory` are None when the device has no valid sample of that kind
    in the period — the report renders 数据缺失, never 0 (§6.2).
    """

    device_id: int
    device_name: str
    cpu: MetricStats | None
    memory: MetricStats | None


@dataclass(frozen=True)
class InterfaceTopEntry:
    """One Top-10 row: an interface's weekly unified-utilization P95 (§15.4)."""

    interface_id: int
    device_id: int
    device_name: str
    display_name: str
    normalized_name: str
    description: str | None
    sample_count: int
    average: float
    maximum: float
    p95: float


def _max_direction_sql(
    in_column: ColumnElement[float | None] | InstrumentedAttribute[float | None],
    out_column: ColumnElement[float | None] | InstrumentedAttribute[float | None],
) -> ColumnElement[float]:
    """SQL mirror of ``monitoring.sustained.max_direction_utilization``.

    ``in`` NULL -> ``out``; ``out`` NULL -> ``in``; both present -> greatest;
    (both NULL rows are filtered out before aggregation). Any change to the
    Python function must be mirrored here — and vice versa.
    """

    return case(
        (in_column.is_(None), out_column),
        (out_column.is_(None), in_column),
        else_=func.greatest(in_column, out_column),
    )


def metric_statistics(
    session: Session, device_id: int, period: ReportPeriod, field: str
) -> MetricStats | None:
    """Avg/max/P95 of one device metric column over the period, or None.

    `field` is ``cpu_usage_percent`` or ``memory_usage_percent``. The P95 is
    PostgreSQL ``percentile_cont(0.95)`` over the valid samples (§14).
    """

    if field not in (CPU_FIELD, MEMORY_FIELD):
        raise ValueError(f"unknown device metric field: {field!r}")
    column = getattr(DeviceMetric, field)
    average, maximum, p95, sample_count = session.execute(
        select(
            func.avg(column),
            func.max(column),
            func.percentile_cont(_P95).within_group(column),
            func.count(column),
        )
        .select_from(DeviceMetric)
        .join(DevicePollRun, DeviceMetric.poll_run_id == DevicePollRun.id)
        .where(
            DeviceMetric.device_id == device_id,
            DevicePollRun.cycle_started_at >= period.start,
            DevicePollRun.cycle_started_at < period.end,
        )
    ).one()
    if not sample_count:
        return None
    return MetricStats(
        average=float(average),
        maximum=float(maximum),
        p95=float(p95),
        sample_count=int(sample_count),
    )


def device_resource_statistics(
    session: Session, device_id: int, device_name: str, period: ReportPeriod
) -> DeviceResourceStats:
    """CPU + memory weekly statistics for one device (§14)."""

    return DeviceResourceStats(
        device_id=device_id,
        device_name=device_name,
        cpu=metric_statistics(session, device_id, period, CPU_FIELD),
        memory=metric_statistics(session, device_id, period, MEMORY_FIELD),
    )


def interface_top_entries(
    session: Session, period: ReportPeriod, limit: int = 10
) -> list[InterfaceTopEntry]:
    """The §15.4 Top 10 by per-interface P95 of the unified sample value.

    Ranking: P95 descending; ties broken by device name then normalized
    interface name so the report is deterministic.
    """

    value = _max_direction_sql(
        InterfaceMetric.in_utilization_percent, InterfaceMetric.out_utilization_percent
    )
    p95 = func.percentile_cont(_P95).within_group(value)
    rows = session.execute(
        select(
            Interface.id,
            Interface.device_id,
            Device.name,
            Interface.display_name,
            Interface.normalized_name,
            Interface.description,
            func.count(value),
            func.avg(value),
            func.max(value),
            p95,
        )
        .select_from(InterfaceMetric)
        .join(Interface, InterfaceMetric.interface_id == Interface.id)
        .join(Device, Interface.device_id == Device.id)
        .join(DevicePollRun, InterfaceMetric.poll_run_id == DevicePollRun.id)
        .where(
            DevicePollRun.cycle_started_at >= period.start,
            DevicePollRun.cycle_started_at < period.end,
            or_(
                InterfaceMetric.in_utilization_percent.isnot(None),
                InterfaceMetric.out_utilization_percent.isnot(None),
            ),
        )
        .group_by(Interface.id, Device.name)
        .order_by(desc(p95), Device.name.asc(), Interface.normalized_name.asc())
        .limit(limit)
    ).all()
    return [
        InterfaceTopEntry(
            interface_id=row[0],
            device_id=row[1],
            device_name=row[2],
            display_name=row[3],
            normalized_name=row[4],
            description=row[5],
            sample_count=int(row[6]),
            average=float(row[7]),
            maximum=float(row[8]),
            p95=float(row[9]),
        )
        for row in rows
    ]
