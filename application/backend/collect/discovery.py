"""Interface discovery persistence (SYSTEM_SPEC.md §10/§11).

Turns normalized `InterfaceSample`/`AggregationMapping` DTOs into database
rows. Business identity is `(device_id, normalized_name)` — ifIndex is
mutable metadata that is refreshed in place, never part of the key, and a
changed ifIndex can therefore never duplicate an interface. The `monitored`
flag belongs to the administrator (§12): discovery never writes it, so
rediscovery cannot lose the operator's selection.

Discovery only maintains interface *metadata*; per-sample counters/state
time series are Wave 2 metric tables. Rows whose interface disappeared from
the current discovery run are left untouched (staleness handling is a Wave 2
concern via missing samples, §13.3).
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.collect.dto import AggregationMapping, InterfaceSample
from backend.db.models import AggregationMember, Interface

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterfaceSyncReport:
    """Counts from one discovery run; names only, safe to log."""

    created: int
    updated: int
    unchanged: int


@dataclass(frozen=True)
class AggregationSyncReport:
    memberships_added: int
    memberships_removed: int
    unresolved: tuple[str, ...]  # aggregation/member ifIndexes that had no Interface row


def _by_normalized_name(session: Session, device_id: int) -> dict[str, Interface]:
    return {
        iface.normalized_name: iface
        for iface in session.execute(
            select(Interface).where(Interface.device_id == device_id)
        )
        .scalars()
        .all()
    }


def sync_interfaces(
    session: Session, device_id: int, samples: list[InterfaceSample], now: datetime
) -> InterfaceSyncReport:
    """Upsert discovered interface metadata keyed by normalized name."""

    existing = _by_normalized_name(session, device_id)
    created = updated = unchanged = 0

    for sample in samples:
        row = existing.get(sample.normalized_name)
        if row is None:
            session.add(
                Interface(
                    device_id=device_id,
                    normalized_name=sample.normalized_name,
                    display_name=sample.name,
                    description=sample.description,
                    if_index=sample.if_index,
                    admin_state=sample.admin_state,
                    oper_state=sample.oper_state,
                    speed_bps=sample.speed_bps,
                    last_seen_at=now,
                )
            )
            created += 1
            continue

        desired = (
            sample.name,
            sample.description,
            sample.if_index,
            sample.admin_state,
            sample.oper_state,
            sample.speed_bps,
        )
        current = (
            row.display_name,
            row.description,
            row.if_index,
            row.admin_state,
            row.oper_state,
            row.speed_bps,
        )
        if desired != current:
            row.display_name = sample.name
            row.description = sample.description
            row.if_index = sample.if_index
            row.admin_state = sample.admin_state
            row.oper_state = sample.oper_state
            row.speed_bps = sample.speed_bps
            row.last_seen_at = now
            updated += 1
        else:
            unchanged += 1
            if row.last_seen_at is None or row.last_seen_at < now:
                row.last_seen_at = now

    return InterfaceSyncReport(created=created, updated=updated, unchanged=unchanged)


def sync_aggregations(
    session: Session, device_id: int, mappings: list[AggregationMapping]
) -> AggregationSyncReport:
    """Persist aggregation -> member membership from the current mapping.

    The database is reconciled to the device, both directions:

    - Membership rows for a still-mapped aggregation follow the current
      member list (a port leaving a lag must not stay a member).
    - Membership rows and `is_aggregation` flags whose aggregation no
      longer appears in the mapping are removed — a successful collection
      that reports *no* aggregations clears stale state. Callers must only
      invoke this with mappings from a *successful* collection
      (`aggregations is not None`); a failed collection never clears.

    Interfaces involved must already have been discovered (their ifIndex
    must map to a row); anything unresolvable is reported, never guessed.
    """

    rows = (
        session.execute(select(Interface).where(Interface.device_id == device_id))
        .scalars()
        .all()
    )
    by_if_index = {row.if_index: row for row in rows if row.if_index is not None}
    by_id = {row.id: row for row in rows}
    mapped_agg_if_indexes = {mapping.aggregation_if_index for mapping in mappings}

    unresolved: list[str] = []
    added = removed = 0

    for mapping in mappings:
        agg_row = by_if_index.get(mapping.aggregation_if_index)
        if agg_row is None:
            unresolved.append(f"agg:{mapping.aggregation_if_index}")
            continue
        agg_row.is_aggregation = True

        member_rows = []
        for member_if_index in mapping.member_if_indexes:
            member_row = by_if_index.get(member_if_index)
            if member_row is None:
                unresolved.append(f"member:{member_if_index}")
                continue
            member_rows.append(member_row)

        current = {
            membership.member_interface_id: membership
            for membership in session.execute(
                select(AggregationMember).where(
                    AggregationMember.aggregation_interface_id == agg_row.id
                )
            )
            .scalars()
            .all()
        }
        desired_ids = {row.id for row in member_rows}

        for member_id in desired_ids - set(current):
            session.add(
                AggregationMember(
                    aggregation_interface_id=agg_row.id, member_interface_id=member_id
                )
            )
            added += 1
        for member_id, membership in current.items():
            if member_id not in desired_ids:
                session.delete(membership)
                removed += 1

    # Stale state: memberships (and the aggregation flag) whose aggregation
    # interface is absent from the current successful mapping. Rows whose
    # ifIndex is unknown are left alone rather than guessed.
    for membership in session.execute(
        select(AggregationMember).where(
            AggregationMember.aggregation_interface_id.in_(by_id.keys())
        )
    ).scalars():
        agg_row = by_id[membership.aggregation_interface_id]
        if agg_row.if_index is not None and agg_row.if_index not in mapped_agg_if_indexes:
            session.delete(membership)
            removed += 1
    for row in rows:
        if (
            row.is_aggregation
            and row.if_index is not None
            and row.if_index not in mapped_agg_if_indexes
        ):
            row.is_aggregation = False

    if unresolved:
        logger.warning(
            "aggregation sync had unresolved ifIndexes (no Interface row yet): %s",
            ", ".join(sorted(set(unresolved))),
        )

    return AggregationSyncReport(
        memberships_added=added,
        memberships_removed=removed,
        unresolved=tuple(sorted(set(unresolved))),
    )
