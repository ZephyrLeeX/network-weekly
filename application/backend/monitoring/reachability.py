"""Device reachability state machine (W02-T004, SYSTEM_SPEC.md §9).

Per §9.1 a cycle is a *failed* cycle only when the SNMP management channel
yielded no valid data AND the one lightweight SSH confirmation also failed
(or was unavailable). SSH-reachable-but-SNMP-broken cycles keep the device
management-reachable — the missing SNMP data stays honestly missing in the
poll run and Coverage (§9.3).

Transitions (§9.2/§9.3):

- 2 consecutive failed cycles (consecutive = the previous planned cycle was
  actually processed; a gap resets the run) confirm DOWN;
- after DOWN, 2 consecutive management-reachable cycles confirm RECOVERED.

`advance_cycle_state` is the pure transition core (unit-tested without a
database); :func:`apply_device_reachability` persists it together with the
incident records the weekly report consumes (§9.4). Incidents are long-term
data (§24) and are only ever created or closed here — never deleted.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import DeviceMonitoringState, DeviceReachabilityIncident

logger = logging.getLogger(__name__)

STATE_NORMAL = "normal"
STATE_DOWN = "down"

# §9.2/§9.3: the two-cycle confirmation rules.
CONFIRMATION_CYCLES = 2


@dataclass(frozen=True)
class ReachabilityObservation:
    """One processed cycle's reachability evidence (§9.1)."""

    cycle_started_at: datetime
    # True when the SNMP channel produced any valid data this cycle.
    snmp_ok: bool
    # §9.1 probe result: True/False when probed, None when not probed.
    ssh_reachable: bool | None

    @property
    def management_reachable(self) -> bool:
        return self.snmp_ok or self.ssh_reachable is True


@dataclass(frozen=True)
class CycleTracking:
    """The persisted per-device cycle-tracking values."""

    state: str = STATE_NORMAL
    last_cycle_started_at: datetime | None = None
    consecutive_failed_cycles: int = 0
    consecutive_reachable_cycles: int = 0
    failed_run_started_at: datetime | None = None
    reachable_run_started_at: datetime | None = None


@dataclass(frozen=True)
class ReachabilityDecision:
    """What one observation changed, for persistence and logging."""

    tracking: CycleTracking
    # Set when this cycle confirmed DOWN (a new incident must be opened).
    incident_started_at: datetime | None = None
    # Set when this cycle confirmed RECOVERED (the open incident closes).
    incident_recovered_at: datetime | None = None


def _is_consecutive(tracking: CycleTracking, cycle: datetime, interval: timedelta) -> bool:
    """True when the previous planned cycle was also processed (no gap)."""

    last = tracking.last_cycle_started_at
    return last is not None and cycle - last == interval


