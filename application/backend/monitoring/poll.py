"""One DEVICE_POLL of one device: collect, persist, in one transaction
(W02-T002).

The collection itself is the Wave 1 orchestration
(:func:`backend.collect.session.run_collection`) re-used unchanged — Wave 2
adds the scheduling around it and the Wave 2 persistence (topology sync +
poll run + raw metrics in a single transaction, so a crash can never leave a
half-persisted cycle).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import sessionmaker

from backend.collect.persist import persist_collection
from backend.collect.session import run_collection
from backend.collect.snmp import SnmpConfig
from backend.collect.ssh import SshConfig
from backend.db.engine import get_session_factory
from backend.monitoring.interface_state import record_interface_states
from backend.monitoring.pipeline import persist_poll_result
from backend.monitoring.reachability import ReachabilityObservation, apply_device_reachability

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DevicePollContext:
    """Everything needed to poll one device this cycle."""

    device_id: int
    device_name: str
    snmp: SnmpConfig
    ssh: SshConfig | None = None


def poll_device(
    context: DevicePollContext,
    cycle_started_at: datetime,
    *,
    session_factory: sessionmaker | None = None,
    now: datetime | None = None,
) -> str:
    """Run one 5-minute poll for one device; returns the §8 status.

    Never raises for collection trouble (the Wave 1 orchestration normalizes
    everything into the outcome); database errors propagate to the scheduler,
    which contains them per device.
    """

    collected_at = now if now is not None else datetime.now(UTC)
    outcome = run_collection(context.snmp, context.ssh, context.device_name)

    factory = session_factory if session_factory is not None else get_session_factory()
    with factory() as session:
        persist_collection(session, context.device_id, outcome, collected_at)
        run_id = persist_poll_result(
            session, context.device_id, cycle_started_at, outcome, collected_at
        )
        # §9: the SNMP channel yielded valid data == reachable; the SSH probe
        # ran only on a real channel failure (Wave 1 gating).
        apply_device_reachability(
            session,
            context.device_id,
            ReachabilityObservation(
                cycle_started_at=cycle_started_at,
                snmp_ok=outcome.has_valid_data(),
                ssh_reachable=outcome.ssh_reachable,
            ),
        )
        # §13: monitored interface Down/Recovery (only when the interfaces
        # section delivered samples; a failed section is a missing sample).
        if outcome.interfaces is not None:
            record_interface_states(
                session, context.device_id, outcome.interfaces, collected_at
            )
        session.commit()

    logger.info(
        "poll device %s cycle %s: %s (run id %s)",
        context.device_name,
        cycle_started_at,
        outcome.overall_status,
        run_id,
    )
    return outcome.overall_status
