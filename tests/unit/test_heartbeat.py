"""Unit tests for worker heartbeat service logic (W00-T005)."""

from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from backend.heartbeat import build_heartbeat_info, build_upsert_statement


def test_build_heartbeat_info_defaults_to_utc_now() -> None:
    before = datetime.now(UTC)
    info = build_heartbeat_info(worker_id="worker", version="0.1.0", started_at=before)
    after = datetime.now(UTC)

    assert info.last_heartbeat.tzinfo is not None
    assert before <= info.last_heartbeat <= after


def test_build_heartbeat_info_uses_injected_now() -> None:
    started = datetime(2026, 9, 1, 0, 10, tzinfo=UTC)
    now = datetime(2026, 9, 1, 0, 11, tzinfo=UTC)

    info = build_heartbeat_info(
        worker_id="worker", version="0.1.0", started_at=started, now=now, hostname="host-1"
    )

    assert info.last_heartbeat == now
    assert info.started_at == started
    assert info.hostname == "host-1"


def test_upsert_statement_targets_single_row_per_worker() -> None:
    info = build_heartbeat_info(
        worker_id="worker",
        version="0.1.0",
        started_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    sql = str(
        build_upsert_statement(info).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "INSERT INTO worker_heartbeat" in sql
    assert "ON CONFLICT (worker_id) DO UPDATE" in sql
    assert "last_heartbeat" in sql
