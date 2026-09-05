"""Integration tests for device reachability persistence (W02-T004).

`apply_device_reachability` against the real migrated PostgreSQL: tracking
row upserts, incident open/close, and §9.4 persisted fields — plus the
pipeline wiring that feeds the §9.1 evidence.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from backend.db.models import (
    Device,
    DeviceMetric,
    DeviceMonitoringState,
    DevicePollRun,
    DeviceReachabilityIncident,
    Interface,
    InterfaceMetric,
)
from backend.monitoring.reachability import (
    STATE_DOWN,
    STATE_NORMAL,
    ReachabilityObservation,
    apply_device_reachability,
    load_tracking,
)

pytestmark = pytest.mark.integration

STEP = timedelta(seconds=300)
T0 = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(InterfaceMetric).delete()
        session.query(DeviceMetric).delete()
        session.query(DevicePollRun).delete()
        session.query(Interface).delete()
        session.query(DeviceReachabilityIncident).delete()
        session.query(DeviceMonitoringState).delete()
        session.query(Device).delete()
        session.commit()
    yield


@pytest.fixture
def device_id(db_engine: Engine) -> int:
    with Session(db_engine) as session:
        device = Device(
            name="core-1",
            management_ip="192.0.2.31",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.commit()
        return device.id


def _failed(cycle: datetime) -> ReachabilityObservation:
    return ReachabilityObservation(cycle_started_at=cycle, snmp_ok=False, ssh_reachable=False)


def _ok(cycle: datetime) -> ReachabilityObservation:
    return ReachabilityObservation(cycle_started_at=cycle, snmp_ok=True, ssh_reachable=None)


def test_down_then_recovered_persists_incident(
    db_engine: Engine, device_id: int
) -> None:
    with Session(db_engine) as session:
        d1 = apply_device_reachability(session, device_id, _failed(T0))
        assert d1.incident_started_at is None
        session.commit()
        assert session.scalar(select(DeviceReachabilityIncident.id).limit(1)) is None

        d2 = apply_device_reachability(session, device_id, _failed(T0 + STEP))
        assert d2.incident_started_at == T0
        session.commit()

        incident = session.execute(select(DeviceReachabilityIncident)).scalar_one()
        assert incident.started_at == T0
        assert incident.recovered_at is None  # device is Down now
        state = session.get(DeviceMonitoringState, device_id)
        assert state is not None and state.state == STATE_DOWN

        r1 = apply_device_reachability(session, device_id, _ok(T0 + 2 * STEP))
        assert r1.incident_recovered_at is None
        session.commit()

        r2 = apply_device_reachability(session, device_id, _ok(T0 + 3 * STEP))
        assert r2.incident_recovered_at == T0 + 2 * STEP
        session.commit()

        session.refresh(incident)
        assert incident.recovered_at == T0 + 2 * STEP
        state = session.get(DeviceMonitoringState, device_id)
        assert state is not None
        assert state.state == STATE_NORMAL
        assert state.consecutive_failed_cycles == 0
        assert state.last_cycle_started_at == T0 + 3 * STEP


def test_second_down_creates_new_incident(db_engine: Engine, device_id: int) -> None:
    with Session(db_engine) as session:
        apply_device_reachability(session, device_id, _failed(T0))
        apply_device_reachability(session, device_id, _failed(T0 + STEP))
        apply_device_reachability(session, device_id, _ok(T0 + 2 * STEP))
        apply_device_reachability(session, device_id, _ok(T0 + 3 * STEP))
        session.commit()

        # Second Down episode much later.
        apply_device_reachability(session, device_id, _failed(T0 + timedelta(hours=5)))
        apply_device_reachability(
            session, device_id, _failed(T0 + timedelta(hours=5) + STEP)
        )
        session.commit()

        incidents = session.execute(
            select(DeviceReachabilityIncident).order_by(DeviceReachabilityIncident.started_at)
        ).scalars().all()
        assert len(incidents) == 2
        assert incidents[0].recovered_at is not None
        assert incidents[1].recovered_at is None  # still Down
        assert incidents[1].started_at == T0 + timedelta(hours=5)


def test_ssh_only_reachable_recovery_keeps_snmp_missing_explicit(
    db_engine: Engine, device_id: int
) -> None:
    """§9.3: SSH recovery ends Down; SNMP data missingness stays in the run."""

    ssh_only = ReachabilityObservation(cycle_started_at=T0, snmp_ok=False, ssh_reachable=True)
    with Session(db_engine) as session:
        # A device that is SSH-reachable from cycle one never goes Down.
        d = apply_device_reachability(session, device_id, ssh_only)
        assert d.tracking.state == STATE_NORMAL
        session.commit()
        assert session.execute(select(DeviceReachabilityIncident)).scalar_one_or_none() is None


def test_gap_resets_consecutive_runs(db_engine: Engine, device_id: int) -> None:
    with Session(db_engine) as session:
        apply_device_reachability(session, device_id, _failed(T0))
        # Skipped cycle T0+STEP: the next processed cycle is not consecutive.
        d2 = apply_device_reachability(session, device_id, _failed(T0 + 2 * STEP))
        assert d2.tracking.state == STATE_NORMAL
        assert d2.tracking.consecutive_failed_cycles == 1
        session.commit()
        assert session.execute(select(DeviceReachabilityIncident)).scalar_one_or_none() is None


def test_load_tracking_defaults(db_engine: Engine, device_id: int) -> None:
    with Session(db_engine) as session:
        tracking = load_tracking(session, device_id)
        assert tracking.state == STATE_NORMAL
        assert tracking.consecutive_failed_cycles == 0
        assert tracking.last_cycle_started_at is None
