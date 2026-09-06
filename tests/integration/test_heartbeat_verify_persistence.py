"""W05-AUDIT fix 1 + W05-AUDIT-2 + W05-PRE-ACCEPTANCE-HARDENING: heartbeat
verifier against real PostgreSQL.

The unit tests pin the decision logic; these tests pin the DB-facing half —
`fetch_sample` reads ONLY the queried worker's row (never another worker's
fresh heartbeat), the baseline JSON survives the real TIMESTAMPTZ round
trip with microsecond fidelity, and the race data sequences (old worker's
post-baseline final tick WITH a baseline row; old container's FIRST tick
after a no-row baseline) are judged exactly as the deploy scripts will
judge them, through the real CLI (`python -m backend.ops.heartbeat_verify`).
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, tzinfo

import pytest
import sqlalchemy as sa

import backend.ops.heartbeat_verify as heartbeat_verify
from backend.db.models import WorkerHeartbeat
from backend.heartbeat import build_heartbeat_info, record_heartbeat
from backend.ops.heartbeat_verify import (
    HeartbeatBaseline,
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


class _FixedClock:
    """`datetime` stand-in so the real CLI judges at a deterministic instant
    (`heartbeat_verify` uses only `datetime.now` and `fromisoformat`). The
    tests move `current` to step the clock between capture and verify."""

    current = NOW

    fromisoformat = staticmethod(datetime.fromisoformat)

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return cls.current if tz is None else cls.current.astimezone(tz)


@pytest.fixture(autouse=True)
def _clean(db_engine: sa.Engine) -> Iterator[None]:
    with db_engine.begin() as conn:
        conn.execute(sa.delete(WorkerHeartbeat))
    yield


@pytest.fixture
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(heartbeat_verify, "datetime", _FixedClock)
    _FixedClock.current = NOW
    yield
    _FixedClock.current = NOW


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
    baseline: HeartbeatBaseline,
    *,
    pre: str = "samecontainerid",
    post: str = "samecontainerid",
) -> tuple[bool, str]:
    return verify_heartbeat(
        sample,
        baseline,
        now=NOW,
        interval_seconds=INTERVAL,
        worker_id=WORKER_ID,
        pre_id=pre,
        post_id=post,
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
    ok, reason = _verify(baseline, HeartbeatBaseline(NOW, baseline))  # no new write
    assert not ok
    assert "no NEW heartbeat" in reason

    # The baseline JSON that deploy/lib.sh holds reproduces the verdict.
    ok, _ = _verify(
        _sample(db_engine), parse_baseline(dump_baseline(baseline, captured_at=NOW))
    )
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

    ok, reason = _verify(
        _sample(db_engine), HeartbeatBaseline(NOW, None), pre="", post="newcontainer"
    )
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

    ok, reason = _verify(
        _sample(db_engine),
        HeartbeatBaseline(NOW - timedelta(seconds=40), baseline),
        pre="oldcontainer",
        post="newcontainer",
    )
    assert ok
    assert "new heartbeat for worker" in reason
    assert "replacement worker" in reason


def test_scenario_d_fresh_install_first_row_passes(db_engine: sa.Engine) -> None:
    assert _sample(db_engine) is None
    captured_at = NOW - timedelta(seconds=2)
    baseline_json = dump_baseline(None, captured_at=captured_at)
    assert baseline_json == json.dumps({"captured_at": captured_at.isoformat()})

    _record(
        db_engine,
        WORKER_ID,
        last=NOW - timedelta(seconds=5),
        started=NEW_STARTED,
    )

    ok, reason = _verify(
        _sample(db_engine), parse_baseline(baseline_json), pre="", post="newcontainer"
    )
    assert ok
    assert "first heartbeat for worker" in reason


def test_scenario_e_same_container_reverify_passes_without_started_at(
    db_engine: sa.Engine,
) -> None:
    _record(db_engine, WORKER_ID, last=NOW - timedelta(seconds=45), started=OLD_STARTED)
    baseline = _sample(db_engine)
    assert baseline is not None
    baseline_obj = HeartbeatBaseline(NOW - timedelta(seconds=45), baseline)

    # The old worker's row, re-read without any new write: must not pass.
    ok, _ = _verify(_sample(db_engine), baseline_obj)
    assert not ok

    # The (re)started worker keeps its container and writes the next tick —
    # same process, so started_at legitimately stays put.
    _record(db_engine, WORKER_ID, last=NOW - timedelta(seconds=5), started=OLD_STARTED)
    ok, reason = _verify(_sample(db_engine), baseline_obj)
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
    baseline_json = dump_baseline(_sample(db_engine), captured_at=t0)
    assert baseline_json == json.dumps(
        {
            "captured_at": t0.isoformat(),
            "last_heartbeat": t0.isoformat(),
            "started_at": OLD_STARTED.isoformat(),
        }
    )

    # The old worker's final tick AFTER the baseline (compose has not
    # stopped it yet) — same process, so started_at stays S0.
    _record(db_engine, WORKER_ID, last=t1, started=OLD_STARTED)

    # Container identity: PRE != POST — the worker container was replaced.
    baseline = parse_baseline(baseline_json)
    sample = _sample(db_engine)
    ok, reason = _verify(sample, baseline, pre="oldcontainer", post="newcontainer")
    assert not ok
    assert "old worker wrote after the baseline but the replacement worker has not" in reason
    assert str(OLD_STARTED.isoformat()) in reason

    # A same-container verdict on this identical data would be the false
    # positive the old (pre-W05-AUDIT-2) logic allowed.
    ok, _ = _verify(sample, baseline, pre="samecontainer", post="samecontainer")
    assert ok

    # The replacement worker's own first heartbeat: T2 > T1, S1 > S0.
    _record(db_engine, WORKER_ID, last=t2, started=NEW_STARTED)
    ok, reason = _verify(_sample(db_engine), baseline, pre="oldcontainer", post="newcontainer")
    assert ok
    assert "replacement worker" in reason


def test_no_row_baseline_race_sequence_on_real_db(db_engine: sa.Engine) -> None:
    """The W05-PRE-ACCEPTANCE-HARDENING race, end to end on real PostgreSQL:

    the existing worker container has NOT ticked yet, so the baseline holds
    no row (only captured_at = T0) → the OLD container writes its FIRST
    tick (T1 > T0, started_at S0 < T0) → compose replaces its container →
    the replacement worker is silent: the fresh-install branch of old would
    PASS here; the hardened verifier must FAIL on the old container's tick.
    Only the replacement worker's own row (S1 > T0) passes. A legacy
    no-row baseline cannot attribute the row at all and fails closed.
    """

    t0 = NOW - timedelta(seconds=40)
    t1 = NOW - timedelta(seconds=20)
    t2 = NOW - timedelta(seconds=5)
    old_started = NOW - timedelta(seconds=70)  # started BEFORE the capture t0

    assert _sample(db_engine) is None
    baseline_json = dump_baseline(None, captured_at=t0)
    baseline = parse_baseline(baseline_json)

    # The OLD container's first-ever tick, after the baseline capture.
    _record(db_engine, WORKER_ID, last=t1, started=old_started)

    ok, reason = _verify(
        _sample(db_engine), baseline, pre="oldcontainer", post="newcontainer"
    )
    assert not ok
    assert "old container wrote after the baseline but the replacement worker has not" in reason

    # A legacy {} baseline for the same data cannot attribute the row.
    ok, reason = _verify(
        _sample(db_engine),
        parse_baseline("{}"),
        pre="oldcontainer",
        post="newcontainer",
    )
    assert not ok
    assert "no captured_at" in reason

    # The replacement worker's own first heartbeat (S1 > T0): PASS.
    _record(db_engine, WORKER_ID, last=t2, started=NEW_STARTED)
    ok, reason = _verify(
        _sample(db_engine), baseline, pre="oldcontainer", post="newcontainer"
    )
    assert ok
    assert "replacement worker" in reason


def test_cli_verify_judges_the_race_on_real_postgres(
    db_engine: sa.Engine,
    fixed_clock: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The real CLI (`python -m backend.ops.heartbeat_verify verify ...`)
    with the deploy scripts' exact arguments: exit 1 on the old worker's
    post-baseline tick, exit 0 on the replacement worker's own heartbeat."""

    monkeypatch.setenv("NETWORK_REPORT_WORKER_ID", WORKER_ID)
    t0 = NOW - timedelta(seconds=40)
    _record(db_engine, WORKER_ID, last=t0, started=OLD_STARTED)
    baseline = dump_baseline(_sample(db_engine), captured_at=t0)

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


