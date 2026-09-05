"""Integration tests for the 90-day batched retention (W02-T009, §24).

Old raw metrics/poll runs are deleted in batches; new data, incident
records, IRF observation history and system settings survive untouched.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.collect.session import DeviceCollectionOutcome, SectionResult
from backend.db.models import (
    Device,
    DeviceMetric,
    DevicePollRun,
    DeviceReachabilityIncident,
    Interface,
    InterfaceMetric,
    IrfMemberObservation,
    SystemSetting,
)
from backend.monitoring.pipeline import persist_poll_result
from backend.monitoring.retention import run_retention

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)
CUTOFF = NOW - timedelta(days=90)
OLD = CUTOFF - timedelta(days=1)  # 91 days old -> deleted
RECENT = NOW - timedelta(days=1)  # 1 day old -> kept


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        for model in (
            InterfaceMetric,
            DeviceMetric,
            DevicePollRun,
            DeviceReachabilityIncident,
            IrfMemberObservation,
            Interface,
            SystemSetting,
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
            management_ip="192.0.2.81",
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


def _seed_cycle(
    session: Session, device_id: int, cycle: datetime, collected: datetime
) -> None:
    outcome = DeviceCollectionOutcome(
        device_name="core-1",
        sections=[SectionResult("identity", "SUCCESS")],
    )
    run_id = persist_poll_result(session, device_id, cycle, outcome, collected)
    assert run_id is not None
    session.add(
        DeviceMetric(
            poll_run_id=run_id, device_id=device_id, collected_at=collected,
            cpu_usage_percent=10.0,
        )
    )


def test_retention_deletes_only_expired_raw_data(
    db_engine: Engine, device_id: int
) -> None:
    with Session(db_engine) as session:
        _seed_cycle(session, device_id, OLD, OLD)  # old cycle
        _seed_cycle(session, device_id, RECENT, RECENT)  # recent cycle
        session.commit()

        # Long-term records that retention must never touch.
        session.add(
            DeviceReachabilityIncident(
                device_id=device_id, started_at=OLD, recovered_at=None
            )
        )
        session.add(
            IrfMemberObservation(
                device_id=device_id,
                member_id=1,
                observed=True,
                observed_at=OLD,
            )
        )
        session.add(SystemSetting(key="cpu_sustained_high_percent", value="80.0"))
        session.commit()

        factory = sessionmaker(bind=db_engine)
        report = run_retention(factory, now=NOW, retention_days=90)

        # One old cycle: 1 poll run + its 1 device metric (no interface rows).
        assert report.total_deleted == 2
        assert report.poll_runs_deleted == 1
        assert report.device_metrics_deleted == 1
        assert report.interface_metrics_deleted == 0

        # Expired raw data is gone (the run cascades its children anyway).
        assert session.scalar(select(func.count()).select_from(DevicePollRun)) == 1
        kept_run = session.execute(select(DevicePollRun)).scalar_one()
        assert kept_run.cycle_started_at == RECENT
        assert session.scalar(select(func.count()).select_from(DeviceMetric)) == 1

        # Long-term data survived.
        assert (
            session.execute(select(func.count()).select_from(DeviceReachabilityIncident))
            .scalar_one()
            == 1
        )
        assert (
            session.execute(select(func.count()).select_from(IrfMemberObservation)).scalar_one()
            == 1
        )
        assert session.execute(select(func.count()).select_from(SystemSetting)).scalar_one() == 1


def test_retention_is_batched(db_engine: Engine, device_id: int) -> None:
    """Batch size bounds each DELETE; several batches drain the backlog."""

    with Session(db_engine) as session:
        for i in range(7):
            cycle = OLD - timedelta(minutes=5 * i)
            _seed_cycle(session, device_id, cycle, cycle)
        session.commit()

        factory = sessionmaker(bind=db_engine)
        # max_batches_per_table=2 x batch_size=3 -> at most 6 of 7 deleted.
        partial = run_retention(
            factory, now=NOW, retention_days=90, batch_size=3, max_batches_per_table=2
        )
        assert partial.poll_runs_deleted == 6
        remaining = session.scalar(select(func.count()).select_from(DevicePollRun))
        assert remaining == 1

        # A full pass removes the rest.
        full = run_retention(factory, now=NOW, retention_days=90, batch_size=3)
        assert full.poll_runs_deleted == 1
        assert session.scalar(select(func.count()).select_from(DevicePollRun)) == 0


def test_retention_rejects_configured_values_below_ninety(
    db_engine: Engine,
) -> None:
    factory = sessionmaker(bind=db_engine)
    with pytest.raises(ValueError, match="must be >= 90"):
        run_retention(factory, now=NOW, retention_days=30)


def test_retention_interface_metrics_deleted_before_runs(
    db_engine: Engine, device_id: int
) -> None:
    """Interface metric rows are cleaned in their own batches (no cascade)."""

    with Session(db_engine) as session:
        outcome = DeviceCollectionOutcome(
            device_name="core-1",
            sections=[SectionResult("interfaces", "SUCCESS")],
        )
        outcome.interfaces = []
        run_id = persist_poll_result(session, device_id, OLD, outcome, OLD)
        assert run_id is not None
        iface = session.execute(
            select(Interface).where(
                Interface.normalized_name == "ten-gigabitethernet1/0/1"
            )
        ).scalar_one()
        session.add(
            InterfaceMetric(
                poll_run_id=run_id,
                device_id=device_id,
                interface_id=iface.id,
                collected_at=OLD,
            )
        )
        session.commit()

        factory = sessionmaker(bind=db_engine)
        report = run_retention(factory, now=NOW, retention_days=90)
        assert report.interface_metrics_deleted == 1
        assert report.poll_runs_deleted == 1
        assert session.scalar(select(func.count()).select_from(InterfaceMetric)) == 0
        assert session.scalar(select(func.count()).select_from(DevicePollRun)) == 0
