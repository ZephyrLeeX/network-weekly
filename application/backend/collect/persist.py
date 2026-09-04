"""Persistence of a DeviceCollectionOutcome (W01-T006).

Wave 1 persists the *topology and identity* data: device identity fields,
discovered interfaces, aggregation membership and IRF member observations.
Per-sample metric time series (CPU/memory/counters) are Wave 2 tables and
are deliberately not stored here.

All writes are driven by :mod:`backend.collect.discovery` semantics:
identity keys never involve ifIndex, `monitored` is never touched, and IRF
members currently missing from an observation are kept (their absence is a
Wave 2 observation event, not a deletion).
"""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.collect.discovery import sync_aggregations, sync_interfaces
from backend.collect.session import DeviceCollectionOutcome
from backend.db.models import Device, DeviceMember

logger = logging.getLogger(__name__)


def persist_collection(
    session: Session, device_id: int, outcome: DeviceCollectionOutcome, now: datetime
) -> None:
    """Persist every valid section of one collection outcome in one transaction."""

    device = session.get(Device, device_id)
    if device is None:
        raise ValueError(f"cannot persist collection: device id {device_id} does not exist")

    if outcome.identity is not None:
        device.sys_name = outcome.identity.sys_name
        device.sys_description = outcome.identity.sys_description
        device.sys_object_id = outcome.identity.sys_object_id
        device.updated_at = now

    if outcome.software is not None and outcome.software.software_version is not None:
        device.software_version = outcome.software.software_version
        device.updated_at = now

    if outcome.interfaces is not None:
        sync_interfaces(session, device_id, outcome.interfaces, now)

    if outcome.aggregations is not None:
        sync_aggregations(session, device_id, outcome.aggregations)

    if outcome.irf_members is not None:
        _upsert_members(session, device_id, outcome, now)

    logger.info("persisted collection for device id %d (%s)", device_id, outcome.device_name)


def _upsert_members(
    session: Session, device_id: int, outcome: DeviceCollectionOutcome, now: datetime
) -> None:
    assert outcome.irf_members is not None
    existing = {
        member.member_id: member
        for member in session.execute(
            select(DeviceMember).where(DeviceMember.device_id == device_id)
        )
        .scalars()
        .all()
    }
    for sample in outcome.irf_members:
        member = existing.get(sample.member_id)
        if member is None:
            member = DeviceMember(device_id=device_id, member_id=sample.member_id)
            session.add(member)
            existing[sample.member_id] = member
        # Role only updates from reliable source data (SYSTEM_SPEC.md §17);
        # None never erases a previously observed role.
        if sample.role is not None:
            member.role = sample.role
        if sample.model is not None:
            member.model = sample.model
        if sample.software_version is not None:
            member.software_version = sample.software_version
        member.last_seen_at = now
