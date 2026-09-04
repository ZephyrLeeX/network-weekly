"""Explicit inventory sync command (SYSTEM_SPEC.md §22.1): devices.toml -> PostgreSQL.

Run as `python -m backend.inventory_sync`. The sync upserts logical devices
by their stable `name` business key; devices already in the database but
absent from the inventory file are left untouched and reported, never
deleted (interfaces and future metrics reference them). Secrets are not
needed for the sync and are therefore not loaded here.
"""

import logging
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings, load_settings
from backend.db.engine import get_session_factory
from backend.db.models import Device
from backend.inventory import DeviceEntry, InventoryError, load_inventory
from backend.log import setup_logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncReport:
    """Counts from one inventory sync run; safe to log (no secrets involved)."""

    created: tuple[str, ...]
    updated: tuple[str, ...]
    unchanged: tuple[str, ...]
    not_in_inventory: tuple[str, ...]


def sync_inventory(session: Session, entries: list[DeviceEntry]) -> SyncReport:
    """Upsert every inventory entry into the devices table by name."""

    existing = {
        device.name: device
        for device in session.execute(select(Device)).scalars().all()
    }

    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []

    for entry in entries:
        device = existing.get(entry.name)
        if device is None:
            session.add(
                Device(
                    name=entry.name,
                    management_ip=entry.management_ip,
                    model_family=entry.model_family,
                    expected_irf_member_count=entry.irf_member_count,
                    credential_profile=entry.credential_profile,
                )
            )
            created.append(entry.name)
            continue

        desired = (
            entry.management_ip,
            entry.model_family,
            entry.irf_member_count,
            entry.credential_profile,
        )
        current = (
            device.management_ip,
            device.model_family,
            device.expected_irf_member_count,
            device.credential_profile,
        )
        if desired == current:
            unchanged.append(entry.name)
        else:
            device.management_ip = entry.management_ip
            device.model_family = entry.model_family
            device.expected_irf_member_count = entry.irf_member_count
            device.credential_profile = entry.credential_profile
            updated.append(entry.name)

    inventory_names = {entry.name for entry in entries}
    not_in_inventory = tuple(sorted(set(existing) - inventory_names))
    if not_in_inventory:
        logger.warning(
            "devices present in the database but absent from the inventory (left untouched): %s",
            ", ".join(not_in_inventory),
        )

    return SyncReport(
        created=tuple(created),
        updated=tuple(updated),
        unchanged=tuple(unchanged),
        not_in_inventory=not_in_inventory,
    )


def main() -> int:
    settings: Settings = load_settings()
    setup_logging(settings)
    try:
        entries = load_inventory(settings.devices_file)
    except InventoryError as exc:
        logger.error("inventory sync aborted: %s", exc)
        return 1

    session_factory = get_session_factory()
    try:
        with session_factory() as session:
            report = sync_inventory(session, entries)
            session.commit()
    except Exception as exc:  # noqa: BLE001  (CLI boundary; class name only, no details)
        logger.error("inventory sync failed (%s)", type(exc).__name__)
        return 1

    logger.info(
        "inventory sync done: %d created, %d updated, %d unchanged, %d not in inventory",
        len(report.created),
        len(report.updated),
        len(report.unchanged),
        len(report.not_in_inventory),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
