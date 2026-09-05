"""Per-cycle monitoring pipeline (W02-T001+).

One call per planned 5-minute cycle per device turns a Wave 1
:class:`DeviceCollectionOutcome` into the Wave 2 raw-data rows:

- one `device_poll_runs` row — SUCCESS / PARTIAL / FAILED (§8) — whatever
  the outcome, so Monitoring Coverage counts stay honest (§18);
- one `device_metrics` row when the cycle produced valid CPU/memory data;
- one `interface_metrics` row per sampled interface.

Persisted in ONE transaction together with the Wave 1 topology sync, so a
crash never leaves a poll run without its metric rows or vice versa.

Utilization (W02-T003), the reachability state machine (W02-T004) and the
priority-interface state machine (W02-T006) are added to this pipeline by
their own tasks. Only section *names* are persisted on the poll run; no
error text and no secret-bearing material ever reaches these tables.
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.collect.dto import EntityLoadSample, InterfaceSample
from backend.collect.session import DeviceCollectionOutcome
from backend.db.models import Device, DeviceMetric, DevicePollRun, Interface, InterfaceMetric

logger = logging.getLogger(__name__)


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
) -> InterfaceMetric:
    """Build one interface metric row from a sample (utilization: W02-T003)."""

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
    )


def persist_poll_result(
    session: Session,
    device_id: int,
    cycle_started_at: datetime,
    outcome: DeviceCollectionOutcome,
    collected_at: datetime,
) -> int | None:
    """Persist one cycle's poll run + raw metric rows; return the run id.

    Idempotent per `(device_id, cycle_started_at)`: an already-persisted
    cycle is left untouched and its run id returned, so a retry can never
    duplicate a cycle (§8: one poll run per planned cycle per device).
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
        return existing_run_id
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
        skipped: list[str] = []
        for sample in outcome.interfaces:
            interface_id = ids_by_name.get(sample.normalized_name)
            if interface_id is None:
                # Discovery (run first, same transaction) owns interface rows;
                # a sample without one cannot be referenced and is never guessed.
                skipped.append(sample.normalized_name)
                continue
            session.add(
                _interface_metric(run_id, device_id, interface_id, collected_at, sample)
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
    return run_id