def advance_cycle_state(
    tracking: CycleTracking,
    observation: ReachabilityObservation,
    *,
    interval: timedelta = timedelta(seconds=300),
) -> ReachabilityDecision:
    """Pure §9 transition for one cycle."""

    cycle = observation.cycle_started_at
    continuous = _is_consecutive(tracking, cycle, interval)
    state = tracking.state

    if observation.management_reachable:
        if state == STATE_DOWN:
            # §9.3: count consecutive reachable cycles toward Recovery.
            count = tracking.consecutive_reachable_cycles + 1 if continuous else 1
            run_started = (
                tracking.reachable_run_started_at if continuous and count > 1 else cycle
            )
            if count >= CONFIRMATION_CYCLES:
                tracking = CycleTracking(
                    state=STATE_NORMAL,
                    last_cycle_started_at=cycle,
                    consecutive_failed_cycles=0,
                    consecutive_reachable_cycles=0,
                    failed_run_started_at=None,
                    reachable_run_started_at=None,
                )
                return ReachabilityDecision(
                    tracking=tracking, incident_recovered_at=run_started
                )
            tracking = CycleTracking(
                state=state,
                last_cycle_started_at=cycle,
                consecutive_failed_cycles=0,
                consecutive_reachable_cycles=count,
                failed_run_started_at=None,
                reachable_run_started_at=run_started,
            )
            return ReachabilityDecision(tracking=tracking)

        # Reachable while normal: a failed run is interrupted.
        tracking = CycleTracking(
            state=state,
            last_cycle_started_at=cycle,
            consecutive_failed_cycles=0,
            consecutive_reachable_cycles=0,
            failed_run_started_at=None,
            reachable_run_started_at=None,
        )
        return ReachabilityDecision(tracking=tracking)

    # Failed cycle (§9.1.5).
    if state == STATE_DOWN:
        # The Down incident is already open; a failure keeps it open and
        # interrupts any partial Recovery run.
        tracking = CycleTracking(
            state=state,
            last_cycle_started_at=cycle,
            consecutive_failed_cycles=tracking.consecutive_failed_cycles,
            consecutive_reachable_cycles=0,
            failed_run_started_at=tracking.failed_run_started_at,
            reachable_run_started_at=None,
        )
        return ReachabilityDecision(tracking=tracking)

    # §9.2: count consecutive failed cycles toward Down.
    count = tracking.consecutive_failed_cycles + 1 if continuous else 1
    run_started = tracking.failed_run_started_at if continuous and count > 1 else cycle
    if count >= CONFIRMATION_CYCLES:
        tracking = CycleTracking(
            state=STATE_DOWN,
            last_cycle_started_at=cycle,
            consecutive_failed_cycles=count,
            consecutive_reachable_cycles=0,
            failed_run_started_at=run_started,
            reachable_run_started_at=None,
        )
        return ReachabilityDecision(tracking=tracking, incident_started_at=run_started)

    tracking = CycleTracking(
        state=state,
        last_cycle_started_at=cycle,
        consecutive_failed_cycles=count,
        consecutive_reachable_cycles=0,
        failed_run_started_at=run_started,
        reachable_run_started_at=None,
    )
    return ReachabilityDecision(tracking=tracking)


def load_tracking(session: Session, device_id: int) -> CycleTracking:
    """Current tracking values for a device (defaults for a fresh device)."""

    row = session.get(DeviceMonitoringState, device_id)
    if row is None:
        return CycleTracking()
    return CycleTracking(
        state=row.state,
        last_cycle_started_at=row.last_cycle_started_at,
        consecutive_failed_cycles=row.consecutive_failed_cycles,
        consecutive_reachable_cycles=row.consecutive_reachable_cycles,
        failed_run_started_at=row.failed_run_started_at,
        reachable_run_started_at=row.reachable_run_started_at,
    )


def _open_incident(
    session: Session, device_id: int
) -> DeviceReachabilityIncident | None:
    return session.execute(
        select(DeviceReachabilityIncident)
        .where(
            DeviceReachabilityIncident.device_id == device_id,
            DeviceReachabilityIncident.recovered_at.is_(None),
        )
        .order_by(DeviceReachabilityIncident.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def apply_device_reachability(
    session: Session,
    device_id: int,
    observation: ReachabilityObservation,
    *,
    interval: timedelta = timedelta(seconds=300),
) -> ReachabilityDecision:
    """Persist one cycle's reachability transition + incident records (§9.4)."""

    decision = advance_cycle_state(
        load_tracking(session, device_id), observation, interval=interval
    )
    tracking = decision.tracking

    row = session.get(DeviceMonitoringState, device_id)
    if row is None:
        row = DeviceMonitoringState(device_id=device_id)
        session.add(row)
    row.state = tracking.state
    row.last_cycle_started_at = tracking.last_cycle_started_at
    row.consecutive_failed_cycles = tracking.consecutive_failed_cycles
    row.consecutive_reachable_cycles = tracking.consecutive_reachable_cycles
    row.failed_run_started_at = tracking.failed_run_started_at
    row.reachable_run_started_at = tracking.reachable_run_started_at
    row.updated_at = observation.cycle_started_at

    if decision.incident_started_at is not None:
        if _open_incident(session, device_id) is None:
            session.add(
                DeviceReachabilityIncident(
                    device_id=device_id, started_at=decision.incident_started_at
                )
            )
            logger.warning(
                "device %d confirmed DOWN at %s",
                device_id,
                decision.incident_started_at,
            )

    if decision.incident_recovered_at is not None:
        incident = _open_incident(session, device_id)
        if incident is not None:
            incident.recovered_at = decision.incident_recovered_at
            logger.info(
                "device %d confirmed RECOVERED at %s",
                device_id,
                decision.incident_recovered_at,
            )

    return decision