def test_cli_baseline_and_no_row_replacement_verdict_on_real_postgres(
    db_engine: sa.Engine, fixed_clock: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`baseline` on a worker with no row emits only captured_at — and the
    deploy scripts' exact verify call then FAILs the old container's first
    post-capture tick and PASSes the replacement worker's (exit codes 1/0
    through the real CLI, stepped fixed clock)."""

    monkeypatch.setenv("NETWORK_REPORT_WORKER_ID", WORKER_ID)
    assert _sample(db_engine) is None

    # The deploy scripts capture the baseline right before the restart.
    capture = NOW - timedelta(seconds=40)
    _FixedClock.current = capture
    rc = main(["heartbeat_verify", "baseline"])
    baseline = capsys.readouterr().out.strip()
    assert rc == 0
    assert json.loads(baseline) == {"captured_at": capture.isoformat()}

    # The OLD container's first-ever tick: last_heartbeat fresh, but its
    # process started BEFORE the capture — the pre-hardening fresh-install
    # branch would have passed this.
    _FixedClock.current = NOW
    _record(
        db_engine,
        WORKER_ID,
        last=NOW - timedelta(seconds=20),
        started=capture - timedelta(seconds=30),
    )
    rc = main(["heartbeat_verify", "verify", baseline, "precontainer", "postcontainer"])
    assert rc == 1
    assert "old container wrote after the baseline" in capsys.readouterr().out

    # The replacement worker started AFTER the capture and ticks: PASS.
    _record(db_engine, WORKER_ID, last=NOW - timedelta(seconds=5), started=NEW_STARTED)
    rc = main(["heartbeat_verify", "verify", baseline, "precontainer", "postcontainer"])
    assert rc == 0
    assert "replacement worker" in capsys.readouterr().out


def test_baseline_json_survives_the_timestamptz_round_trip(
    db_engine: sa.Engine,
) -> None:
    last = NOW - timedelta(seconds=10, microseconds=123456)
    started = NOW - timedelta(hours=1, microseconds=654321)
    _record(db_engine, WORKER_ID, last=last, started=started)

    parsed = parse_baseline(dump_baseline(_sample(db_engine), captured_at=NOW))
    assert parsed.sample is not None
    assert parsed.captured_at == NOW
    assert parsed.sample.last_heartbeat == last
    assert parsed.sample.started_at == started
    assert parsed.sample.last_heartbeat.tzinfo is not None
