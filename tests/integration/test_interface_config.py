"""Integration tests for the priority interface configuration service
(W02-T005, SYSTEM_SPEC.md §11/§12) and rediscovery preservation of the
`monitored` flag.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from backend.collect.discovery import sync_interfaces
from backend.collect.dto import InterfaceSample, normalize_interface_name
from backend.db.models import AggregationMember, Device, Interface
from backend.monitoring.interface_config import (
    InterfaceNotFoundError,
    interface_overview,
    set_monitored,
)

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(AggregationMember).delete()
        session.query(Interface).delete()
        session.query(Device).delete()
        session.commit()
    yield


@pytest.fixture
def device_id(db_engine: Engine) -> int:
    with Session(db_engine) as session:
        device = Device(
            name="core-irf",
            management_ip="192.0.2.41",
            model_family="s10500x",
            expected_irf_member_count=2,
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        session.add_all(
            [
                Interface(
                    device_id=device.id,
                    normalized_name="bridge-aggregation1",
                    display_name="Bridge-Aggregation1",
                    is_aggregation=True,
                ),
                Interface(
                    device_id=device.id,
                    normalized_name="ten-gigabitethernet1/0/49",
                    display_name="Ten-GigabitEthernet1/0/49",
                ),
                Interface(
                    device_id=device.id,
                    normalized_name="ten-gigabitethernet2/0/49",
                    display_name="Ten-GigabitEthernet2/0/49",
                ),
            ]
        )
        session.commit()
        session.add(
            AggregationMember(
                aggregation_interface_id=session.execute(
                    select(Interface.id).where(
                        Interface.normalized_name == "bridge-aggregation1"
                    )
                ).scalar_one(),
                member_interface_id=session.execute(
                    select(Interface.id).where(
                        Interface.normalized_name == "ten-gigabitethernet1/0/49"
                    )
                ).scalar_one(),
            )
        )
        session.commit()
        return device.id


def _iface_id(session: Session, normalized_name: str) -> int:
    return session.execute(
        select(Interface.id).where(Interface.normalized_name == normalized_name)
    ).scalar_one()


def test_selecting_aggregation_does_not_auto_select_members(
    db_engine: Engine, device_id: int
) -> None:
    with Session(db_engine) as session:
        agg_id = _iface_id(session, "bridge-aggregation1")
        set_monitored(session, agg_id, True)
        session.commit()

        agg = session.get(Interface, agg_id)
        assert agg is not None and agg.monitored is True
        for member in agg.aggregate_members:
            assert member.member_interface.monitored is False


def test_member_is_independently_selectable(db_engine: Engine, device_id: int) -> None:
    with Session(db_engine) as session:
        member_id = _iface_id(session, "ten-gigabitethernet2/0/49")
        set_monitored(session, member_id, True)
        session.commit()
        # A member can be monitored without its aggregation being monitored.
        member = session.get(Interface, member_id)
        assert member is not None and member.monitored is True
        assert member.aggregation_memberships == []

        # And the LAG member stays independently toggleable.
        agg_id = _iface_id(session, "bridge-aggregation1")
        agg = session.get(Interface, agg_id)
        assert agg is not None and agg.monitored is False


def test_toggle_off_and_unknown_id(db_engine: Engine, device_id: int) -> None:
    with Session(db_engine) as session:
        agg_id = _iface_id(session, "bridge-aggregation1")
        set_monitored(session, agg_id, True)
        set_monitored(session, agg_id, False)
        session.commit()
        agg = session.get(Interface, agg_id)
        assert agg is not None and agg.monitored is False

        with pytest.raises(InterfaceNotFoundError, match="does not exist"):
            set_monitored(session, 999999, True)


def test_overview_shows_states_and_relationships(
    db_engine: Engine, device_id: int
) -> None:
    with Session(db_engine) as session:
        agg_id = _iface_id(session, "bridge-aggregation1")
        set_monitored(session, agg_id, True)
        session.commit()

        rows = {row.normalized_name: row for row in interface_overview(session, device_id)}
        assert set(rows) == {
            "bridge-aggregation1",
            "ten-gigabitethernet1/0/49",
            "ten-gigabitethernet2/0/49",
        }
        agg = rows["bridge-aggregation1"]
        assert agg.is_aggregation is True
        assert agg.monitored is True
        assert agg.aggregation_members == ("Ten-GigabitEthernet1/0/49",)
        assert agg.member_of == ()

        member = rows["ten-gigabitethernet1/0/49"]
        assert member.is_aggregation is False
        assert member.monitored is False
        assert member.member_of == ("Bridge-Aggregation1",)


def test_rediscovery_and_ifindex_change_preserve_monitored(
    db_engine: Engine, device_id: int
) -> None:
    """§12: re-discovery / ifIndex change must never lose the selection."""

    with Session(db_engine) as session:
        member_id = _iface_id(session, "ten-gigabitethernet1/0/49")
        set_monitored(session, member_id, True)
        session.commit()

        # Re-discovery: same names, ifIndex changed, description refreshed.
        rediscovered = [
            InterfaceSample(
                if_index=999,  # changed ifIndex
                name="Ten-GigabitEthernet1/0/49",
                normalized_name=normalize_interface_name("Ten-GigabitEthernet1/0/49"),
                description="uplink-to-core",
                admin_state="up",
                oper_state="up",
                speed_bps=10_000_000_000,
                in_octets=None,
                out_octets=None,
                in_errors=None,
                out_errors=None,
                in_discards=None,
                out_discards=None,
            ),
            InterfaceSample(
                if_index=2,
                name="Bridge-Aggregation1",
                normalized_name=normalize_interface_name("Bridge-Aggregation1"),
                description=None,
                admin_state="up",
                oper_state="up",
                speed_bps=20_000_000_000,
                in_octets=None,
                out_octets=None,
                in_errors=None,
                out_errors=None,
                in_discards=None,
                out_discards=None,
            ),
        ]
        report = sync_interfaces(session, device_id, rediscovered, NOW)
        session.commit()
        assert report.updated >= 1

        member = session.get(Interface, member_id)
        assert member is not None
        assert member.monitored is True  # selection survived
        assert member.if_index == 999  # metadata refreshed in place
        assert member.description == "uplink-to-core"
        # A NEW interface appears unmonitored (§12 default).
        new_row = session.execute(
            select(Interface).where(
                Interface.normalized_name == "ten-gigabitethernet2/0/49"
            )
        ).scalar_one()
        assert new_row.monitored is False
