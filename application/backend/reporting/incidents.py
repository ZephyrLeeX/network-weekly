"""Weekly device and priority-interface incident summaries
(W03-T003, SYSTEM_SPEC.md §9.4/§13/§19).

Reads ONLY the long-term incident records persisted by the Wave 2 state
machines (§24). An incident belongs to the week when its ``[started_at,
recovered_at)`` interval intersects the half-open period — including
incidents that started in an earlier week (recovered inside the week or
still open) and incidents whose recovery lands exactly at the period end
(the whole week was down; the recovery belongs to the next week).

Ongoing vs recovered (§19): an episode is *ongoing at period end* when it
had not recovered by ``period.end`` — ``recovered_at`` is NULL (never
recovered at data-read time) or >= ``period.end`` (recovered after the
week ended). Ongoing device/interface episodes are the 异常 condition;
episodes recovered inside the week are the 关注 condition.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import (
    Device,
    DeviceReachabilityIncident,
    Interface,
    InterfaceStateIncident,
)
from backend.reporting.period import ReportPeriod


@dataclass(frozen=True)
class IncidentEpisode:
    """One confirmed Down episode as presented for the report week (§9.4).

    ``duration_seconds`` is the full observed duration to recovery — an
    episode that started before the week therefore reports its whole
    length, not just the in-week part. None while not recovered.
    """

    started_at: datetime
    recovered_at: datetime | None
    # True when the episode was already down when the week began (跨周).
    started_before_period: bool
    # True when recovery happened, but only after the period ended.
    recovered_after_period_end: bool

    @property
    def duration_seconds(self) -> float | None:
        if self.recovered_at is None:
            return None
        return (self.recovered_at - self.started_at).total_seconds()

    @property
    def ongoing_at_period_end(self) -> bool:
        """§19 异常 evidence: still confirmed Down when the week ended."""

        return self.recovered_at is None or self.recovered_after_period_end


@dataclass(frozen=True)
class DeviceIncidentSummary:
    """Weekly Down/Recovery summary for one logical device (§9.4)."""

    device_id: int
    device_name: str
    episodes: tuple[IncidentEpisode, ...]

    @property
    def down_count(self) -> int:
        return len(self.episodes)

    @property
    def ongoing_episodes(self) -> tuple[IncidentEpisode, ...]:
        return tuple(e for e in self.episodes if e.ongoing_at_period_end)

    @property
    def has_any_episode(self) -> bool:
        return bool(self.episodes)


@dataclass(frozen=True)
class InterfaceIncidentSummary:
    """Weekly Down/Recovery summary for one monitored interface (§13)."""

    interface_id: int
    device_id: int
    device_name: str
    display_name: str
    normalized_name: str
    description: str | None
    episodes: tuple[IncidentEpisode, ...]

    @property
    def down_count(self) -> int:
        return len(self.episodes)

    @property
    def ongoing_episodes(self) -> tuple[IncidentEpisode, ...]:
        return tuple(e for e in self.episodes if e.ongoing_at_period_end)


def _episode(
    started_at: datetime, recovered_at: datetime | None, period: ReportPeriod
) -> IncidentEpisode:
    return IncidentEpisode(
        started_at=started_at,
        recovered_at=recovered_at,
        started_before_period=started_at < period.start,
        recovered_after_period_end=recovered_at is not None and recovered_at >= period.end,
    )


def weekly_device_incidents(session: Session, period: ReportPeriod) -> list[DeviceIncidentSummary]:
    """Per-device weekly Down episodes from the persisted §9.4 records.

    Devices with no incident in the window are omitted — the report renders
    "本周无设备掉线" when the result is empty; absence of rows is absence of
    incidents, not missing data.
    """

    rows = session.execute(
        select(
            DeviceReachabilityIncident.device_id,
            Device.name,
            DeviceReachabilityIncident.started_at,
            DeviceReachabilityIncident.recovered_at,
        )
        .join(Device, DeviceReachabilityIncident.device_id == Device.id)
        .where(
            DeviceReachabilityIncident.started_at < period.end,
            DeviceReachabilityIncident.recovered_at.is_(None)
            | (DeviceReachabilityIncident.recovered_at > period.start),
        )
        .order_by(Device.name.asc(), DeviceReachabilityIncident.started_at.asc())
    ).all()

    summaries: dict[int, DeviceIncidentSummary] = {}
    for device_id, device_name, started_at, recovered_at in rows:
        existing = summaries.get(device_id)
        episodes = (
            existing.episodes if existing is not None else ()
        ) + (_episode(started_at, recovered_at, period),)
        summaries[device_id] = DeviceIncidentSummary(
            device_id=device_id, device_name=device_name, episodes=episodes
        )
    return list(summaries.values())


def weekly_interface_incidents(
    session: Session, period: ReportPeriod
) -> list[InterfaceIncidentSummary]:
    """Per-monitored-interface weekly Down episodes (§13 records only)."""

    rows = session.execute(
        select(
            InterfaceStateIncident.interface_id,
            Interface.device_id,
            Device.name,
            Interface.display_name,
            Interface.normalized_name,
            Interface.description,
            InterfaceStateIncident.started_at,
            InterfaceStateIncident.recovered_at,
        )
        .join(Interface, InterfaceStateIncident.interface_id == Interface.id)
        .join(Device, Interface.device_id == Device.id)
        .where(
            InterfaceStateIncident.started_at < period.end,
            InterfaceStateIncident.recovered_at.is_(None)
            | (InterfaceStateIncident.recovered_at > period.start),
        )
        .order_by(
            Device.name.asc(),
            Interface.normalized_name.asc(),
            InterfaceStateIncident.started_at.asc(),
        )
    ).all()

    summaries: dict[int, InterfaceIncidentSummary] = {}
    for (
        interface_id,
        device_id,
        device_name,
        display_name,
        normalized_name,
        description,
        started_at,
        recovered_at,
    ) in rows:
        existing = summaries.get(interface_id)
        episodes = (
            existing.episodes if existing is not None else ()
        ) + (_episode(started_at, recovered_at, period),)
        summaries[interface_id] = InterfaceIncidentSummary(
            interface_id=interface_id,
            device_id=device_id,
            device_name=device_name,
            display_name=display_name,
            normalized_name=normalized_name,
            description=description,
            episodes=episodes,
        )
    return list(summaries.values())
