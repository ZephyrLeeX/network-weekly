"""Integration test: one full DEVICE_POLL cycle persisted end-to-end (W02-T002).

`poll_device` is run with a canned Wave 1 collection outcome (monkeypatched
`run_collection` — no real device here) against the real migrated PostgreSQL,
proving the scheduler's poll action writes topology, poll run and metrics in
one transaction. The W02 audit adds: whole-cycle idempotency (a replayed
`(device, cycle)` never re-advances the §9/§13 state machines) and the §8
guarantee that unattemptable cycles (overlap skip, missing credentials)
still land a FAILED poll run.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.collect.dto import DeviceIdentity, EntityLoadSample, InterfaceSample
from backend.collect.session import DeviceCollectionOutcome, SectionResult
from backend.collect.snmp import SnmpConfig
from backend.db.models import (
    Device,
    DeviceMetric,
    DeviceMonitoringState,
    DevicePollRun,
    DeviceReachabilityIncident,
    Interface,
    InterfaceMetric,
    InterfaceMonitoringState,
    InterfaceStateIncident,
)
from backend.monitoring import poll as poll_module
from backend.monitoring.poll import (
    DevicePollContext,
    poll_device,
    record_skipped_poll,
)

pytestmark = pytest.mark.integration

CYCLE = datetime(2026, 9, 5, 9, 5, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 5, 9, 5, 4, tzinfo=UTC)
STEP = timedelta(seconds=300)


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


def _dead(device_name: str, ssh_reachable: bool) -> DeviceCollectionOutcome:
    return DeviceCollectionOutcome(
        device_name=device_name,
        sections=[SectionResult("identity", "FAILED", error="SnmpError")],
        ssh_reachable=ssh_reachable,
    )


def _ok_with_cpu(device_name: str) -> DeviceCollectionOutcome:
    return DeviceCollectionOutcome(
        device_name=device_name,
        sections=[SectionResult("identity", "SUCCESS"), SectionResult("cpu", "SUCCESS")],
        identity=DeviceIdentity(device_name, None, None, 5.0),
        cpu=[EntityLoadSample(entity_index=1, usage_percent=31.0)],
    )


def test_duplicate_cycle_is_fully_idempotent(
    db_engine: Engine, device_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reprocessing (device, cycle) must not re-advance the state machines.

    One real failed cycle + a replay of the same cycle must NOT confirm
    DOWN — only a second, distinct failed cycle may (§9.2).
    """

    factory = sessionmaker(bind=db_engine)
    context = _context(device_id)
    calls = []

    def scripted(*args: object, **kwargs: object) -> DeviceCollectionOutcome:
        calls.append(args)
        return _dead("core-1", ssh_reachable=False)

    monkeypatch.setattr(poll_module, "run_collection", scripted)

    assert poll_device(context, CYCLE, session_factory=factory, now=NOW) == "FAILED"
    # Replay of the very same planned cycle (e.g. after a restart race):
    assert poll_device(context, CYCLE, session_factory=factory, now=NOW) == "FAILED"

    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(DevicePollRun)) == 1
        tracking = session.get(DeviceMonitoringState, device_id)
        assert tracking is not None
        # The replay did not count as a second failed cycle.
        assert tracking.consecutive_failed_cycles == 1
        assert tracking.last_cycle_started_at == CYCLE
        assert session.scalar(
            select(func.count()).select_from(DeviceReachabilityIncident)
        ) == 0

    # The replay never even reached the collection orchestration.
    assert len(calls) == 1

    # A genuinely distinct failed cycle still confirms DOWN, anchored at the
    # first processed failed cycle — not inflated by the replay.
    cycle2 = CYCLE + STEP
    assert poll_device(context, cycle2, session_factory=factory, now=NOW) == "FAILED"
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(DevicePollRun)) == 2
        incident = session.execute(select(DeviceReachabilityIncident)).scalar_one()
        assert incident.started_at == CYCLE
        assert incident.recovered_at is None


def test_duplicate_cycle_does_not_duplicate_metric_rows(
    db_engine: Engine, device_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = sessionmaker(bind=db_engine)
    outcome = _ok_with_cpu("core-1")
    monkeypatch.setattr(poll_module, "run_collection", lambda *a, **k: outcome)

    assert poll_device(_context(device_id), CYCLE, session_factory=factory) == "SUCCESS"
    assert poll_device(_context(device_id), CYCLE, session_factory=factory) == "SUCCESS"

    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(DevicePollRun)) == 1
        assert session.scalar(select(func.count()).select_from(DeviceMetric)) == 1


