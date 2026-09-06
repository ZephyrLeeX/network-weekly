"""W05-AUDIT fix 1: heartbeat verifier scoping against real PostgreSQL.

The unit tests pin the decision logic; these tests pin the DB-facing half —
`fetch_sample` reads ONLY the queried worker's row (never another worker's
fresh heartbeat) and the baseline JSON survives the real TIMESTAMPTZ round
trip with microsecond fidelity.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from backend.db.models import WorkerHeartbeat
from backend.heartbeat import build_heartbeat_info, record_heartbeat
from backend.ops.heartbeat_verify import (
    HeartbeatSample,
    dump_baseline,
    fetch_sample,
    parse_baseline,
    verify_heartbeat,
)

pytestmark = pytest.mark.integration

WORKER_ID = "deploy-worker"
OTHER_WORKER_ID = "other-worker"
NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
INTERVAL = 30


@pytest.fixture(autouse=True)
def _clean(db_engine: sa.Engine) -> Iterator[None]:
    with db_engine.begin() as conn:
        conn.execute(sa.delete(WorkerHeartbeat))
    yield


def _record(engine: sa.Engine, worker_id: str, *, last: datetime, started: datetime) -> None:
    with engine.begin() as conn:
        record_heartbeat(
            conn,
            build_heartbeat_info(
                worker_id=worker_id, version="0.1.0", started_at=started, now=last
            ),
        )


def _sample(
    engine: sa.Engine, worker_id: str = WORKER_ID
) -> HeartbeatSample | None:
    with engine.connect() as conn:
        return fetch_sample(conn, worker_id)


def _verify(
    sample: HeartbeatSample | None, baseline: HeartbeatSample | None
) -> tuple[bool, str]:
    return verify_heartbeat(
        sample, baseline, now=NOW, interval_seconds=INTERVAL, worker_id=WORKER_ID
    )


def test_scenario_a_stale_leftover_row_fails_on_real_db(db_engine: sa.Engine) -> None:
    """The regression that motivated the fix: a 10s-old row from the
    PREVIOUS worker with a silent new worker must FAIL, not pass."""

    _record(
        db_engine,
        WORKER_ID,
        last=NOW - timedelta(seconds=10),
        started=NOW - timedelta(hours=1),
    )

    baseline = _sample(db_engine)
    assert baseline is not None
    ok, reason = _verify(baseline, baseline)  # no new write since baseline
    assert not ok
    assert "no NEW heartbeat" in reason

    # The baseline JSON that deploy/lib.sh holds reproduces the verdict.
    ok, _ = _verify(_sample(db_engine), parse_baseline(dump_baseline(baseline)))
    assert not ok


def test_scenario_b_other_workers_fresh_row_is_invisible(db_engine: sa.Engine) -> None:
    _record(
        db_engine,
        OTHER_WORKER_ID,
        last=NOW - timedelta(seconds=1),
        started=NOW - timedelta(seconds=2),
    )

    # The query is scoped to the current worker id: the fresh foreign row
    # must not be returned, and the verifier must fail on the missing row.
    assert _sample(db_engine) is None
    with db_engine.connect() as conn:
        assert fetch_sample(conn, OTHER_WORKER_ID) is not None

    ok, reason = _verify(_sample(db_engine), None)
    assert not ok
    assert f"no heartbeat row for worker {WORKER_ID!r}" in reason


def test_scenario_c_new_post_start_heartbeat_passes(db_engine: sa.Engine) -> None:
    _record(
        db_engine,
        WORKER_ID,
        last=NOW - timedelta(seconds=40),
        started=NOW - timedelta(hours=1),
    )
    baseline = _sample(db_engine)
    assert baseline is not None

    _record(
        db_engine,
        WORKER_ID,
        last=NOW - timedelta(seconds=5),
        started=NOW - timedelta(seconds=15),  # the recreated worker process
    )

    ok, reason = _verify(_sample(db_engine), baseline)
    assert ok
    assert "new heartbeat for worker" in reason
    assert "restarted" in reason


def test_scenario_d_fresh_install_first_row_passes(db_engine: sa.Engine) -> None:
    assert _sample(db_engine) is None
    baseline_json = dump_baseline(None)
    assert baseline_json == "{}"

    _record(
        db_engine,
        WORKER_ID,
        last=NOW - timedelta(seconds=5),
        started=NOW - timedelta(seconds=10),
    )

    ok, reason = _verify(
        _sample(db_engine), parse_baseline(baseline_json)
    )
    assert ok
    assert "first heartbeat for worker" in reason


def test_scenario_e_same_image_update_requires_new_write(db_engine: sa.Engine) -> None:
    _record(
        db_engine,
        WORKER_ID,
        last=NOW - timedelta(seconds=45),
        started=NOW - timedelta(days=1),
    )
    baseline = _sample(db_engine)
    assert baseline is not None

    # The old worker's row, re-read without any new write: must not pass.
    ok, _ = _verify(_sample(db_engine), baseline)
    assert not ok

    # The (re)created worker keeps its id and writes the next tick.
    _record(
        db_engine,
        WORKER_ID,
        last=NOW - timedelta(seconds=5),
        started=NOW - timedelta(seconds=20),
    )
    ok, reason = _verify(_sample(db_engine), baseline)
    assert ok
    assert "new heartbeat for worker" in reason


def test_baseline_json_survives_the_timestamptz_round_trip(
    db_engine: sa.Engine,
) -> None:
    last = NOW - timedelta(seconds=10, microseconds=123456)
    started = NOW - timedelta(hours=1, microseconds=654321)
    _record(db_engine, WORKER_ID, last=last, started=started)

    parsed = parse_baseline(dump_baseline(_sample(db_engine)))
    assert parsed is not None
    assert parsed.last_heartbeat == last
    assert parsed.started_at == started
    assert parsed.last_heartbeat.tzinfo is not None
