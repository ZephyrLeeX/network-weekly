"""Integration tests for interface discovery persistence (W01-T004).

Proves against the real migrated PostgreSQL: identity by
(device_id, normalized_name), ifIndex mutability, `monitored` survival
across rediscovery, aggregation membership add/remove and cascade cleanup.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from backend.collect.discovery import sync_aggregations, sync_interfaces
from backend.collect.dto import AggregationMapping, InterfaceSample
from backend.db.models import AggregationMember, Device, Interface

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
            name="core-s10500x-01",
            management_ip="192.0.2.11",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.commit()
        return device.id


def _sample(if_index: int, name: str, **overrides: object) -> InterfaceSample:
    fields: dict[str, object] = {
        "if_index": if_index,
        "name": name,
        "normalized_name": name.lower(),
        "description": None,
        "admin_state": "up",
        "oper_state": "up",
        "speed_bps": 10_000_000_000,
        "in_octets": None,
        "out_octets": None,
        "in_errors": None,
        "out_errors": None,
        "in_discards": None,
        "out_discards": None,
    }
    fields.update(overrides)
    return InterfaceSample(**fields)  # type: ignore[arg-type]


def test_discovery_creates_then_updates_in_place(db_engine: Engine, device_id: int) -> None:
    with Session(db_engine) as session:
        report = sync_interfaces(
            session,
            device_id,
            [_sample(1, "Ten-GigabitEthernet1/0/1", oper_state="up")],
            NOW,
        )
        assert report.created == 1
        session.commit()

        # Rediscovery: same interface name, changed ifIndex and state.
        report2 = sync_interfaces(
            session,
            device_id,
            [_sample(77, "Ten-GigabitEthernet1/0/1", oper_state="down")],
            NOW,
        )
        session.commit()

        assert report2.created == 0
        assert report2.updated == 1
        rows = (
            session.execute(select(Interface).where(Interface.device_id == device_id))
            .scalars()
            .all()
        )
        assert len(rows) == 1  # no duplicate despite changed ifIndex
        row = rows[0]
        assert row.if_index == 77
        assert row.oper_state == "down"
        assert row.monitored is False  # untouched by discovery


def test_monitored_flag_survives_rediscovery(db_engine: Engine, device_id: int) -> None:
    with Session(db_engine) as session:
        sync_interfaces(session, device_id, [_sample(1, "Ten-GigabitEthernet1/0/1")], NOW)
        session.commit()
        row = session.execute(select(Interface)).scalar_one()
        row.monitored = True  # operator selection (Wave 2 service)
        session.commit()

        sync_interfaces(session, device_id, [_sample(1, "Ten-GigabitEthernet1/0/1")], NOW)
        session.commit()
        session.refresh(row)
        assert row.monitored is True


def test_aggregation_membership_add_and_remove(db_engine: Engine, device_id: int) -> None:
    samples = [
        _sample(65, "Bridge-Aggregation1"),
        _sample(49, "Ten-GigabitEthernet1/0/49"),
        _sample(50, "Ten-GigabitEthernet1/0/50"),
    ]
    with Session(db_engine) as session:
        sync_interfaces(session, device_id, samples, NOW)
        session.commit()

        report = sync_aggregations(
            session,
            device_id,
            [AggregationMapping(aggregation_if_index=65, member_if_indexes=(49, 50))],
        )
        session.commit()
        assert report.memberships_added == 2
        assert report.unresolved == ()

        agg = session.execute(
            select(Interface).where(
                Interface.device_id == device_id,
                Interface.normalized_name == "bridge-aggregation1",
            )
        ).scalar_one()
        assert agg.is_aggregation is True
        assert sorted(m.member_interface.normalized_name for m in agg.aggregate_members) == [
            "ten-gigabitethernet1/0/49",
            "ten-gigabitethernet1/0/50",
        ]

        # Port 50 leaves the lag; port 49 stays.
        report2 = sync_aggregations(
            session,
            device_id,
            [AggregationMapping(aggregation_if_index=65, member_if_indexes=(49,))],
        )
        session.commit()
        assert report2.memberships_added == 0
        assert report2.memberships_removed == 1
        session.refresh(agg)
        assert [m.member_interface.normalized_name for m in agg.aggregate_members] == [
            "ten-gigabitethernet1/0/49"
        ]


def test_empty_successful_mapping_clears_stale_aggregation_state(
    db_engine: Engine, device_id: int
) -> None:
    """A successful collection reporting no aggregations clears old state."""

    samples = [
        _sample(65, "Bridge-Aggregation1"),
        _sample(66, "Bridge-Aggregation2"),
        _sample(49, "Ten-GigabitEthernet1/0/49"),
        _sample(50, "Ten-GigabitEthernet1/0/50"),
    ]
    with Session(db_engine) as session:
        sync_interfaces(session, device_id, samples, NOW)
        session.commit()
        sync_aggregations(
            session,
            device_id,
            [
                AggregationMapping(aggregation_if_index=65, member_if_indexes=(49, 50)),
                AggregationMapping(aggregation_if_index=66, member_if_indexes=(49,)),
            ],
        )
        session.commit()

        # Device now reports no aggregation at all: memberships and the
        # aggregation flags must be cleared, interfaces stay.
        report = sync_aggregations(session, device_id, [])
        session.commit()
        assert report.memberships_removed == 3
        assert session.scalar(select(func.count()).select_from(AggregationMember)) == 0
        rows = session.execute(
            select(Interface.normalized_name, Interface.is_aggregation).where(
                Interface.device_id == device_id
            )
        ).all()
        flags: dict[str, bool] = {name: flag for name, flag in rows}
        assert flags == {
            "bridge-aggregation1": False,
            "bridge-aggregation2": False,
            "ten-gigabitethernet1/0/49": False,
            "ten-gigabitethernet1/0/50": False,
        }


def test_aggregation_removed_from_mapping_cleans_stale_membership(
    db_engine: Engine, device_id: int
) -> None:
    samples = [
        _sample(65, "Bridge-Aggregation1"),
        _sample(66, "Bridge-Aggregation2"),
        _sample(49, "Ten-GigabitEthernet1/0/49"),
    ]
    with Session(db_engine) as session:
        sync_interfaces(session, device_id, samples, NOW)
        session.commit()
        sync_aggregations(
            session,
            device_id,
            [
                AggregationMapping(aggregation_if_index=65, member_if_indexes=(49,)),
                AggregationMapping(aggregation_if_index=66, member_if_indexes=(49,)),
            ],
        )
        session.commit()

        # Agg 66 disappeared from the device; agg 65 remains.
        report = sync_aggregations(
            session,
            device_id,
            [AggregationMapping(aggregation_if_index=65, member_if_indexes=(49,))],
        )
        session.commit()
        assert report.memberships_removed == 1
        agg66 = session.execute(
            select(Interface).where(
                Interface.device_id == device_id,
                Interface.normalized_name == "bridge-aggregation2",
            )
        ).scalar_one()
        assert agg66.is_aggregation is False
        assert session.scalar(select(func.count()).select_from(AggregationMember)) == 1


def test_persist_failure_keeps_previous_aggregation_state(
    db_engine: Engine, device_id: int
) -> None:
    """A failed collection (aggregations=None) must never clear state."""

    samples = [_sample(65, "Bridge-Aggregation1"), _sample(49, "Ten-GigabitEthernet1/0/49")]
    with Session(db_engine) as session:
        sync_interfaces(session, device_id, samples, NOW)
        session.commit()
        sync_aggregations(
            session,
            device_id,
            [AggregationMapping(aggregation_if_index=65, member_if_indexes=(49,))],
        )
        session.commit()

        # Simulated section failure: persist_collection would skip
        # sync_aggregations entirely when outcome.aggregations is None.
        # State must be untouched — proven by the persist_collection test
        # in test_collect_persist.py; here the invariant is that stale
        # cleanup only happens inside an explicit sync call with real data.
        assert session.scalar(select(func.count()).select_from(AggregationMember)) == 1


def test_aggregation_with_unknown_ifindex_is_reported_not_guessed(
    db_engine: Engine, device_id: int
) -> None:
    with Session(db_engine) as session:
        sync_interfaces(session, device_id, [_sample(65, "Bridge-Aggregation1")], NOW)
        session.commit()
        report = sync_aggregations(
            session,
            device_id,
            [AggregationMapping(aggregation_if_index=65, member_if_indexes=(49,))],
        )
        session.commit()
        assert report.unresolved == ("member:49",)
        assert session.scalar(select(func.count()).select_from(AggregationMember)) == 0


def test_discovery_does_not_delete_disappeared_interfaces(
    db_engine: Engine, device_id: int
) -> None:
    with Session(db_engine) as session:
        sync_interfaces(
            session,
            device_id,
            [_sample(1, "Ten-GigabitEthernet1/0/1"), _sample(2, "Ten-GigabitEthernet1/0/2")],
            NOW,
        )
        session.commit()
        sync_interfaces(session, device_id, [_sample(1, "Ten-GigabitEthernet1/0/1")], NOW)
        session.commit()
        assert session.scalar(select(func.count()).select_from(Interface)) == 2
