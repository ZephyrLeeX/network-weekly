"""Integration test for the inventory sync command (W01-T001).

Runs the real `sync_inventory` against the migrated dev PostgreSQL and
proves the current 10-logical-device topology can be synced and re-synced
idempotently, with drift updates and absent-device reporting.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from backend.db.models import Device
from backend.inventory import DeviceEntry, load_inventory
from backend.inventory_sync import sync_inventory

pytestmark = pytest.mark.integration

TOML = """
[[device]]
name = "core-s10500x-01"
management_ip = "192.0.2.11"
model_family = "s10500x"

[[device]]
name = "core-s10500x-irf"
management_ip = "192.0.2.19"
model_family = "s10500x"
irf_member_count = 2
credential_profile = "core"
"""


@pytest.fixture(autouse=True)
def _clean_devices(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(Device).delete()
        session.commit()
    yield


def _entries(tmp_path: Path, toml: str = TOML) -> list[DeviceEntry]:
    path = tmp_path / "devices.toml"
    path.write_text(toml)
    return load_inventory(path)


def test_sync_creates_updates_and_is_idempotent(db_engine: Engine, tmp_path: Path) -> None:
    entries = _entries(tmp_path)

    with Session(db_engine) as session:
        report = sync_inventory(session, entries)
        session.commit()
        assert report.created == ("core-s10500x-01", "core-s10500x-irf")
        assert session.scalar(select(func.count()).select_from(Device)) == 2

        device = session.execute(
            select(Device).where(Device.name == "core-s10500x-irf")
        ).scalar_one()
        assert device.expected_irf_member_count == 2
        assert device.credential_profile == "core"
        assert device.management_ip == "192.0.2.19"

        # Re-run: unchanged, no duplicates.
        report2 = sync_inventory(session, entries)
        session.commit()
        assert report2.created == ()
        assert set(report2.unchanged) == {"core-s10500x-01", "core-s10500x-irf"}
        assert session.scalar(select(func.count()).select_from(Device)) == 2


def test_sync_updates_drift_and_reports_absent_devices(
    db_engine: Engine, tmp_path: Path
) -> None:
    entries = _entries(tmp_path)
    with Session(db_engine) as session:
        sync_inventory(session, entries)
        session.commit()

        # Inventory drift: management IP + profile change, one device removed.
        changed = _entries(
            tmp_path,
            """
[[device]]
name = "core-s10500x-01"
management_ip = "192.0.2.99"
model_family = "s10500x"
credential_profile = "rotated"
""",
        )
        report = sync_inventory(session, changed)
        session.commit()

        assert report.updated == ("core-s10500x-01",)
        assert report.not_in_inventory == ("core-s10500x-irf",)

        device = session.execute(
            select(Device).where(Device.name == "core-s10500x-01")
        ).scalar_one()
        assert device.management_ip == "192.0.2.99"
        assert device.credential_profile == "rotated"

        # Absent devices are kept, not deleted.
        assert session.scalar(select(func.count()).select_from(Device)) == 2
