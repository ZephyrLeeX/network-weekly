"""Worker-only inventory refresh against migrated PostgreSQL."""

from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.collect.h3c import oids
from backend.collect.snmp import SnmpVarbind
from backend.db.models import (
    AggregationMember,
    Device,
    DeviceMetric,
    DeviceMonitoringState,
    DevicePollRun,
    DeviceReachabilityIncident,
    Interface,
    InterfaceDiscoveryJob,
    InterfaceMetric,
    InterfaceMonitoringState,
    InterfaceStateIncident,
    IrfMemberObservation,
)
from backend.monitoring import interface_discovery as discovery

pytestmark = pytest.mark.integration


def _device(engine: Engine) -> int:
    with Session(engine) as db:
        device = Device(
            name="discovery-test",
            management_ip="192.0.2.8",
            model_family="s10500x",
            credential_profile="test",
        )
        db.add(device)
        db.commit()
        return device.id


def _fake_client(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> None:
    values: dict[str, list[tuple[int, int | str]]] = {
        oids.IF_DESCR: [(1, "Bridge-Aggr1"), (2, "Ten-GigabitEthernet1/0/1")],
        oids.IF_ALIAS: [(1, "uplink"), (2, "firewall")],
        oids.IF_ADMIN_STATUS: [(1, 1), (2, 1)],
        oids.IF_OPER_STATUS: [(1, 1), (2, 2)],
        oids.IF_SPEED: [(1, 1_000_000_000), (2, 1_000_000_000)],
        oids.IF_HIGH_SPEED: [(1, 10000), (2, 10000)],
        oids.DOT3_AGG_PORT_ATTACHED_AGG_ID: [(2, 1)],
        oids.DOT3_AGG_PORT_SELECTED_AGG_ID: [(2, 0)],
    }

    class FakeClient:
        def __init__(self, config: object) -> None:
            pass

        def bulk_walk(self, column: str) -> list[SnmpVarbind]:
            if fail and column == oids.IF_DESCR:
                raise RuntimeError("community=SECRET")
            return [
                SnmpVarbind(f"{column}.{index}", value) for index, value in values.get(column, [])
            ]

    monkeypatch.setattr(discovery, "SnmpClient", FakeClient)
    monkeypatch.setattr(discovery, "load_secrets", lambda _path: {"SNMP_COMMUNITY_TEST": "SECRET"})


def _count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def test_worker_refresh_is_metadata_only_and_preserves_monitored(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    device_id = _device(db_engine)
    factory = sessionmaker(db_engine)
    _fake_client(monkeypatch)
    with factory() as db:
        job = discovery.request_discovery(db, device_id)
        assert discovery.request_discovery(db, device_id).id == job.id
        db.commit()
        assert _count(db, InterfaceDiscoveryJob) == 1
        untouched = (
            DevicePollRun,
            DeviceMetric,
            InterfaceMetric,
            DeviceMonitoringState,
            DeviceReachabilityIncident,
            InterfaceMonitoringState,
            InterfaceStateIncident,
            IrfMemberObservation,
        )
        before = tuple(_count(db, model) for model in untouched)
    assert discovery.run_next_discovery(factory, Path("unused"))
    with factory() as db:
        rows = db.scalars(
            select(Interface).where(Interface.device_id == device_id).order_by(Interface.if_index)
        ).all()
        assert len(rows) == 2
        assert rows[1].description == "firewall"
        assert rows[1].speed_bps == 10_000_000_000
        assert rows[1].oper_state == "down"
        assert _count(db, AggregationMember) == 1
        assert tuple(_count(db, model) for model in untouched) == before
        assert db.scalar(select(InterfaceDiscoveryJob.status)) == "succeeded"
        rows[1].monitored = True
        db.commit()
        member_id = rows[1].id
    with factory() as db:
        discovery.request_discovery(db, device_id)
        db.commit()
    assert discovery.run_next_discovery(factory, Path("unused"))
    with factory() as db:
        assert db.get(Interface, member_id).monitored  # type: ignore[union-attr]
        assert _count(db, DevicePollRun) == before[0]
        db.query(InterfaceDiscoveryJob).delete()
        db.query(AggregationMember).delete()
        db.query(Interface).filter(Interface.device_id == device_id).delete()
        db.query(Device).filter(Device.id == device_id).delete()
        db.commit()


def test_failure_keeps_existing_inventory_and_sanitizes_error(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    device_id = _device(db_engine)
    factory = sessionmaker(db_engine)
    with factory() as db:
        db.add(
            Interface(
                device_id=device_id,
                normalized_name="existing",
                display_name="Existing",
                monitored=True,
            )
        )
        discovery.request_discovery(db, device_id)
        db.commit()
    _fake_client(monkeypatch, fail=True)
    assert discovery.run_next_discovery(factory, Path("unused"))
    with factory() as db:
        job = db.scalar(select(InterfaceDiscoveryJob))
        assert job is not None and job.status == "failed"
        assert job.last_error == discovery.SAFE_FAILURE and "SECRET" not in job.last_error
        row = db.scalar(select(Interface).where(Interface.device_id == device_id))
        assert row is not None and row.display_name == "Existing" and row.monitored
        db.query(InterfaceDiscoveryJob).delete()
        db.query(Interface).filter(Interface.device_id == device_id).delete()
        db.query(Device).filter(Device.id == device_id).delete()
        db.commit()
