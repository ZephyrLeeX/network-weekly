"""W02-GATE scenario: one device walked through a full monitoring cycle.

Consecutive polls drive the whole Wave 2 pipeline end-to-end against real
PostgreSQL: SUCCESS/PARTIAL/FAILED runs, utilization + counter reset
rebaseline, 2-cycle device DOWN/RECOVERED, sustained-high detection, and
retention that spares the incident record.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.collect.dto import (
    DeviceIdentity,
    EntityLoadSample,
    InterfaceSample,
    normalize_interface_name,
)
from backend.collect.session import DeviceCollectionOutcome, SectionResult
from backend.collect.snmp import SnmpConfig
from backend.db.models import (
    Device,
    DeviceMetric,
    DevicePollRun,
    DeviceReachabilityIncident,
    Interface,
    InterfaceMetric,
)
from backend.monitoring import poll as poll_module
from backend.monitoring.pipeline import persist_poll_result
from backend.monitoring.poll import DevicePollContext, poll_device
from backend.monitoring.retention import run_retention
from backend.monitoring.thresholds import CPU_KEY, detect_device_sustained_high

pytestmark = pytest.mark.integration

STEP = timedelta(seconds=300)
T0 = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        for model in (
            InterfaceMetric,
            DeviceMetric,
            DevicePollRun,
            DeviceReachabilityIncident,
            Interface,
            Device,
        ):
            session.query(model).delete()
        session.commit()
    yield


@pytest.fixture
def device_id(db_engine: Engine) -> int:
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.91",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        session.add(
            Interface(
                device_id=device.id,
                normalized_name="ten-gigabitethernet1/0/1",
                display_name="Ten-GigabitEthernet1/0/1",
            )
        )
        session.commit()
        return device.id


def _sample(in_octets: int, oper: str = "up") -> InterfaceSample:
    return InterfaceSample(
        if_index=1,
        name="Ten-GigabitEthernet1/0/1",
        normalized_name=normalize_interface_name("Ten-GigabitEthernet1/0/1"),
        description=None,
        admin_state="up",
        oper_state=oper,
        speed_bps=10_000_000_000,
        in_octets=in_octets,
        out_octets=None,
        in_errors=0,
        out_errors=0,
        in_discards=0,
        out_discards=0,
        fcs_errors=0,
    )


def _identity(cpu: float) -> tuple[DeviceIdentity, list[EntityLoadSample]]:
    return DeviceIdentity("core-1", "H3C Comware", None, None), [
        EntityLoadSample(entity_index=1, usage_percent=cpu)
    ]


def test_gate_scenario_success_failure_down_recovery_rebaseline(
    db_engine: Engine, device_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = sessionmaker(bind=db_engine)
    context = DevicePollContext(
        device_id=device_id,
        device_name="core-1",
        snmp=SnmpConfig(host="192.0.2.91", community="public"),
        ssh=None,
    )

    def ok(in_octets: int, cpu: float) -> DeviceCollectionOutcome:
        identity, cpu_samples = _identity(cpu)
        return DeviceCollectionOutcome(
            device_name="core-1",
            sections=[
                SectionResult("identity", "SUCCESS"),
                SectionResult("cpu", "SUCCESS"),
                SectionResult("interfaces", "SUCCESS"),
            ],
            identity=identity,
            cpu=cpu_samples,
            interfaces=[_sample(in_octets)],
        )

    def dead(ssh_reachable: bool) -> DeviceCollectionOutcome:
        return DeviceCollectionOutcome(
            device_name="core-1",
            sections=[SectionResult("identity", "FAILED", error="SnmpError")],
            ssh_reachable=ssh_reachable,
        )

    script = iter(
        [
            ok(1_000, 30.0),  # t0: baseline cycle
            ok(1_000 + 300_000_000_000, 30.0),  # t1: valid delta (80%)
            ok(500, 30.0),  # t2: counter reset -> rebaseline
            dead(False),  # t3: failed cycle 1
            dead(False),  # t4: failed cycle 2 -> DOWN
            ok(500, 30.0),  # t5: reachable again (recovery run 1/2)
            ok(500, 95.0),  # t6: recovery run 2/2 -> RECOVERED
        ]
    )
    monkeypatch.setattr(poll_module, "run_collection", lambda *a, **k: next(script))

    cycles = [T0 + i * STEP for i in range(7)]
    for cycle in cycles:
        status = poll_device(context, cycle, session_factory=factory, now=cycle)
        print(f"cycle {cycle}: {status}")  # noqa: T201  (scenario log for the gate)

    with Session(db_engine) as session:
        # 7 planned cycles -> 7 poll runs, statuses per §8.
        statuses = [
            row.status
            for row in session.execute(
                select(DevicePollRun.status).order_by(DevicePollRun.cycle_started_at)
            ).all()
        ]
        assert statuses == [
            "SUCCESS", "SUCCESS", "SUCCESS", "FAILED", "FAILED", "SUCCESS", "SUCCESS",
        ]

        # Utilization: baseline -> 80% over the real 300 s -> reset rebaseline.
        utilizations = [
            row.in_utilization_percent
            for row in session.execute(
                select(InterfaceMetric)
                .order_by(InterfaceMetric.collected_at)
            ).scalars()
        ]
        assert utilizations[0] is None  # baseline
        assert utilizations[1] == pytest.approx(80.0)  # actual elapsed + speed
        assert utilizations[2] is None  # counter reset: rebaseline, no spike

        # §9.2: two consecutive failed cycles confirm DOWN, started at t3.
        incident = session.execute(select(DeviceReachabilityIncident)).scalar_one()
        assert incident.started_at == T0 + 3 * STEP
        # §9.3: two consecutive reachable cycles confirm RECOVERED at t5.
        assert incident.recovered_at == T0 + 5 * STEP

        # §14: CPU samples 30/30/30/MISSING(t3,t4)/30/95 — 95 alone confirms
        # nothing; at the configured 80% threshold nothing sustains.
        from backend.monitoring.thresholds import REQUIRED_SAMPLES_KEY, defaults

        thresholds = defaults() | {CPU_KEY: 80.0, REQUIRED_SAMPLES_KEY: 3.0}
        result = detect_device_sustained_high(
            session, device_id, T0, T0 + 8 * STEP, thresholds=thresholds
        )
        assert result["cpu"] == []

        # Missing cycles (FAILED) leave NO device_metrics rows.
        assert session.scalar(select(func.count()).select_from(DeviceMetric)) == 5


def test_gate_scenario_retention_keeps_incident_deletes_raw(
    db_engine: Engine, device_id: int
) -> None:
    """90-day retention spares the long-term incident, deletes old runs."""

    with Session(db_engine) as session:
        session.add(
            DeviceReachabilityIncident(
                device_id=device_id,
                started_at=datetime(2026, 5, 1, tzinfo=UTC),
                recovered_at=datetime(2026, 5, 1, 0, 10, tzinfo=UTC),
            )
        )
        outcome = DeviceCollectionOutcome(
            device_name="core-1",
            sections=[SectionResult("identity", "SUCCESS")],
        )
        old_cycle = datetime(2026, 5, 1, tzinfo=UTC)  # >90 days before NOW
        persist_poll_result(session, device_id, old_cycle, outcome, old_cycle)
        session.commit()

        report = run_retention(
            sessionmaker(bind=db_engine), now=datetime(2026, 9, 5, tzinfo=UTC)
        )
        assert report.poll_runs_deleted == 1

        # The incident survived (§24: long-term).
        assert (
            session.execute(select(func.count()).select_from(DeviceReachabilityIncident))
            .scalar_one()
            == 1
        )
        assert session.scalar(select(func.count()).select_from(DevicePollRun)) == 0
