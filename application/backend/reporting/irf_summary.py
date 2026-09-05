"""Weekly IRF member summary (W03-T004, SYSTEM_SPEC.md §17).

Reads the long-term `irf_member_observations` history persisted by the
W02-T008 ~15-minute loop. Because a *failed* observation records nothing,
any in-week row means the device itself reported the member state — member
absence is visible even when the logical management IP stayed reachable
(§17).

Semantics:

- A *missing window* is a run of consecutive in-week observations with
  ``observed=false``: it opens at the first missing observation and closes
  at the first later observation that sees the member again ( reappearance
  time). A window still open at the end of the in-week data means the
  member's latest known in-week state is missing.
- ``missing_at_period_end`` follows the latest in-week observation only —
  a reappearance after the period belongs to the next week (§19 evaluates
  the state AT period end).
- A week with NO successful observation has no evidence at all: the section
  reports 数据缺失 and must NOT be read as "members missing" (§6.2 — missing
  data is never interpreted as a value; §19's IRF condition needs evidence).

Standalone devices (expected members <= 1) are never part of the IRF
section (§17).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Device, DeviceMember, IrfMemberObservation
from backend.reporting.period import ReportPeriod


@dataclass(frozen=True)
class IrfMissingWindow:
    """One in-week run of observations that did not see the member (§17)."""

    started_at: datetime
    # First observation that saw the member again; None = still missing
    # through the end of the in-week data.
    reappeared_at: datetime | None


@dataclass(frozen=True)
class IrfRoleChange:
    """A reliably identified role change (both roles known, W02-T008)."""

    member_id: int
    observed_at: datetime
    previous_role: str
    new_role: str


@dataclass(frozen=True)
class IrfMemberWeeklySummary:
    """One member's week: observation count, missing windows, latest state."""

    member_id: int
    observations_count: int
    missing_windows: tuple[IrfMissingWindow, ...]
    # Latest in-week observation: True/False, or None with no in-week data.
    latest_observed: bool | None
    latest_role: str | None

    @property
    def missing_at_period_end(self) -> bool:
        """§19 异常 evidence: the member's latest in-week state is missing."""

        return self.latest_observed is False


@dataclass(frozen=True)
class IrfDeviceWeeklySummary:
    """Weekly IRF summary for one fabric (§17)."""

    device_id: int
    device_name: str
    expected_member_count: int
    # Members reported present by the latest in-week observation; None when
    # the week had no successful observation.
    observed_member_count: int | None
    latest_observation_at: datetime | None
    members: tuple[IrfMemberWeeklySummary, ...]
    role_changes: tuple[IrfRoleChange, ...]

    @property
    def data_missing(self) -> bool:
        """No successful observation in the week — 数据缺失, not member loss."""

        return self.latest_observation_at is None

    @property
    def current_missing_members(self) -> tuple[int, ...]:
        return tuple(m.member_id for m in self.members if m.missing_at_period_end)

    @property
    def any_member_missing_at_period_end(self) -> bool:
        """§19 异常 condition: an IRF member still missing at period end."""

        return any(m.missing_at_period_end for m in self.members)

    @property
    def had_missing_member_during_week(self) -> bool:
        """§19 关注 condition: any in-week missing window (now recovered)."""

        return any(m.missing_windows for m in self.members)


def _irf_device_ids(session: Session) -> list[tuple[int, str, int]]:
    """Configured IRF fabrics only: expected member count > 1 (§17)."""

    rows = session.execute(
        select(Device.id, Device.name, Device.expected_irf_member_count)
        .where(Device.expected_irf_member_count.is_not(None))
        .order_by(Device.name.asc())
    ).all()
    return [
        (device_id, name, expected)
        for device_id, name, expected in rows
        if expected is not None and expected > 1
    ]


def weekly_irf_summaries(session: Session, period: ReportPeriod) -> list[IrfDeviceWeeklySummary]:
    """Weekly IRF summaries for every configured fabric, name-ordered (§17)."""

    fabrics = _irf_device_ids(session)
    if not fabrics:
        return []

    rows = session.execute(
        select(
            IrfMemberObservation.device_id,
            IrfMemberObservation.member_id,
            IrfMemberObservation.observed,
            IrfMemberObservation.role,
            IrfMemberObservation.previous_role,
            IrfMemberObservation.role_changed,
            IrfMemberObservation.observed_at,
        )
        .where(
            IrfMemberObservation.device_id.in_([f[0] for f in fabrics]),
            IrfMemberObservation.observed_at >= period.start,
            IrfMemberObservation.observed_at < period.end,
        )
        .order_by(
            IrfMemberObservation.device_id,
            IrfMemberObservation.member_id,
            IrfMemberObservation.observed_at,
            IrfMemberObservation.id,
        )
    ).all()

    observations: dict[int, list[tuple[int, bool, str | None, str | None, bool, datetime]]] = {}
    for device_id, member_id, observed, role, previous_role, role_changed, observed_at in rows:
        observations.setdefault(device_id, []).append(
            (member_id, observed, role, previous_role, role_changed, observed_at)
        )

    summaries: list[IrfDeviceWeeklySummary] = []
    for device_id, device_name, expected in fabrics:
        device_rows = observations.get(device_id, [])

        member_ids = set(
            session.execute(
                select(DeviceMember.member_id).where(DeviceMember.device_id == device_id)
            ).scalars()
        )
        member_ids.update(row[0] for row in device_rows)

        member_summaries = tuple(
            _member_summary(member_id, device_rows) for member_id in sorted(member_ids)
        )
        latest_at = max((row[5] for row in device_rows), default=None)
        observed_count = (
            sum(1 for row in device_rows if row[1] and row[5] == latest_at)
            if latest_at is not None
            else None
        )
        role_changes = tuple(
            IrfRoleChange(
                member_id=row[0],
                observed_at=row[5],
                previous_role=row[3] or "",
                new_role=row[2] or "",
            )
            for row in device_rows
            if row[4] and row[3] is not None and row[2] is not None
        )

        summaries.append(
            IrfDeviceWeeklySummary(
                device_id=device_id,
                device_name=device_name,
                expected_member_count=expected,
                observed_member_count=observed_count,
                latest_observation_at=latest_at,
                members=member_summaries,
                role_changes=role_changes,
            )
        )
    return summaries


def _member_summary(
    member_id: int,
    device_rows: list[tuple[int, bool, str | None, str | None, bool, datetime]],
) -> IrfMemberWeeklySummary:
    """Fold one member's chronological in-week observations (§17)."""

    member_rows = [row for row in device_rows if row[0] == member_id]
    if not member_rows:
        return IrfMemberWeeklySummary(
            member_id=member_id,
            observations_count=0,
            missing_windows=(),
            latest_observed=None,
            latest_role=None,
        )

    windows: list[IrfMissingWindow] = []
    open_window_start: datetime | None = None
    for _, observed, _role, _previous, _changed, observed_at in member_rows:
        if observed:
            if open_window_start is not None:
                windows.append(
                    IrfMissingWindow(started_at=open_window_start, reappeared_at=observed_at)
                )
                open_window_start = None
        elif open_window_start is None:
            open_window_start = observed_at
    if open_window_start is not None:
        windows.append(IrfMissingWindow(started_at=open_window_start, reappeared_at=None))

    latest = member_rows[-1]
    return IrfMemberWeeklySummary(
        member_id=member_id,
        observations_count=len(member_rows),
        missing_windows=tuple(windows),
        latest_observed=latest[1],
        latest_role=latest[2],
    )
