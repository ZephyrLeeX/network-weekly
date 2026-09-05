"""Integration test: one full DEVICE_POLL cycle persisted end-to-end (W02-T002).

`poll_device` is run with a canned Wave 1 collection outcome (monkeypatched
`run_collection` — no real device here) against the real migrated PostgreSQL,
proving the scheduler's poll action writes topology, poll run and metrics in
one transaction.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.collect.dto import DeviceIdentity
from backend.collect.session import DeviceCollectionOutcome, SectionResult
from backend.collect.snmp import SnmpConfig
from backend.db.models import (
    Device,
    DeviceMetric,
    DevicePollRun,
    Interface,
    InterfaceMetric,
)
from backend.monitoring import poll as poll_module
from backend.monitoring.poll import DevicePollContext, poll_device

pytestmark = pytest.mark.integration

CYCLE = datetime(2026, 9, 5, 9, 5, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 5, 9, 5, 4, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(InterfaceMetric).delete()
        session.query(DeviceMetric).delete()
        session.query(DevicePollRun).delete()
        session.query(Interface).delete()
        session.query(Device).delete()
        session.commit()
    yield


@pytest.fixture
def device_id(db_engine: Engine) -> int:
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.21",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.commit()
        return device.id


def _context(device_id: int) -> DevicePollContext:
    return DevicePollContext(
        device_id=device_id,
        device_name="core-1",
        snmp=SnmpConfig(host="192.0.2.21", community="public"),
        ssh=None,
    )


def test_poll_device_persists_whole_cycle(
    db_engine: Engine, device_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = DeviceCollectionOutcome(
        device_name="core-1",
        sections=[SectionResult("identity", "SUCCESS"), SectionResult("cpu", "SUCCESS")],
        identity=DeviceIdentity("core-1", "desc", None, 5.0),
        cpu=[],
    )
    monkeypatch.setattr(poll_module, "run_collection", lambda *a, **k: outcome)

    status = poll_device(
        _context(device_id),
        CYCLE,
        session_factory=sessionmaker(bind=db_engine),
        now=NOW,
    )
    assert status == "SUCCESS"

    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(DevicePollRun)) == 1
        run = session.execute(select(DevicePollRun)).scalar_one()
        assert run.status == "SUCCESS"
        assert run.cycle_started_at == CYCLE
        # Topology sync ran as part of the same pipeline call.
        device = session.get(Device, device_id)
        assert device is not None
        assert device.sys_name == "core-1"
        # The canned outcome has CPU data but no entities: the cycle is a
        # SUCCESS run and the CPU series stays honestly empty.
        assert session.scalar(select(func.count()).select_from(DeviceMetric)) == 0
        assert session.scalar(select(func.count()).select_from(InterfaceMetric)) == 0


def test_poll_device_failed_collection_still_records_run(
    db_engine: Engine, device_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = DeviceCollectionOutcome(
        device_name="core-1",
        sections=[SectionResult("identity", "FAILED", error="SnmpError")],
        ssh_reachable=False,
    )
    monkeypatch.setattr(poll_module, "run_collection", lambda *a, **k: outcome)

    status = poll_device(
        _context(device_id), CYCLE, session_factory=sessionmaker(bind=db_engine)
    )
    assert status == "FAILED"

    with Session(db_engine) as session:
        run = session.execute(select(DevicePollRun)).scalar_one()
        assert run.status == "FAILED"
        assert run.ssh_reachable is False
        assert run.failed_sections == "identity"
        device = session.get(Device, device_id)
        assert device is not None
        assert device.sys_name is None  # nothing valid: no identity overwrite
