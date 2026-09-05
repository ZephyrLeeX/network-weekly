"""Priority-interface Down/Recovery state machine (W02-T006, §13).

Only `monitored = true` interfaces participate (§13).

Valid-sample rules (§13.1/§13.2/§13.3):

- a sample is *valid* only when oper state is exactly "up" or "down";
- 2 consecutive valid Down samples confirm interface DOWN;
- after DOWN, 2 consecutive valid Up samples confirm RECOVERED;
- missing, failed or undetermined samples (None, testing, unknown, dormant,
  notpresent, lowerlayerdown) never count as Up or Down — and, per §13.3,
  they do not participate in the consecutive count of *valid* samples, so
  they neither advance nor break a valid-sample run. (This is the §13
  reading for interfaces — in contrast to CPU/memory thresholds, where a
  missing sample explicitly breaks continuity, §14.)

`advance_interface_state` is the pure core; :func:`record_interface_states`
is the pipeline entry that walks one cycle's interface samples and applies
the machine to every monitored interface. Incidents are long-term records
(§24) — created or closed here, never deleted.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.collect.dto import InterfaceSample
from backend.db.models import (
    Interface,
    InterfaceMonitoringState,
    InterfaceStateIncident,
)

logger = logging.getLogger(__name__)

STATE_NORMAL = "normal"
STATE_DOWN = "down"

# §13.1/§13.2: two valid sampling periods.
CONFIRMATION_SAMPLES = 2


@dataclass(frozen=True)
class InterfaceTracking:
    """The persisted per-interface valid-sample tracking values."""

    state: str = STATE_NORMAL
    consecutive_down_samples: int = 0
    consecutive_up_samples: int = 0
    down_run_started_at: datetime | None = None
    up_run_started_at: datetime | None = None


@dataclass(frozen=True)
class InterfaceStateDecision:
    """What one valid sample changed, for persistence and logging."""

    tracking: InterfaceTracking
    incident_started_at: datetime | None = None
    incident_recovered_at: datetime | None = None


def advance_interface_state(
    tracking: InterfaceTracking, oper_state: str | None, collected_at: datetime
) -> InterfaceStateDecision:
    """Pure §13 transition for one sample (`oper_state` as collected).

    An invalid/missing sample (anything but "up"/"down") is a no-op: it must
    not count and must not break the current valid-sample run (§13.3).
    """

    if oper_state == "down":
        if tracking.state == STATE_DOWN:
            # Already Down: keep the open incident, break any Up run.
            return InterfaceStateDecision(
                tracking=InterfaceTracking(
                    state=tracking.state,
                    consecutive_down_samples=tracking.consecutive_down_samples,
                    consecutive_up_samples=0,
                    down_run_started_at=tracking.down_run_started_at,
                    up_run_started_at=None,
                )
            )

        count = tracking.consecutive_down_samples + 1
        run_started = (
            tracking.down_run_started_at if tracking.consecutive_down_samples > 0 else collected_at
        )
        if count >= CONFIRMATION_SAMPLES:
            return InterfaceStateDecision(
                tracking=InterfaceTracking(
                    state=STATE_DOWN,
                    consecutive_down_samples=count,
                    consecutive_up_samples=0,
                    down_run_started_at=run_started,
                    up_run_started_at=None,
                ),
                incident_started_at=run_started,
            )
        return InterfaceStateDecision(
            tracking=InterfaceTracking(
                state=tracking.state,
                consecutive_down_samples=count,
                consecutive_up_samples=0,
                down_run_started_at=run_started,
                up_run_started_at=None,
            )
        )

    if oper_state == "up":
        if tracking.state == STATE_NORMAL:
            # Normal and Up: reset any partial run.
            return InterfaceStateDecision(
                tracking=InterfaceTracking(
                    state=tracking.state,
                    consecutive_down_samples=0,
                    consecutive_up_samples=0,
                    down_run_started_at=None,
                    up_run_started_at=None,
                )
            )

        count = tracking.consecutive_up_samples + 1
        run_started = (
            tracking.up_run_started_at if tracking.consecutive_up_samples > 0 else collected_at
        )
        if count >= CONFIRMATION_SAMPLES:
            return InterfaceStateDecision(
                tracking=InterfaceTracking(
                    state=STATE_NORMAL,
                    consecutive_down_samples=0,
                    consecutive_up_samples=0,
                    down_run_started_at=None,
                    up_run_started_at=None,
                ),
                incident_recovered_at=run_started,
            )
        return InterfaceStateDecision(
            tracking=InterfaceTracking(
                state=tracking.state,
                consecutive_down_samples=0,
                consecutive_up_samples=count,
                down_run_started_at=None,
                up_run_started_at=run_started,
            )
        )

    # §13.3: missing / failed / undetermined — does not count, does not break.
    return InterfaceStateDecision(tracking=tracking)


def load_interface_tracking(session: Session, interface_id: int) -> InterfaceTracking:
    row = session.get(InterfaceMonitoringState, interface_id)
    if row is None:
        return InterfaceTracking()
    return InterfaceTracking(
        state=row.state,
        consecutive_down_samples=row.consecutive_down_samples,
        consecutive_up_samples=row.consecutive_up_samples,
        down_run_started_at=row.down_run_started_at,
        up_run_started_at=row.up_run_started_at,
    )


def _open_incident(session: Session, interface_id: int) -> InterfaceStateIncident | None:
    return session.execute(
        select(InterfaceStateIncident)
        .where(
            InterfaceStateIncident.interface_id == interface_id,
            InterfaceStateIncident.recovered_at.is_(None),
        )
        .order_by(InterfaceStateIncident.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def apply_interface_state(
    session: Session,
    interface: Interface,
    oper_state: str | None,
    collected_at: datetime,
) -> InterfaceStateDecision:
    """Persist one monitored interface's state transition (§13/§9.4-style)."""

    decision = advance_interface_state(
        load_interface_tracking(session, interface.id), oper_state, collected_at
    )
    tracking = decision.tracking

    row = session.get(InterfaceMonitoringState, interface.id)
    if row is None:
        row = InterfaceMonitoringState(interface_id=interface.id)
        session.add(row)
    row.state = tracking.state
    row.consecutive_down_samples = tracking.consecutive_down_samples
    row.consecutive_up_samples = tracking.consecutive_up_samples
    row.down_run_started_at = tracking.down_run_started_at
    row.up_run_started_at = tracking.up_run_started_at
    row.updated_at = collected_at

    if decision.incident_started_at is not None and _open_incident(session, interface.id) is None:
        session.add(
            InterfaceStateIncident(
                interface_id=interface.id, started_at=decision.incident_started_at
            )
        )
        logger.warning(
            "interface %s (device %d) confirmed DOWN at %s",
            interface.display_name,
            interface.device_id,
            decision.incident_started_at,
        )

    if decision.incident_recovered_at is not None:
        incident = _open_incident(session, interface.id)
        if incident is not None:
            incident.recovered_at = decision.incident_recovered_at
            logger.info(
                "interface %s (device %d) confirmed RECOVERED at %s",
                interface.display_name,
                interface.device_id,
                decision.incident_recovered_at,
            )

    return decision


def record_interface_states(
    session: Session, device_id: int, samples: list[InterfaceSample], collected_at: datetime
) -> int:
    """Pipeline entry: apply §13 to every *monitored* interface of the cycle.

    Returns how many monitored interfaces were processed. Interfaces that are
    not monitored are skipped entirely (§13) — no state rows are created for
    them. Called only when the interfaces section succeeded (samples exist);
    a failed section means no samples, which the machine never sees.
    """

    monitored = {
        row.normalized_name: row
        for row in session.execute(
            select(Interface).where(
                Interface.device_id == device_id, Interface.monitored.is_(True)
            )
        )
        .scalars()
        .all()
    }
    if not monitored:
        return 0

    processed = 0
    seen: set[int] = set()
    for sample in samples:
        interface = monitored.get(sample.normalized_name)
        if interface is None or interface.id in seen:
            continue
        seen.add(interface.id)
        apply_interface_state(session, interface, sample.oper_state, collected_at)
        processed += 1
    return processed
