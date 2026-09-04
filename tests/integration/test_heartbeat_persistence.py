"""Worker heartbeat persistence on real PostgreSQL (W00-T005)."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from backend.db.models import WorkerHeartbeat
from backend.heartbeat import build_heartbeat_info, record_heartbeat

pytestmark = pytest.mark.integration


def _read_row(engine: sa.Engine, worker_id: str) -> sa.Row:
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_id)
        ).one()
    return row


def test_heartbeat_is_persisted(db_engine: sa.Engine) -> None:
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    started = now - timedelta(hours=1)
    info = build_heartbeat_info(
        worker_id="test-worker", version="0.1.0", started_at=started, now=now, hostname="host-1"
    )

    with db_engine.begin() as conn:
        record_heartbeat(conn, info)

    row = _read_row(db_engine, "test-worker")
    assert row.worker_id == "test-worker"
    assert row.last_heartbeat == now
    assert row.started_at == started
    assert row.version == "0.1.0"
    assert row.hostname == "host-1"
    # TIMESTAMPTZ columns come back timezone-aware (SYSTEM_SPEC.md §3/§23).
    assert row.last_heartbeat.tzinfo is not None
    assert row.started_at.tzinfo is not None


def test_heartbeat_updates_in_place_on_next_tick(db_engine: sa.Engine) -> None:
    first = build_heartbeat_info(
        worker_id="test-worker",
        version="0.1.0",
        started_at=datetime(2026, 9, 4, 11, 0, tzinfo=UTC),
        now=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
    )
    second = build_heartbeat_info(
        worker_id="test-worker",
        version="0.1.0",
        started_at=datetime(2026, 9, 4, 11, 0, tzinfo=UTC),
        now=datetime(2026, 9, 4, 12, 0, 30, tzinfo=UTC),
    )

    with db_engine.begin() as conn:
        record_heartbeat(conn, first)
    with db_engine.begin() as conn:
        record_heartbeat(conn, second)

    with db_engine.connect() as conn:
        rows = conn.execute(
            sa.select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == "test-worker")
        ).all()

    assert len(rows) == 1
    assert rows[0].last_heartbeat == datetime(2026, 9, 4, 12, 0, 30, tzinfo=UTC)


def test_worker_restart_replaces_started_at(db_engine: sa.Engine) -> None:
    """A restarted worker keeps its identity but shows a new process start."""

    before_restart = build_heartbeat_info(
        worker_id="restarting-worker",
        version="0.1.0",
        started_at=datetime(2026, 9, 4, 8, 0, tzinfo=UTC),
        now=datetime(2026, 9, 4, 8, 0, 30, tzinfo=UTC),
    )
    after_restart = build_heartbeat_info(
        worker_id="restarting-worker",
        version="0.1.0",
        started_at=datetime(2026, 9, 4, 12, 5, tzinfo=UTC),
        now=datetime(2026, 9, 4, 12, 5, 30, tzinfo=UTC),
    )

    with db_engine.begin() as conn:
        record_heartbeat(conn, before_restart)
    with db_engine.begin() as conn:
        record_heartbeat(conn, after_restart)

    row = _read_row(db_engine, "restarting-worker")
    assert row.started_at == datetime(2026, 9, 4, 12, 5, tzinfo=UTC)
    assert row.last_heartbeat == datetime(2026, 9, 4, 12, 5, 30, tzinfo=UTC)
