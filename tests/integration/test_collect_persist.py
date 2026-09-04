"""Integration tests for W01-T006 persistence of collection outcomes.

Runs `persist_collection` against the real migrated PostgreSQL: identity
fields, interface discovery, aggregation membership and IRF member upsert
in one transaction, with section isolation already proven at unit level.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from backend.collect.dto import (
    AggregationMapping,
    DeviceIdentity,
    InterfaceSample,
    IrfMemberSample,
    normalize_interface_name,
)
from backend.collect.persist import persist_collection
from backend.collect.session import DeviceCollectionOutcome, SectionResult
from backend.db.models import AggregationMember, Device, DeviceMember, Interface

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(AggregationMember).delete()
        session.query(Interface).delete()
        session.query(DeviceMember).delete()
        session.query(Device).delete()
        session.commit()
    yield


@pytest.fixture
def device_id(db_engine: Engine) -> int:
    with Session(db_engine) as session:
        device = Device(
            name="core-s10500x-irf",
            management_ip="192.0.2.19",
            model_family="s10500x",
            expected_irf_member_count=2,
            credential_profile="default",
        )
        session.add(device)
        session.commit()
        return device.id


def _iface(if_index: int, name: str) -> InterfaceSample:
    return InterfaceSample(
        if_index=if_index,
        name=name,
        normalized_name=normalize_interface_name(name),
        description="uplink" if if_index == 49 else None,
        admin_state="up",
        oper_state="up",
        speed_bps=10_000_000_000,
        in_octets=None,
        out_octets=None,
        in_errors=None,
        out_errors=None,
        in_discards=None,
        out_discards=None,
    )


def _outcome(identity: DeviceIdentity | None = None) -> DeviceCollectionOutcome:
    outcome = DeviceCollectionOutcome(
        device_name="core-s10500x-irf",
        sections=[SectionResult(name="identity", status="SUCCESS")],
        identity=identity,
        interfaces=[_iface(49, "Ten-GigabitEthernet1/0/49"), _iface(65, "Bridge-Aggregation1")],
        aggregations=[AggregationMapping(aggregation_if_index=65, member_if_indexes=(49,))],
        irf_members=[IrfMemberSample(member_id=1, role="Master"), IrfMemberSample(member_id=2)],
    )
    return outcome


def test_persist_collection_writes_all_valid_sections(
    db_engine: Engine, device_id: int
) -> None:
    identity = DeviceIdentity(
        sys_name="core-s10500x-irf",
        sys_description="H3C Comware ...",
        sys_object_id="1.3.6.1.4.1.25506.1.617",
        uptime_seconds=123.4,
    )
    with Session(db_engine) as session:
        persist_collection(session, device_id, _outcome(identity), NOW)
        session.commit()

        device = session.get(Device, device_id)
        assert device is not None
        assert device.sys_name == "core-s10500x-irf"
        assert device.sys_object_id == "1.3.6.1.4.1.25506.1.617"

        assert session.scalar(select(func.count()).select_from(Interface)) == 2
        agg = session.execute(
            select(Interface).where(Interface.normalized_name == "bridge-aggregation1")
        ).scalar_one()
        assert agg.is_aggregation is True
        assert [m.member_interface.normalized_name for m in agg.aggregate_members] == [
            "ten-gigabitethernet1/0/49"
        ]

        members = {
            m.member_id: m
            for m in session.execute(
                select(DeviceMember).where(DeviceMember.device_id == device_id)
            )
            .scalars()
            .all()
        }
        assert set(members) == {1, 2}
        assert members[1].role == "Master"
        assert members[2].role is None  # unknown role stays None, never guessed
        assert members[1].last_seen_at is not None


def test_persist_collection_preserves_unknown_role_and_absent_member(
    db_engine: Engine, device_id: int
) -> None:
    with Session(db_engine) as session:
        persist_collection(session, device_id, _outcome(), NOW)
        session.commit()

        # Second run: only member 1 observed, role unknown this time.
        outcome = DeviceCollectionOutcome(
            device_name="core-s10500x-irf",
            irf_members=[IrfMemberSample(member_id=1, role=None)],
        )
        persist_collection(session, device_id, outcome, NOW)
        session.commit()

        members = {
            m.member_id: m
            for m in session.execute(select(DeviceMember))
            .scalars()
            .all()
        }
        # Missing member 2 is kept (absence is a Wave 2 observation event).
        assert set(members) == {1, 2}
        # A None role does not erase the previously observed one.
        assert members[1].role == "Master"


def test_persist_collection_with_identity_only(
    db_engine: Engine, device_id: int
) -> None:
    identity = DeviceIdentity("name", None, None, None)
    outcome = DeviceCollectionOutcome(
        device_name="core-s10500x-irf",
        sections=[SectionResult(name="identity", status="SUCCESS")],
        identity=identity,
    )
    with Session(db_engine) as session:
        persist_collection(session, device_id, outcome, NOW)
        session.commit()
        device = session.get(Device, device_id)
        assert device is not None and device.sys_name == "name"
        assert session.scalar(select(func.count()).select_from(Interface)) == 0


def test_persist_unknown_device_rejected(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        with pytest.raises(ValueError, match="does not exist"):
            persist_collection(session, 99999, _outcome(), NOW)
