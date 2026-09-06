"""W05-AUDIT fix 1 + W05-AUDIT-2: heartbeat verifier against real PostgreSQL.

The unit tests pin the decision logic; these tests pin the DB-facing half —
`fetch_sample` reads ONLY the queried worker's row (never another worker's
fresh heartbeat), the baseline JSON survives the real TIMESTAMPTZ round
trip with microsecond fidelity, and the W05-AUDIT-2 race data sequence
(old worker's post-baseline final tick → replaced container → replacement
worker's own first heartbeat) is judged exactly as the deploy scripts will
judge it, through the real CLI (`python -m backend.ops.heartbeat_verify`).
"""

import json
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
    main,
    parse_baseline,
    verify_heartbeat,
)

pytestmark = pytest.mark.integration

WORKER_ID = "deploy-worker"
OTHER_WORKER_ID = "other-worker"
NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
INTERVAL = 30

OLD_STARTED = NOW - timedelta(hours=1)
NEW_STARTED = NOW - timedelta(seconds=15)


@pytest.fixture(autouse=True)
def _clean(db_engine: sa.Engine) -> Iterator[None]:
    with db_engine.begin() as conn:
        conn.execute(sa.delete(WorkerHeartbeat))
    yield


def _record(
    engine: sa.Engine,
    worker_id: str,
    *,
    last: datetime,
    started: datetime,
) -> None:
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
    sample: HeartbeatSample | None,
    baseline: HeartbeatSample | None,
    *,
    replaced: bool = False,
) -> tuple[bool, str]:
    return verify_heartbeat(
        sample,
        baseline,
        now=NOW,
        interval_seconds=INTERVAL,
        worker_id=WORKER_ID,
        container_replaced=replaced,
    )


