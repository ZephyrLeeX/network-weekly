"""Per-cycle monitoring pipeline (W02-T001/T003).

One call per planned 5-minute cycle per device turns a Wave 1
:class:`DeviceCollectionOutcome` into the Wave 2 raw-data rows:

- one `device_poll_runs` row — SUCCESS / PARTIAL / FAILED (§8) — whatever
  the outcome, so Monitoring Coverage counts stay honest (§18). That
  includes planned cycles that could not even be attempted (overlap skip,
  unusable credentials): they land as FAILED with the reason as the failed
  section, without advancing the §9/§13 state machines;
- one `device_metrics` row when the cycle produced valid CPU/memory data;
- one `interface_metrics` row per sampled interface, with delta-based
  utilization against the interface's previous stored sample and the
  rebaseline semantics of §15.2 (W02-T003).

Persisted in ONE transaction together with the Wave 1 topology sync, so a
crash never leaves a poll run without its metric rows or vice versa.

The device reachability state machine (W02-T004) and the priority-interface
state machine (W02-T006) join this pipeline with their own tasks. Only
section *names* are persisted on the poll run; no error text and no
secret-bearing material ever reaches these tables.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.collect.dto import EntityLoadSample, InterfaceSample
from backend.collect.session import DeviceCollectionOutcome
from backend.db.models import Device, DeviceMetric, DevicePollRun, Interface, InterfaceMetric
from backend.monitoring.utilization import PreviousSample, compute_utilization

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PersistedPollRun:
    """The poll run a cycle landed on, and whether this call created it.

    `newly_persisted` is False when the `(device_id, cycle_started_at)` row
    already existed: the cycle was fully processed earlier and nothing —
    metrics, §9 reachability, §13 interface states — may be advanced again.
    """

    run_id: int
    newly_persisted: bool


def peak_usage_percent(samples: list[EntityLoadSample] | None) -> float | None:
    """Device-level usage for one cycle: the peak across reported entities.

    Weekly CPU/memory statistics are per logical device (§14); for an IRF
    fabric that means the busiest chassis. None (no samples) stays None —
    a missing sample is never replaced by 0 (§6.2).
    """

    if not samples:
        return None
    return max((sample.usage_percent for sample in samples), default=None)


def _interface_ids_by_name(session: Session, device_id: int) -> dict[str, int]:
    return {
        normalized_name: id_
        for normalized_name, id_ in session.execute(
            select(Interface.normalized_name, Interface.id).where(
                Interface.device_id == device_id
            )
        ).all()
    }


def _interface_metric(
    poll_run_id: int,
    device_id: int,
    interface_id: int,
    collected_at: datetime,
    sample: InterfaceSample,
    previous: PreviousSample | None,
) -> InterfaceMetric:
    """Build one interface metric row, with §15 utilization / rebaseline."""

    result = compute_utilization(
        previous, collected_at, sample.in_octets, sample.out_octets, sample.speed_bps
    )
    return InterfaceMetric(
        poll_run_id=poll_run_id,
        device_id=device_id,
        interface_id=interface_id,
        collected_at=collected_at,
        admin_state=sample.admin_state,
        oper_state=sample.oper_state,
        speed_bps=sample.speed_bps,
        in_octets=sample.in_octets,
        out_octets=sample.out_octets,
        in_errors=sample.in_errors,
        out_errors=sample.out_errors,
        in_discards=sample.in_discards,
        out_discards=sample.out_discards,
        fcs_errors=sample.fcs_errors,
        in_utilization_percent=result.in_utilization_percent,
        out_utilization_percent=result.out_utilization_percent,
        utilization_elapsed_seconds=result.elapsed_seconds,
        utilization_rebaselined=result.rebaselined,
    )


def _previous_samples(
    session: Session, interface_ids: set[int]
) -> dict[int, InterfaceMetric]:
    """Latest stored sample per interface (the §15 counter baseline)."""

    if not interface_ids:
        return {}
    rows = (
        session.execute(
            select(InterfaceMetric)
            .where(InterfaceMetric.interface_id.in_(interface_ids))
            .distinct(InterfaceMetric.interface_id)
            .order_by(
                InterfaceMetric.interface_id,
                InterfaceMetric.collected_at.desc(),
                InterfaceMetric.id.desc(),
            )
        )
        .scalars()
        .all()
    )
    return {row.interface_id: row for row in rows}


def load_poll_run_status(
    session: Session, device_id: int, cycle_started_at: datetime
) -> str | None:
    """The persisted §8 status of one planned cycle, or None when unprocessed."""

    return session.execute(
        select(DevicePollRun.status).where(
            DevicePollRun.device_id == device_id,
            DevicePollRun.cycle_started_at == cycle_started_at,
        )
    ).scalar_one_or_none()


def persist_poll_result(
    session: Session,
    device_id: int,
    cycle_started_at: datetime,
    outcome: DeviceCollectionOutcome,
    collected_at: datetime,
) -> PersistedPollRun | None:
    """Persist one cycle's poll run + raw metric rows.

    Idempotent per `(device_id, cycle_started_at)`: an already-persisted
    cycle is left untouched and reported with ``newly_persisted=False``, so
    a retry can never duplicate a cycle or re-advance the state machines
    (§8: one poll run per planned cycle per device).
    Returns None when the device does not exist.
    """

    if session.get(Device, device_id) is None:
        logger.error("cannot persist poll result: device id %d does not exist", device_id)
        return None

    existing_id = session.execute(
        pg_insert(DevicePollRun)
        .values(
            device_id=device_id,
            cycle_started_at=cycle_started_at,
            status=outcome.overall_status,
            ssh_reachable=outcome.ssh_reachable,
            failed_sections=", ".join(outcome.failed_sections) or None,
        )
        .on_conflict_do_nothing(constraint="uq_device_poll_run_cycle")
        .returning(DevicePollRun.id)
    ).scalar_one_or_none()
    if existing_id is None:
        # The cycle was already fully persisted; its metric rows are final.
        existing_run_id = session.execute(
            select(DevicePollRun.id).where(
                DevicePollRun.device_id == device_id,
                DevicePollRun.cycle_started_at == cycle_started_at,
            )
        ).scalar_one()
        logger.warning(
            "poll run for device %d cycle %s already persisted; leaving it untouched",
            device_id,
            cycle_started_at,
        )
        return PersistedPollRun(run_id=existing_run_id, newly_persisted=False)
    run_id = existing_id

    cpu_percent = peak_usage_percent(outcome.cpu)
    memory_percent = peak_usage_percent(outcome.memory)
    if cpu_percent is not None or memory_percent is not None:
        session.add(
            DeviceMetric(
                poll_run_id=run_id,
                device_id=device_id,
                collected_at=collected_at,
                cpu_usage_percent=cpu_percent,
                memory_usage_percent=memory_percent,
            )
        )

    if outcome.interfaces is not None:
        ids_by_name = _interface_ids_by_name(session, device_id)
        baselines = _previous_samples(session, set(ids_by_name.values()))
        skipped: list[str] = []
        for sample in outcome.interfaces:
            interface_id = ids_by_name.get(sample.normalized_name)
            if interface_id is None:
                # Discovery (run first, same transaction) owns interface rows;
                # a sample without one cannot be referenced and is never guessed.
                skipped.append(sample.normalized_name)
                continue
            previous_row = baselines.get(interface_id)
            previous = (
                PreviousSample(
                    collected_at=previous_row.collected_at,
                    in_octets=previous_row.in_octets,
                    out_octets=previous_row.out_octets,
                    speed_bps=previous_row.speed_bps,
                )
                if previous_row is not None
                else None
            )
            session.add(
                _interface_metric(
                    run_id, device_id, interface_id, collected_at, sample, previous
                )
            )
        if skipped:
            logger.warning(
                "cycle %s: %d interface sample(s) had no Interface row and were not stored",
                cycle_started_at,
                len(skipped),
            )

    logger.info(
        "persisted %s poll run id %d for device %d (cycle %s, failed sections: %s)",
        outcome.overall_status,
        run_id,
        device_id,
        cycle_started_at,
        ", ".join(outcome.failed_sections) or "none",
    )
    return PersistedPollRun(run_id=run_id, newly_persisted=True)
