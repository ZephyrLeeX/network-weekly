"""WeeklyStatisticsService: all report-ready values and the overall status
(W03-T006, SYSTEM_SPEC.md §19/§6).

One service, one pass over the persisted data, one statistical
implementation per value kind (the W03-T002..T005 modules; §15.4):

    persisted Wave 2 data
      -> period (reporting.period)
      -> CPU/memory avg/max/P95 + sustained-high (reporting.resources +
         monitoring.thresholds, the W02-T007 implementation)
      -> interface Top 10 (reporting.resources)
      -> device / priority-interface incidents (reporting.incidents)
      -> IRF weekly summary (reporting.irf_summary)
      -> CRC/Error/Drop deltas + Coverage (reporting.counters_coverage)
      -> deterministic overall status + summary text

Overall status (§19 — fixed rules, deterministic):

    异常: at period end, any device still confirmed Down, any priority
          interface still confirmed Down, or any IRF member still missing.
    关注: no 异常 condition, but any recovered device/interface Down, CPU
          or memory sustained-high, priority-interface high utilization,
          or Coverage < 95% (single device or overall).
    正常: none of the above.

CRC/Error/Drop observations NEVER change the status (§19). Missing data
stays explicit (None → 数据缺失 in the renderer, §6.2) and never creates a
condition by itself — except Coverage, where planned-but-missing cycles
honestly lower the percentage (§18). The summary text is a deterministic
template — no external service, no randomness (§19).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Device, Interface
from backend.monitoring.sustained import SustainedHighInterval
from backend.monitoring.thresholds import (
    detect_device_sustained_high,
    detect_interface_high_utilization,
    load_thresholds,
)
from backend.reporting.counters_coverage import (
    CoverageSummary,
    InterfaceCounterDelta,
    counter_delta_top_entries,
    weekly_coverage,
)
from backend.reporting.incidents import (
    DeviceIncidentSummary,
    InterfaceIncidentSummary,
    weekly_device_incidents,
    weekly_interface_incidents,
)
from backend.reporting.irf_summary import IrfDeviceWeeklySummary, weekly_irf_summaries
from backend.reporting.period import ReportPeriod
from backend.reporting.resources import (
    InterfaceTopEntry,
    MetricStats,
    device_resource_statistics,
    interface_top_entries,
)

logger = logging.getLogger(__name__)

STATUS_NORMAL = "正常"
STATUS_ATTENTION = "关注"
STATUS_ABNORMAL = "异常"

MISSING_TEXT = "数据缺失"


@dataclass(frozen=True)
class HighUtilizationEntry:
    """One priority interface's sustained high-utilization intervals (§15.3)."""

    device_name: str
    display_name: str
    normalized_name: str
    description: str | None
    intervals: tuple[SustainedHighInterval, ...]


@dataclass(frozen=True)
class DeviceResourceReport:
    """§14 report row for one logical device: stats + sustained intervals.

    `cpu`/`memory` are None when the week has no valid sample (数据缺失);
    the sustained-high intervals carry start/end/duration from the shared
    W02-T006/T007 implementation (§14: 区间数量、开始时间、结束时间、持续时间).
    """

    device_id: int
    device_name: str
    cpu: MetricStats | None
    memory: MetricStats | None
    cpu_sustained_high: tuple[SustainedHighInterval, ...]
    memory_sustained_high: tuple[SustainedHighInterval, ...]


@dataclass(frozen=True)
class WeeklyReportData:
    """Everything the DOCX renderer needs, all values report-ready (§6)."""

    period: ReportPeriod
    generated_at: datetime
    timezone: ZoneInfo
    overall_status: str
    summary_text: str
    coverage: CoverageSummary
    device_resources: tuple[DeviceResourceReport, ...]
    interface_top10: tuple[InterfaceTopEntry, ...]
    device_incidents: tuple[DeviceIncidentSummary, ...]
    interface_incidents: tuple[InterfaceIncidentSummary, ...]
    irf_summaries: tuple[IrfDeviceWeeklySummary, ...]
    counter_top10: tuple[InterfaceCounterDelta, ...]
    high_utilization: tuple[HighUtilizationEntry, ...]


def build_weekly_report_data(
    session: Session,
    period: ReportPeriod,
    *,
    generated_at: datetime | None = None,
) -> WeeklyReportData:
    """Compose every §6 report value for one period from persisted data (§27.10).

    Reads only persisted monitoring data — no device is ever contacted
    during report generation (AGENTS.md rule 8).
    """

    generated_at = generated_at or datetime.now(UTC)

    coverage = weekly_coverage(session, period)
    device_incidents = weekly_device_incidents(session, period)
    interface_incidents = weekly_interface_incidents(session, period)
    irf_summaries = weekly_irf_summaries(session, period)
    counter_top10 = counter_delta_top_entries(session, period)
    interface_top10 = interface_top_entries(session, period)

    device_resources: list[DeviceResourceReport] = []
    cpu_sustained = 0
    memory_sustained = 0
    thresholds = load_thresholds(session)
    for device_id, device_name in session.execute(
        select(Device.id, Device.name).order_by(Device.name.asc())
    ).all():
        stats = device_resource_statistics(session, device_id, device_name, period)
        sustained = detect_device_sustained_high(
            session, device_id, period.start, period.end, thresholds=thresholds
        )
        if sustained["cpu"]:
            cpu_sustained += 1
        if sustained["memory"]:
            memory_sustained += 1
        device_resources.append(
            DeviceResourceReport(
                device_id=device_id,
                device_name=device_name,
                cpu=stats.cpu,
                memory=stats.memory,
                cpu_sustained_high=tuple(sustained["cpu"]),
                memory_sustained_high=tuple(sustained["memory"]),
            )
        )

    high_utilization = _high_utilization_entries(session, period, thresholds)

    overall_status = _overall_status(
        coverage=coverage,
        device_incidents=device_incidents,
        interface_incidents=interface_incidents,
        irf_summaries=irf_summaries,
        cpu_sustained=cpu_sustained,
        memory_sustained=memory_sustained,
        high_utilization=high_utilization,
    )
    summary_text = _summary_text(
        period=period,
        status=overall_status,
        coverage=coverage,
        device_incidents=device_incidents,
        interface_incidents=interface_incidents,
        irf_summaries=irf_summaries,
        cpu_sustained=cpu_sustained,
        memory_sustained=memory_sustained,
        high_utilization=high_utilization,
    )

    start_tzinfo = period.start.tzinfo
    report_tz = start_tzinfo if isinstance(start_tzinfo, ZoneInfo) else ZoneInfo("UTC")
    return WeeklyReportData(
        period=period,
        generated_at=generated_at,
        timezone=report_tz,
        overall_status=overall_status,
        summary_text=summary_text,
        coverage=coverage,
        device_resources=tuple(device_resources),
        interface_top10=tuple(interface_top10),
        device_incidents=tuple(device_incidents),
        interface_incidents=tuple(interface_incidents),
        irf_summaries=tuple(irf_summaries),
        counter_top10=tuple(counter_top10),
        high_utilization=tuple(high_utilization),
    )