def test_scenario_a_stale_leftover_row_fails_on_real_db(db_engine: sa.Engine) -> None:
    """The regression that motivated the fix: a 10s-old row from the
    PREVIOUS worker with a silent new worker must FAIL, not pass."""

    _record(
        db_engine,
        WORKER_ID,
        last=NOW - timedelta(seconds=10),
        started=OLD_STARTED,
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
    # must not be returned, and the verifier must fail on the missing row —
    # including in the fresh-install mode where any own row would pass.
    assert _sample(db_engine) is None
    with db_engine.connect() as conn:
        assert fetch_sample(conn, OTHER_WORKER_ID) is not None

    ok, reason = _verify(_sample(db_engine), None, replaced=True)
    assert not ok
    assert f"no heartbeat row for worker {WORKER_ID!r}" in reason


def test_scenario_c_replacement_workers_post_start_heartbeat_passes(
    db_engine: sa.Engine,
) -> None:
    _record(db_engine, WORKER_ID, last=NOW - timedelta(seconds=40), started=OLD_STARTED)
    baseline = _sample(db_engine)
    assert baseline is not None

    _record(
        db_engine,
        WORKER_ID,
        last=NOW - timedelta(seconds=5),
        started=NEW_STARTED,  # the recreated worker process
    )

    ok, reason = _verify(_sample(db_engine), baseline, replaced=True)
    assert ok
    assert "new heartbeat for worker" in reason
    assert "replacement worker" in reason


def test_scenario_d_fresh_install_first_row_passes(db_engine: sa.Engine) -> None:
    assert _sample(db_engine) is None
    baseline_json = dump_baseline(None)
    assert baseline_json == "{}"

    _record(
        db_engine,
        WORKER_ID,
        last=NOW - timedelta(seconds=5),
        started=NEW_STARTED,
    )

    ok, reason = _verify(_sample(db_engine), parse_baseline(baseline_json), replaced=True)
    assert ok
    assert "first heartbeat for worker" in reason


def test_scenario_e_same_container_reverify_passes_without_started_at(
    db_engine: sa.Engine,
) -> None:
    _record(db_engine, WORKER_ID, last=NOW - timedelta(seconds=45), started=OLD_STARTED)
    baseline = _sample(db_engine)
    assert baseline is not None

    # The old worker's row, re-read without any new write: must not pass.
    ok, _ = _verify(_sample(db_engine), baseline, replaced=False)
    assert not ok

    # The (re)started worker keeps its container and writes the next tick —
    # same process, so started_at legitimately stays put.
    _record(db_engine, WORKER_ID, last=NOW - timedelta(seconds=5), started=OLD_STARTED)
    ok, reason = _verify(_sample(db_engine), baseline, replaced=False)
    assert ok
    assert "new heartbeat for worker" in reason
    assert "kept running" in reason


def test_race_sequence_old_worker_final_tick_fails_then_replacement_passes(
    db_engine: sa.Engine,
) -> None:
    """The W05-AUDIT-2 race, end to end on real PostgreSQL:

    baseline (T0, S0) → the OLD worker ticks once more (T1 > T0, still S0)
    → its container is replaced → the replacement worker is silent:
    the verifier must FAIL on the old tick. Only when the replacement
    worker writes its own heartbeat (T2 > T1, S1 > S0) does it PASS.
    """

    t0 = NOW - timedelta(seconds=40)
    t1 = NOW - timedelta(seconds=20)
    t2 = NOW - timedelta(seconds=5)

    _record(db_engine, WORKER_ID, last=t0, started=OLD_STARTED)
    baseline_json = dump_baseline(_sample(db_engine))
    assert baseline_json == json.dumps(
        {"last_heartbeat": t0.isoformat(), "started_at": OLD_STARTED.isoformat()}
    )

    # The old worker's final tick AFTER the baseline (compose has not
    # stopped it yet) — same process, so started_at stays S0.
    _record(db_engine, WORKER_ID, last=t1, started=OLD_STARTED)

    # Container identity: PRE != POST — the worker container was replaced.
    baseline = parse_baseline(baseline_json)
    sample = _sample(db_engine)
    ok, reason = _verify(sample, baseline, replaced=True)
    assert not ok
    assert "old worker wrote after the baseline but the replacement worker has not" in reason
    assert str(OLD_STARTED.isoformat()) in reason

    # A same-container verdict on this identical data would be the false
    # positive the old (pre-W05-AUDIT-2) logic allowed.
    ok, _ = _verify(sample, baseline, replaced=False)
    assert ok

    # The replacement worker's own first heartbeat: T2 > T1, S1 > S0.
    _record(db_engine, WORKER_ID, last=t2, started=NEW_STARTED)
    ok, reason = _verify(_sample(db_engine), baseline, replaced=True)
    assert ok
    assert "replacement worker" in reason


def test_cli_verify_judges_the_race_on_real_postgres(
    db_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real CLI (`python -m backend.ops.heartbeat_verify verify ...`)
    with the deploy scripts' exact arguments: exit 1 on the old worker's
    post-baseline tick, exit 0 on the replacement worker's own heartbeat."""

    monkeypatch.setenv("NETWORK_REPORT_WORKER_ID", WORKER_ID)
    t0 = NOW - timedelta(seconds=40)
    _record(db_engine, WORKER_ID, last=t0, started=OLD_STARTED)
    baseline = dump_baseline(_sample(db_engine))

    _record(db_engine, WORKER_ID, last=NOW - timedelta(seconds=20), started=OLD_STARTED)
    rc = main(
        ["heartbeat_verify", "verify", baseline, "precontainer", "postcontainer"]
    )
    assert rc == 1
    assert "replacement worker has not" in capsys.readouterr().out

    _record(db_engine, WORKER_ID, last=NOW - timedelta(seconds=5), started=NEW_STARTED)
    rc = main(
        ["heartbeat_verify", "verify", baseline, "precontainer", "postcontainer"]
    )
    assert rc == 0
    assert "replacement worker" in capsys.readouterr().out

    # Same container identity on both sides: the started_at requirement is
    # never imposed (the in-place re-verify stays legal).
    rc = main(["heartbeat_verify", "verify", baseline, "same", "same"])
    assert rc == 0


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
