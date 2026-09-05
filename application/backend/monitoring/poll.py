"""One DEVICE_POLL of one device: collect, persist, in one transaction
(W02-T002).

The collection itself is the Wave 1 orchestration
(:func:`backend.collect.session.run_collection`) re-used unchanged — Wave 2
adds the scheduling around it and the Wave 2 persistence (topology sync +
poll run + raw metrics in a single transaction, so a crash can never leave a
half-persisted cycle).

§8 completeness: *every* planned cycle of every device lands exactly one
`device_poll_runs` row. A cycle that cannot even be attempted still gets its
FAILED row (:func:`record_skipped_poll` for an overlap skip, `poll_device`
for a device without usable credentials) — a planned cycle never silently
disappears from Coverage. Such a row is bookkeeping, not device evidence:
the §9 reachability and §13 interface state machines are deliberately NOT
advanced for it (a cycle we could not attempt says nothing about the device;
AGENTS.md rule 7), and the §9 continuity anchor treats it as a gap.

Idempotency: a `(device_id, cycle_started_at)` pair is processed at most
once. `poll_device` checks for an existing run before collecting, and the
persist layer reports `newly_persisted` as a race backstop — a replayed
cycle can never advance the state machines twice (which would confirm a
Down from one duplicated sample).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import sessionmaker

from backend.collect.persist import persist_collection
from backend.collect.session import (
    FAILED,
    DeviceCollectionOutcome,
    SectionResult,
    run_collection,
)
from backend.collect.snmp import SnmpConfig
from backend.collect.ssh import SshConfig
from backend.db.engine import get_session_factory
from backend.monitoring.interface_state import record_interface_states
from backend.monitoring.pipeline import (
    load_poll_run_status,
    persist_poll_result,
)
from backend.monitoring.reachability import ReachabilityObservation, apply_device_reachability

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DevicePollContext:
    """Everything needed to poll one device this cycle.

    `snmp is None` marks a device whose cycle is attributable (it occupies a
    planned slot and gets its FAILED poll-run row) but not attemptable —
    e.g. its SNMP community is missing or the secrets file could not be
    loaded. `unavailable_reason` carries the secret-free why.
    """

    device_id: int
    device_name: str
    snmp: SnmpConfig | None = None
    ssh: SshConfig | None = None
    unavailable_reason: str | None = None


# failed_sections markers for cycles that never reached a device.
OVERLAP_SECTION = "overlap"
CREDENTIALS_SECTION = "credentials"


def _unattempted_outcome(
    context: DevicePollContext, section: str, reason: str
) -> DeviceCollectionOutcome:
    """A §8 FAILED outcome for a cycle that could not even be attempted."""

    outcome = DeviceCollectionOutcome(device_name=context.device_name)
    outcome.sections.append(SectionResult(name=section, status=FAILED, error=reason))
    return outcome


def _persist_unattempted(
    context: DevicePollContext,
    cycle_started_at: datetime,
    outcome: DeviceCollectionOutcome,
    *,
    session_factory: sessionmaker | None,
    collected_at: datetime,
) -> str:
    """Persist the FAILED row of an unattempted cycle (no state machines)."""

    factory = session_factory if session_factory is not None else get_session_factory()
    with factory() as session:
        persisted = persist_poll_result(
            session, context.device_id, cycle_started_at, outcome, collected_at
        )
        session.commit()
    run_id = persisted.run_id if persisted is not None else None
    logger.error(
        "poll device %s cycle %s not attempted: %s (run id %s)",
        context.device_name,
        cycle_started_at,
        outcome.sections[0].error,
        run_id,
    )
    return outcome.overall_status


def record_skipped_poll(
    context: DevicePollContext,
    cycle_started_at: datetime,
    *,
    session_factory: sessionmaker | None = None,
    now: datetime | None = None,
) -> str:
    """Record the FAILED run of a cycle skipped because the device's previous
    poll was still in flight (§27.11 no-overlap + §8 one run per cycle).

    The cycle is not polled again (no overlap, no backfill) and the §9/§13
    state machines are not touched — the skip is a collection gap, not a
    reachability observation.
    """

    return _persist_unattempted(
        context,
        cycle_started_at,
        _unattempted_outcome(
            context, OVERLAP_SECTION, "previous poll still in flight; cycle not attempted"
        ),
        session_factory=session_factory,
        collected_at=now if now is not None else datetime.now(UTC),
    )


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

    A cycle that already has its poll-run row is never processed again —
    no second collection and no second §9/§13 state-machine advance.
    """

    collected_at = now if now is not None else datetime.now(UTC)
    factory = session_factory if session_factory is not None else get_session_factory()

    existing_status = _processed_status(factory, context, cycle_started_at)
    if existing_status is not None:
        return existing_status

    if context.snmp is None:
        # §8: the planned cycle is still attributable to this device — record
        # it FAILED instead of letting it vanish. Not device evidence: the
        # reachability/interface state machines stay untouched.
        return _persist_unattempted(
            context,
            cycle_started_at,
            _unattempted_outcome(
                context,
                CREDENTIALS_SECTION,
                context.unavailable_reason or "no SNMP credentials available",
            ),
            session_factory=session_factory,
            collected_at=collected_at,
        )

    outcome = run_collection(context.snmp, context.ssh, context.device_name)

    with factory() as session:
        persist_collection(session, context.device_id, outcome, collected_at)
        persisted = persist_poll_result(
            session, context.device_id, cycle_started_at, outcome, collected_at
        )
        # §9/§13 state machines advance only when THIS call created the run
        # row; a cycle persisted by an earlier attempt is already final.
        if persisted is not None and persisted.newly_persisted:
            # §9: the SNMP channel yielded valid data == reachable; the SSH
            # probe ran only on a real channel failure (Wave 1 gating).
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

    run_id = persisted.run_id if persisted is not None else None
    logger.info(
        "poll device %s cycle %s: %s (run id %s)",
        context.device_name,
        cycle_started_at,
        outcome.overall_status,
        run_id,
    )
    return outcome.overall_status


def _processed_status(
    factory: sessionmaker, context: DevicePollContext, cycle_started_at: datetime
) -> str | None:
    """The §8 status when this `(device, cycle)` was already fully processed."""

    with factory() as session:
        status = load_poll_run_status(session, context.device_id, cycle_started_at)
    if status is not None:
        logger.warning(
            "poll device %s cycle %s already processed (%s); skipping re-processing",
            context.device_name,
            cycle_started_at,
            status,
        )
    return status
