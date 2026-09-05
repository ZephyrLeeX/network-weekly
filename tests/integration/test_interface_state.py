"""Integration tests for priority-interface Down/Recovery persistence
(W02-T006) and the pipeline wiring that feeds it.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from backend.collect.dto import InterfaceSample, normalize_interface_name
from backend.collect.session import DeviceCollectionOutcome, SectionResult
from backend.collect.snmp import SnmpConfig
from backend.db.models import (
    Device,
    DeviceMetric,
    DevicePollRun,
    Interface,
    InterfaceMetric,
    InterfaceMonitoringState,
    InterfaceStateIncident,
)
from backend.monitoring import poll as poll_module
from backend.monitoring.interface_state import (
    apply_interface_state,
    record_interface_states,
)
from backend.monitoring.poll import DevicePollContext

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
            InterfaceStateIncident,
            InterfaceMonitoringState,
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
            management_ip="192.0.2.51",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        session.add_all(
            [
                Interface(
                    device_id=device.id,
                    normalized_name="ten-gigabitethernet1/0/1",
                    display_name="Ten-GigabitEthernet1/0/1",
                    monitored=True,
                ),
                Interface(
                    device_id=device.id,
                    normalized_name="ten-gigabitethernet1/0/2",
                    display_name="Ten-GigabitEthernet1/0/2",
                    monitored=False,
                ),
            ]
        )
        session.commit()
        return device.id


def _sample(name: str, oper_state: str | None) -> InterfaceSample:
    return InterfaceSample(
        if_index=1,
        name=name,
        normalized_name=normalize_interface_name(name),
        description=None,
        admin_state="up",
        oper_state=oper_state,
        speed_bps=10_000_000_000,
        in_octets=None,
        out_octets=None,
        in_errors=None,
        out_errors=None,
        in_discards=None,
        out_discards=None,
    )


def _interface_by_name(session: Session, normalized_name: str) -> Interface:
    return session.execute(
        select(Interface).where(Interface.normalized_name == normalized_name)
    ).scalar_one()


def test_apply_down_then_recovered_persists_incident(
    db_engine: Engine, device_id: int
) -> None:
    with Session(db_engine) as session:
        iface = _interface_by_name(session, "ten-gigabitethernet1/0/1")

        d1 = apply_interface_state(session, iface, "down", T0)
        assert d1.incident_started_at is None
        d2 = apply_interface_state(session, iface, "down", T0 + STEP)
        assert d2.incident_started_at == T0
        session.commit()

        incident = session.execute(select(InterfaceStateIncident)).scalar_one()
        assert incident.interface_id == iface.id
        assert incident.started_at == T0
        assert incident.recovered_at is None

        r1 = apply_interface_state(session, iface, "up", T0 + 2 * STEP)
        assert r1.incident_recovered_at is None
        r2 = apply_interface_state(session, iface, "up", T0 + 3 * STEP)
        assert r2.incident_recovered_at == T0 + 2 * STEP
        session.commit()

        session.refresh(incident)
        assert incident.recovered_at == T0 + 2 * STEP


def test_non_monitored_interfaces_are_ignored(db_engine: Engine, device_id: int) -> None:
    with Session(db_engine) as session:
        processed = record_interface_states(
            session,
            device_id,
            [_sample("Ten-GigabitEthernet1/0/2", "down")],
            T0,
        )
        session.commit()
        assert processed == 0
        assert session.scalar(select(func.count()).select_from(InterfaceMonitoringState)) == 0


def test_undetermined_sample_not_counted(db_engine: Engine, device_id: int) -> None:
    with Session(db_engine) as session:
        iface = _interface_by_name(session, "ten-gigabitethernet1/0/1")
        apply_interface_state(session, iface, "down", T0)
        # lowerlayerdown is not "down": it must not advance the count.
        d = apply_interface_state(session, iface, "lowerlayerdown", T0 + STEP)
        assert d.tracking.consecutive_down_samples == 1
        session.commit()
        assert session.execute(select(InterfaceStateIncident)).scalar_one_or_none() is None


def test_pipeline_feeds_interface_state_machine(
    db_engine: Engine, device_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two cycles reporting the monitored port Down confirm the incident."""

    from sqlalchemy.orm import sessionmaker

    def outcome_for(oper: str | None) -> DeviceCollectionOutcome:
        return DeviceCollectionOutcome(
            device_name="core-1",
            sections=[SectionResult("interfaces", "SUCCESS")],
            interfaces=[_sample("Ten-GigabitEthernet1/0/1", oper)],
        )

    outcomes = iter([outcome_for("down"), outcome_for("down")])
    monkeypatch.setattr(poll_module, "run_collection", lambda *a, **k: next(outcomes))
    context = DevicePollContext(
        device_id=device_id,
        device_name="core-1",
        snmp=SnmpConfig(host="192.0.2.51", community="public"),
        ssh=None,
    )

    poll_module.poll_device(
        context, T0, session_factory=sessionmaker(bind=db_engine), now=T0
    )
    poll_module.poll_device(
        context, T0 + STEP, session_factory=sessionmaker(bind=db_engine), now=T0 + STEP
    )

    with Session(db_engine) as session:
        incident = session.execute(select(InterfaceStateIncident)).scalar_one()
        iface = _interface_by_name(session, "ten-gigabitethernet1/0/1")
        assert incident.interface_id == iface.id
        assert incident.started_at == T0
        assert incident.recovered_at is None
        state = session.get(InterfaceMonitoringState, iface.id)
        assert state is not None and state.state == "down"