def _high_utilization_entries(
    session: Session, period: ReportPeriod, thresholds: dict[str, float]
) -> list[HighUtilizationEntry]:
    """Sustained high-utilization intervals for monitored interfaces (§15.3)."""

    entries: list[HighUtilizationEntry] = []
    monitored = session.execute(
        select(Interface, Device.name)
        .join(Device, Interface.device_id == Device.id)
        .where(Interface.monitored.is_(True))
        .order_by(Device.name.asc(), Interface.normalized_name.asc())
    ).all()
    for interface, device_name in monitored:
        intervals = detect_interface_high_utilization(
            session, interface.id, period.start, period.end, thresholds=thresholds
        )
        if intervals:
            entries.append(
                HighUtilizationEntry(
                    device_name=device_name,
                    display_name=interface.display_name,
                    normalized_name=interface.normalized_name,
                    description=interface.description,
                    intervals=tuple(intervals),
                )
            )
    return entries


def _overall_status(
    *,
    coverage: CoverageSummary,
    device_incidents: list[DeviceIncidentSummary],
    interface_incidents: list[InterfaceIncidentSummary],
    irf_summaries: list[IrfDeviceWeeklySummary],
    cpu_sustained: int,
    memory_sustained: int,
    high_utilization: list[HighUtilizationEntry],
) -> str:
    """The fixed §19 rules, evaluated in deterministic order."""

    # 异常: anything still down / missing at period end (§19).
    if any(d.ongoing_episodes for d in device_incidents):
        return STATUS_ABNORMAL
    if any(i.ongoing_episodes for i in interface_incidents):
        return STATUS_ABNORMAL
    if any(s.any_member_missing_at_period_end for s in irf_summaries):
        return STATUS_ABNORMAL

    # 关注: recovered episodes, sustained conditions, coverage breaches.
    attention = (
        any(d.episodes for d in device_incidents)
        or any(i.episodes for i in interface_incidents)
        or any(s.had_missing_member_during_week for s in irf_summaries)
        or cpu_sustained > 0
        or memory_sustained > 0
        or bool(high_utilization)
        or coverage.below_target
    )
    return STATUS_ATTENTION if attention else STATUS_NORMAL


def _summary_text(
    *,
    period: ReportPeriod,
    status: str,
    coverage: CoverageSummary,
    device_incidents: list[DeviceIncidentSummary],
    interface_incidents: list[InterfaceIncidentSummary],
    irf_summaries: list[IrfDeviceWeeklySummary],
    cpu_sustained: int,
    memory_sustained: int,
    high_utilization: list[HighUtilizationEntry],
) -> str:
    """Deterministic Chinese summary (§19: 确定性文字模板, no randomness)."""

    device_downs = sum(d.down_count for d in device_incidents)
    device_open = sum(len(d.ongoing_episodes) for d in device_incidents)
    interface_downs = sum(i.down_count for i in interface_incidents)
    interface_open = sum(len(i.ongoing_episodes) for i in interface_incidents)

    overall_coverage = coverage.coverage_percent
    coverage_text = (
        f"{overall_coverage:.2f}%" if overall_coverage is not None else MISSING_TEXT
    )

    clauses = [
        f"Monitoring Coverage {coverage_text}",
        "数据完整性不足" if coverage.below_target else "数据完整性满足要求",
        f"设备掉线 {device_downs} 次"
        + (f"（{device_open} 次期末仍未恢复）" if device_open else ""),
        f"重点接口中断 {interface_downs} 次"
        + (f"（{interface_open} 次期末仍未恢复）" if interface_open else ""),
    ]

    missing_irf = [
        (s.device_name, s.current_missing_members)
        for s in irf_summaries
        if s.current_missing_members
    ]
    if missing_irf:
        detail = "；".join(
            f"{name} 成员 {'、'.join(str(m) for m in members)}" for name, members in missing_irf
        )
        clauses.append(f"IRF 成员期末缺失：{detail}")
    else:
        clauses.append("IRF 成员无缺失")

    clauses.append(f"CPU 持续高负载 {cpu_sustained} 台")
    clauses.append(f"内存持续高负载 {memory_sustained} 台")
    clauses.append(f"重点接口持续高利用率 {len(high_utilization)} 个")

    return f"{period.week_code} 周报总体状态：{status}。" + "；".join(clauses) + "。"
