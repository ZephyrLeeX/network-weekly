"""Integration tests for the Wave 1 device/interface schema (W01-T002).

Runs against the dev compose PostgreSQL with the real Alembic migrations and
proves the current topology is representable (SYSTEM_SPEC.md §2.2): 8
standalone S10500X + 1 two-member S10500X IRF + 1 two-member S12500 IRF =
10 logical devices / 12 physical chassis; ifIndex is mutable metadata; the
aggregation relationship persists.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from backend.db.models import AggregationMember, Device, DeviceMember, Interface

pytestmark = pytest.mark.integration

STANDALONE_NAMES = [f"core-s10500x-{i:02d}" for i in range(1, 9)]


@pytest.fixture(autouse=True)
def _clean_devices(db_engine: Engine) -> Iterator[None]:
    """The migrated test database is session-scoped; clear rows between tests."""

    with Session(db_engine) as session:
        session.query(AggregationMember).delete()
        session.query(Interface).delete()
        session.query(DeviceMember).delete()
        session.query(Device).delete()
        session.commit()
    yield


def _seed_topology(session: Session) -> dict[str, Device]:
    """Create the current 10 logical device / 12 chassis topology."""

    devices: dict[str, Device] = {}
    for name in STANDALONE_NAMES:
        device = Device(
            name=name,
            management_ip="10.0.0.1",
            model_family="s10500x",
            expected_irf_member_count=None,
            credential_profile="default",
        )
        session.add(device)
        devices[name] = device

    s_irf = Device(
        name="core-s10500x-irf",
        management_ip="10.0.0.1",
        model_family="s10500x",
        expected_irf_member_count=2,
        credential_profile="default",
    )
    session.add(s_irf)
    for member_id, role in ((1, "Master"), (2, "Slave")):
        s_irf.members.append(DeviceMember(member_id=member_id, role=role))
    devices["core-s10500x-irf"] = s_irf

    s12500 = Device(
        name="core-s12500-irf",
        management_ip="10.0.0.1",
        model_family="s12500",
        expected_irf_member_count=2,
        credential_profile="default",
    )
    session.add(s12500)
    for member_id, role in ((1, "Master"), (2, "Backup")):
        s12500.members.append(DeviceMember(member_id=member_id, role=role))
    devices["core-s12500-irf"] = s12500

    session.flush()
    return devices


def test_current_topology_is_representable(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        devices = _seed_topology(session)

        assert session.scalar(select(func.count()).select_from(Device)) == 10
        assert session.scalar(select(func.count()).select_from(DeviceMember)) == 4

        irf = devices["core-s10500x-irf"]
        member_ids = sorted(m.member_id for m in irf.members)
        assert member_ids == [1, 2]

        session.commit()


def test_ifindex_is_mutable_metadata_not_identity(db_engine: Engine) -> None:
    """A changed ifIndex updates the existing row instead of duplicating it."""

    with Session(db_engine) as session:
        device = _seed_topology(session)["core-s10500x-01"]
        session.add(
            Interface(
                device_id=device.id,
                normalized_name="ten-gigabitethernet1/0/1",
                display_name="Ten-GigabitEthernet1/0/1",
                if_index=10,
            )
        )
        session.commit()

        # Rediscovery with a different ifIndex (e.g. after an IRF merge).
        row = session.execute(
            select(Interface).where(
                Interface.device_id == device.id,
                Interface.normalized_name == "ten-gigabitethernet1/0/1",
            )
        ).scalar_one()
        row.if_index = 77
        session.flush()

        count = session.scalar(
            select(func.count()).select_from(Interface).where(Interface.device_id == device.id)
        )
        assert count == 1
        assert row.if_index == 77
        session.rollback()


def test_aggregation_relationship_persists(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _seed_topology(session)["core-s10500x-01"]
        agg = Interface(
            device_id=device.id,
            normalized_name="bridge-aggregation1",
            display_name="Bridge-Aggregation1",
            is_aggregation=True,
        )
        member1 = Interface(
            device_id=device.id,
            normalized_name="ten-gigabitethernet1/0/49",
            display_name="Ten-GigabitEthernet1/0/49",
        )
        member2 = Interface(
            device_id=device.id,
            normalized_name="ten-gigabitethernet2/0/49",
            display_name="Ten-GigabitEthernet2/0/49",
        )
        session.add_all([agg, member1, member2])
        session.flush()
        session.add_all(
            [
                AggregationMember(aggregation_interface_id=agg.id, member_interface_id=member1.id),
                AggregationMember(aggregation_interface_id=agg.id, member_interface_id=member2.id),
            ]
        )
        session.commit()

        loaded = session.execute(
            select(Interface).where(
                Interface.device_id == device.id,
                Interface.normalized_name == "bridge-aggregation1",
            )
        ).scalar_one()
        member_names = sorted(m.member_interface.normalized_name for m in loaded.aggregate_members)
        assert member_names == [
            "ten-gigabitethernet1/0/49",
            "ten-gigabitethernet2/0/49",
        ]
        session.delete(loaded)
        session.commit()
        assert session.scalar(select(func.count()).select_from(AggregationMember)) == 0


def test_monitored_defaults_false_and_survives_row_update(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _seed_topology(session)["core-s10500x-01"]
        iface = Interface(
            device_id=device.id,
            normalized_name="ten-gigabitethernet1/0/1",
            display_name="Ten-GigabitEthernet1/0/1",
        )
        session.add(iface)
        session.commit()
        assert iface.monitored is False

        # Discovery refresh must be able to update metadata without touching
        # `monitored` (enforced by the discovery service in W01-T004; here the
        # column simply exists and keeps its value on metadata-only updates).
        iface.if_index = 42
        iface.last_seen_at = datetime.now(UTC)
        session.commit()
        assert iface.monitored is False


def test_device_cascade_deletes_members_and_interfaces(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _seed_topology(session)["core-s10500x-01"]
        session.add(
            Interface(
                device_id=device.id,
                normalized_name="ten-gigabitethernet1/0/1",
                display_name="Ten-GigabitEthernet1/0/1",
            )
        )
        session.commit()

        session.delete(device)
        session.commit()
        assert session.scalar(select(func.count()).select_from(DeviceMember)) == 4  # only IRFs
        assert session.scalar(select(func.count()).select_from(Interface)) == 0


def test_duplicate_interface_identity_rejected(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _seed_topology(session)["core-s10500x-01"]
        session.add(
            Interface(
                device_id=device.id,
                normalized_name="ten-gigabitethernet1/0/1",
                display_name="Ten-GigabitEthernet1/0/1",
            )
        )
        session.commit()
        session.add(
            Interface(
                device_id=device.id,
                normalized_name="ten-gigabitethernet1/0/1",
                display_name="Ten-GigabitEthernet1/0/1",
            )
        )
        with pytest.raises(Exception, match="uq_interface_identity"):
            session.commit()


def test_read_of_new_device_defaults(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _seed_topology(session)["core-s10500x-01"]
        session.commit()
        assert device.enabled is True
        assert device.sys_name is None
        assert device.software_version is None
