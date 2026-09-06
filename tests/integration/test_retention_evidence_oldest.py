"""W05-AUDIT fix 2: the A13 evidence query returns the TRUE oldest row.

The pre-audit collector used MAX(collected_at) and labelled it "oldest" —
with any recent row present it always reported OK. Against real
PostgreSQL, `oldest_rows` must return MIN() per raw table, so a table
holding both a 180-day-old and a 1-day-old row is judged BEYOND-90d (A13),
never OK.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend.collect.session import DeviceCollectionOutcome, SectionResult
from backend.db.models import Device, DeviceMetric, DevicePollRun, Interface, InterfaceMetric
from backend.monitoring.pipeline import persist_poll_result
from backend.ops.retention_evidence import oldest_rows, render_retention_evidence

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=180)  # beyond 90d — must drive the verdict
RECENT = NOW - timedelta(days=1)  # the row MAX used to report as "oldest"

TABLES = (DeviceMetric, InterfaceMetric, DevicePollRun)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        for model in (*TABLES, Interface, Device):
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
) -> int:
    outcome = DeviceCollectionOutcome(
        device_name="core-1",
        sections=[SectionResult("identity", "SUCCESS")],
    )
    persisted = persist_poll_result(session, device_id, cycle, outcome, collected)
    assert persisted is not None
    session.add(
        DeviceMetric(
            poll_run_id=persisted.run_id, device_id=device_id, collected_at=collected,
            cpu_usage_percent=10.0,
        )
    )
    interface_id = session.query(Interface.id).filter_by(device_id=device_id).scalar()
    session.add(
        InterfaceMetric(
            poll_run_id=persisted.run_id,
            device_id=device_id,
            interface_id=interface_id,
            collected_at=collected,
        )
    )
    session.commit()
    return persisted.run_id


def test_oldest_row_is_the_180_day_one_not_the_recent_one(
    db_engine: Engine, device_id: int
) -> None:
    with Session(db_engine) as session:
        _seed_cycle(session, device_id, OLD, OLD)
        _seed_cycle(session, device_id, RECENT, RECENT)

    with db_engine.connect() as conn:
        oldest = oldest_rows(conn)
    for label in ("device_metrics", "interface_metrics", "device_poll_runs"):
        assert oldest[label] is not None, label
        assert oldest[label] == OLD, label  # MIN, never MAX/RECENT


def test_evidence_block_reports_beyond_90d_with_mixed_ages(
    db_engine: Engine, device_id: int
) -> None:
    with Session(db_engine) as session:
        _seed_cycle(session, device_id, OLD, OLD)
        _seed_cycle(session, device_id, RECENT, RECENT)

    with db_engine.connect() as conn:
        rendered = render_retention_evidence(conn, NOW)

    assert rendered.count("BEYOND-90d") == 3
    assert "age 180.0 days" in rendered
    assert OLD.isoformat() in rendered
    # A recent row existing must NOT produce an OK verdict anywhere.
    assert "OK" not in rendered


def test_recent_only_tables_report_ok(db_engine: Engine, device_id: int) -> None:
    with Session(db_engine) as session:
        _seed_cycle(session, device_id, RECENT, RECENT)

    with db_engine.connect() as conn:
        rendered = render_retention_evidence(conn, NOW)

    assert rendered.count("OK") == 3
    assert "age 1.0 days" in rendered


def test_empty_tables_report_empty(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        rendered = render_retention_evidence(conn, NOW)
    assert rendered.count(": empty") == 3
