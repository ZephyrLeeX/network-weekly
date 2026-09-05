"""Integration tests for the W02-T001 monitoring pipeline persistence.

Runs `persist_poll_result` against the real migrated PostgreSQL: poll runs
(SUCCESS/PARTIAL/FAILED), device metrics and interface metrics land with
their uniqueness and retention-time columns; one planned cycle can never
produce two poll runs.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from backend.collect.dto import (
    DeviceIdentity,
    EntityLoadSample,
    InterfaceSample,
    normalize_interface_name,
)
from backend.collect.session import DeviceCollectionOutcome, SectionResult
from backend.db.models import (
    Device,
    DeviceMetric,
    DevicePollRun,
    Interface,
    InterfaceMetric,
)
from backend.monitoring.pipeline import persist_poll_result

pytestmark = pytest.mark.integration

CYCLE = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
COLLECTED = datetime(2026, 9, 5, 8, 0, 3, tzinfo=UTC)


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
            name="core-s10500x-01",
            management_ip="192.0.2.11",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        session.add(
            Interface(
                device_id=device.id,
                normalized_name=normalize_interface_name("Ten-GigabitEthernet1/0/49"),
                display_name="Ten-GigabitEthernet1/0/49",
                description="uplink",
            )
        )
        session.add(
            Interface(
                device_id=device.id,
                normalized_name=normalize_interface_name("Bridge-Aggregation1"),
                display_name="Bridge-Aggregation1",
            )
        )
        session.commit()
        return device.id


def _iface_sample(if_index: int, name: str, in_octets: int | None = 100) -> InterfaceSample:
    return InterfaceSample(
        if_index=if_index,
        name=name,
        normalized_name=normalize_interface_name(name),
        description=None,
        admin_state="up",
        oper_state="up",
        speed_bps=10_000_000_000,
        in_octets=in_octets,
        out_octets=200,
        in_errors=0,
        out_errors=0,
        in_discards=0,
        out_discards=0,
        fcs_errors=0,
    )


def _outcome(
    identity: DeviceIdentity | None = None,
    cpu: list[EntityLoadSample] | None = None,
    memory: list[EntityLoadSample] | None = None,
    interfaces: list[InterfaceSample] | None = None,
    sections: list[SectionResult] | None = None,
) -> DeviceCollectionOutcome:
    outcome = DeviceCollectionOutcome(device_name="core-s10500x-01")
    outcome.identity = identity
    outcome.cpu = cpu
    outcome.memory = memory
    outcome.interfaces = interfaces
    if sections is not None:
        outcome.sections = sections
    return outcome


def test_success_cycle_persists_run_and_metrics(db_engine: Engine, device_id: int) -> None:
    outcome = _outcome(
        identity=DeviceIdentity("core", "desc", "1.3.6.1.4.1.25506", 12.0),
        cpu=[EntityLoadSample(entity_index=1, usage_percent=31.0), EntityLoadSample(2, 47.5)],
        memory=[EntityLoadSample(entity_index=1, usage_percent=55.0)],
        interfaces=[
            _iface_sample(49, "Ten-GigabitEthernet1/0/49"),
            _iface_sample(65, "Bridge-Aggregation1"),
        ],
        sections=[
            SectionResult("identity", "SUCCESS"),
            SectionResult("cpu", "SUCCESS"),
            SectionResult("memory", "SUCCESS"),
            SectionResult("interfaces", "SUCCESS"),
        ],
    )
    with Session(db_engine) as session:
        run_id = persist_poll_result(session, device_id, CYCLE, outcome, COLLECTED)
        session.commit()
        assert run_id is not None

        run = session.get(DevicePollRun, run_id)
        assert run is not None
        assert run.status == "SUCCESS"
        assert run.cycle_started_at == CYCLE
        assert run.ssh_reachable is None  # probe never ran on a healthy cycle
        assert run.failed_sections is None

        metric = session.execute(
            select(DeviceMetric).where(DeviceMetric.poll_run_id == run_id)
        ).scalar_one()
        # Device-level value is the peak across reported entities (§14).
        assert metric.cpu_usage_percent == 47.5
        assert metric.memory_usage_percent == 55.0
        assert metric.collected_at == COLLECTED
        assert metric.device_id == device_id

        rows = {
            row.interface_id: row
            for row in session.execute(
                select(InterfaceMetric).where(InterfaceMetric.poll_run_id == run_id)
            )
            .scalars()
            .all()
        }
        assert len(rows) == 2
        uplink = session.execute(
            select(Interface).where(Interface.normalized_name == "ten-gigabitethernet1/0/49")
        ).scalar_one()
        assert rows[uplink.id].in_octets == 100
        assert rows[uplink.id].fcs_errors == 0
        assert rows[uplink.id].oper_state == "up"
        assert rows[uplink.id].speed_bps == 10_000_000_000
        # Utilization computation arrives with W02-T003.
        assert rows[uplink.id].in_utilization_percent is None
        assert rows[uplink.id].utilization_rebaselined is False


def test_partial_cycle_keeps_valid_memory_and_null_cpu(
    db_engine: Engine, device_id: int
) -> None:
    outcome = _outcome(
        memory=[EntityLoadSample(entity_index=1, usage_percent=61.0)],
        sections=[
            SectionResult("cpu", "FAILED", error="SnmpError"),
            SectionResult("memory", "SUCCESS"),
        ],
    )
    with Session(db_engine) as session:
        run_id = persist_poll_result(session, device_id, CYCLE, outcome, COLLECTED)
        session.commit()

        run = session.get(DevicePollRun, run_id)
        assert run is not None
        assert run.status == "PARTIAL"
        assert run.failed_sections == "cpu"
        metric = session.execute(
            select(DeviceMetric).where(DeviceMetric.poll_run_id == run_id)
        ).scalar_one()
        assert metric.cpu_usage_percent is None  # missing stays missing, never 0
        assert metric.memory_usage_percent == 61.0


def test_failed_cycle_persists_run_only(db_engine: Engine, device_id: int) -> None:
    outcome = _outcome(
        sections=[SectionResult("identity", "FAILED", error="SnmpError")],
    )
    outcome.ssh_reachable = False
    with Session(db_engine) as session:
        run_id = persist_poll_result(session, device_id, CYCLE, outcome, COLLECTED)
        session.commit()

        run = session.get(DevicePollRun, run_id)
        assert run is not None
        assert run.status == "FAILED"
        assert run.ssh_reachable is False
        assert session.scalar(select(func.count()).select_from(DeviceMetric)) == 0
        assert session.scalar(select(func.count()).select_from(InterfaceMetric)) == 0


def test_interface_sample_without_interface_row_is_skipped(
    db_engine: Engine, device_id: int
) -> None:
    outcome = _outcome(
        interfaces=[_iface_sample(999, "M-GigabitEthernet9/9/9")],
        sections=[SectionResult("interfaces", "SUCCESS")],
    )
    with Session(db_engine) as session:
        run_id = persist_poll_result(session, device_id, CYCLE, outcome, COLLECTED)
        session.commit()
        assert run_id is not None
        assert session.scalar(select(func.count()).select_from(InterfaceMetric)) == 0
        assert session.get(DevicePollRun, run_id) is not None


def test_same_cycle_is_idempotent(db_engine: Engine, device_id: int) -> None:
    outcome = _outcome(
        cpu=[EntityLoadSample(entity_index=1, usage_percent=20.0)],
        interfaces=[_iface_sample(49, "Ten-GigabitEthernet1/0/49")],
        sections=[SectionResult("cpu", "SUCCESS"), SectionResult("interfaces", "SUCCESS")],
    )
    with Session(db_engine) as session:
        first = persist_poll_result(session, device_id, CYCLE, outcome, COLLECTED)
        second = persist_poll_result(session, device_id, CYCLE, outcome, COLLECTED)
        session.commit()
        assert first == second
        assert session.scalar(select(func.count()).select_from(DevicePollRun)) == 1
        assert session.scalar(select(func.count()).select_from(DeviceMetric)) == 1
        assert session.scalar(select(func.count()).select_from(InterfaceMetric)) == 1


def test_same_cycle_for_different_devices_is_two_runs(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        d1 = Device(
            name="dev-a", management_ip="192.0.2.1", model_family="s10500x",
            credential_profile="default",
        )
        d2 = Device(
            name="dev-b", management_ip="192.0.2.2", model_family="s10500x",
            credential_profile="default",
        )
        session.add_all([d1, d2])
        session.commit()
        ids = (d1.id, d2.id)

    outcome = _outcome(sections=[SectionResult("identity", "SUCCESS")])
    outcome.identity = DeviceIdentity("n", None, None, None)
    with Session(db_engine) as session:
        r1 = persist_poll_result(session, ids[0], CYCLE, outcome, COLLECTED)
        r2 = persist_poll_result(session, ids[1], CYCLE, outcome, COLLECTED)
        session.commit()
        assert r1 is not None and r2 is not None and r1 != r2
        assert session.scalar(select(func.count()).select_from(DevicePollRun)) == 2


def test_unknown_device_returns_none(db_engine: Engine) -> None:
    outcome = _outcome(sections=[SectionResult("identity", "SUCCESS")])
    with Session(db_engine) as session:
        assert persist_poll_result(session, 99999, CYCLE, outcome, COLLECTED) is None
