"""Integration tests for the worker's periodic §24 retention loop (W05-T004).

`run_retention` itself is covered by test_retention.py; here the worker's
loop wrapper is exercised end to end against PostgreSQL: it must actually
delete expired raw data on its periodic passes, keep running across passes,
and refuse to loop at all when the configured retention is below the §24
minimum (misconfiguration must surface, not silently skip cleanup).
"""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.collect.session import DeviceCollectionOutcome, SectionResult
from backend.db.models import Device, DeviceMetric, DevicePollRun
from backend.monitoring.pipeline import persist_poll_result
from backend.worker import _retention_loop

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)
OLD = NOW - timedelta(days=91)  # beyond the 90-day cutoff -> deleted


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        for model in (DeviceMetric, DevicePollRun, Device):
            session.query(model).delete()
        session.commit()
    yield


def _seed_expired_cycle(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = Device(
            name="core-loop",
            management_ip="192.0.2.82",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add(device)
        session.flush()
        outcome = DeviceCollectionOutcome(
            device_name="core-loop",
            sections=[SectionResult("identity", "SUCCESS")],
        )
        persisted = persist_poll_result(session, device.id, OLD, outcome, OLD)
        assert persisted is not None
        session.add(
            DeviceMetric(
                poll_run_id=persisted.run_id,
                device_id=device.id,
                collected_at=OLD,
                cpu_usage_percent=10.0,
            )
        )
        session.commit()


def test_retention_loop_deletes_expired_data_periodically(db_engine: Engine) -> None:
    _seed_expired_cycle(db_engine)
    factory = sessionmaker(bind=db_engine)
    stop = threading.Event()
    thread = threading.Thread(
        target=_retention_loop,
        args=(stop, factory, 90),
        kwargs={"first_delay_seconds": 0, "interval_seconds": 0.1},
        daemon=True,
    )
    thread.start()
    deadline = datetime.now(UTC) + timedelta(seconds=10)
    remaining: int | None = -1
    try:
        while datetime.now(UTC) < deadline:
            with Session(db_engine) as session:
                remaining = session.scalar(select(func.count()).select_from(DevicePollRun))
            if remaining == 0:
                break
            threading.Event().wait(0.05)
        assert remaining == 0, "the retention loop never deleted the expired cycle"
        # The loop keeps running after a productive pass (periodic, not once).
        assert thread.is_alive()
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive(), "the loop ignored the stop event"


def test_retention_loop_refuses_sub_ninety_day_configuration(db_engine: Engine) -> None:
    # §24 keeps raw data at least 90 days: a smaller configured value must
    # terminate the loop loudly (logged error), never loop "successfully".
    _seed_expired_cycle(db_engine)
    factory = sessionmaker(bind=db_engine)
    stop = threading.Event()
    thread = threading.Thread(
        target=_retention_loop,
        args=(stop, factory, 89),
        kwargs={"first_delay_seconds": 0, "interval_seconds": 0.1},
        daemon=True,
    )
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "a sub-90-day configuration must stop the loop"
    stop.set()
    # The expired data is untouched: refusing is not deleting.
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(DevicePollRun)) == 1