def test_duplicate_cycle_does_not_confirm_interface_down(
    db_engine: Engine, device_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One Down sample replayed must not confirm interface DOWN (§13.1)."""

    with Session(db_engine) as session:
        session.add(
            Interface(
                device_id=device_id,
                normalized_name="ten-gigabitethernet1/0/1",
                display_name="Ten-GigabitEthernet1/0/1",
                monitored=True,
            )
        )
        session.commit()

    def down_sample(*args: object, **kwargs: object) -> DeviceCollectionOutcome:
        return DeviceCollectionOutcome(
            device_name="core-1",
            sections=[SectionResult("interfaces", "SUCCESS")],
            interfaces=[
                InterfaceSample(
                    if_index=1,
                    name="Ten-GigabitEthernet1/0/1",
                    normalized_name="ten-gigabitethernet1/0/1",
                    description=None,
                    admin_state="up",
                    oper_state="down",
                    speed_bps=10_000_000_000,
                    in_octets=None,
                    out_octets=None,
                    in_errors=None,
                    out_errors=None,
                    in_discards=None,
                    out_discards=None,
                )
            ],
        )

    factory = sessionmaker(bind=db_engine)
    monkeypatch.setattr(poll_module, "run_collection", down_sample)

    assert poll_device(_context(device_id), CYCLE, session_factory=factory) == "SUCCESS"
    assert poll_device(_context(device_id), CYCLE, session_factory=factory) == "SUCCESS"

    with Session(db_engine) as session:
        tracking = session.get(InterfaceMonitoringState, session.scalars(
            select(Interface.id).where(Interface.device_id == device_id)
        ).one())
        assert tracking is not None
        assert tracking.consecutive_down_samples == 1  # replay did not count
        assert (
            session.scalar(select(func.count()).select_from(InterfaceStateIncident)) == 0
        )


def test_missing_credentials_records_failed_run_without_state_advance(
    db_engine: Engine, device_id: int
) -> None:
    """§8: an unattemptable cycle is bookkept FAILED, never device evidence."""

    factory = sessionmaker(bind=db_engine)
    context = DevicePollContext(
        device_id=device_id,
        device_name="core-1",
        snmp=None,
        unavailable_reason=(
            "secrets file is missing required key SNMP_COMMUNITY_DEFAULT"
        ),
    )

    status = poll_device(context, CYCLE, session_factory=factory, now=NOW)
    assert status == "FAILED"

    with Session(db_engine) as session:
        run = session.execute(select(DevicePollRun)).scalar_one()
        assert run.status == "FAILED"
        assert run.failed_sections == "credentials"
        assert run.ssh_reachable is None
        assert session.scalar(select(func.count()).select_from(DeviceMetric)) == 0

        tracking = session.get(DeviceMonitoringState, device_id)
        assert tracking is None  # the state machines were not touched

    # The next real failed cycle counts 1, not 2: the credentials cycle was
    # a gap, not a reachability observation.
    assert (
        poll_device(_context(device_id), CYCLE + STEP, session_factory=factory) == "FAILED"
    )
    with Session(db_engine) as session:
        tracking = session.get(DeviceMonitoringState, device_id)
        assert tracking is not None
        assert tracking.consecutive_failed_cycles == 1
        assert tracking.last_cycle_started_at == CYCLE + STEP
        assert session.scalar(
            select(func.count()).select_from(DeviceReachabilityIncident)
        ) == 0


def test_overlap_skip_records_failed_run_without_state_advance(
    db_engine: Engine, device_id: int
) -> None:
    """§27.11 + §8: the skipped cycle gets its FAILED row; §9 stays untouched."""

    factory = sessionmaker(bind=db_engine)
    status = record_skipped_poll(_context(device_id), CYCLE, session_factory=factory)
    assert status == "FAILED"

    with Session(db_engine) as session:
        run = session.execute(select(DevicePollRun)).scalar_one()
        assert run.status == "FAILED"
        assert run.failed_sections == "overlap"
        assert run.ssh_reachable is None
        assert session.get(DeviceMonitoringState, device_id) is None

    # record_skipped_poll is idempotent too (ON CONFLICT, no duplicate row).
    record_skipped_poll(_context(device_id), CYCLE, session_factory=factory)
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(DevicePollRun)) == 1
