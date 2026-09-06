"""W05-AUDIT fix 1 + W05-AUDIT-2: the deploy heartbeat verifier state machine.

`backend.ops.heartbeat_verify` must only accept heartbeat evidence written
by the worker container that is RUNNING NOW, after the baseline captured
before the start/update:

- a stale row from a previous worker (scenario A) never passes;
- when the worker CONTAINER was replaced, a heartbeat newer than the
  baseline is NOT enough — the old worker's post-baseline final tick (the
  race scenario R) looks exactly like that, so `started_at` must have
  advanced too;
- when the container was NOT replaced (a true same-container re-verify,
  scenario E) the heartbeat moving is sufficient and `started_at` may stay
  put — making `started_at` unconditional there would break the legitimate
  in-place re-verify;
- the fresh-install (D) and post-write (C) cases must pass.

The DB-facing query scoping is pinned separately against real PostgreSQL in
`tests/integration/test_heartbeat_verify_persistence.py`, together with the
full race data sequence.
"""

from datetime import UTC, datetime, timedelta

import pytest

from backend.ops.heartbeat_verify import (
    AGE_LIMIT_INTERVALS,
    HeartbeatSample,
    dump_baseline,
    is_container_replaced,
    main,
    parse_baseline,
    verify_heartbeat,
)

NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
INTERVAL = 30
LIMIT = INTERVAL * AGE_LIMIT_INTERVALS  # 120s


def _sample(last_heartbeat: datetime, started_at: datetime) -> HeartbeatSample:
    return HeartbeatSample(last_heartbeat=last_heartbeat, started_at=started_at)


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
        worker_id="worker",
        container_replaced=replaced,
    )


def test_scenario_a_stale_row_from_previous_worker_never_passes() -> None:
    """A 10s-old row left by the PREVIOUS worker, new worker silent: FAIL —
    both when the row vanished and when it is exactly the baseline."""

    baseline = _sample(NOW - timedelta(seconds=10), NOW - timedelta(hours=1))
    ok, reason = _verify(None, baseline)
    assert not ok
    assert "no heartbeat row for worker 'worker'" in reason

    ok, reason = _verify(baseline, baseline)  # row unchanged since baseline
    assert not ok
    assert "no NEW heartbeat" in reason


def test_scenario_a_worker_restart_alone_is_not_evidence() -> None:
    """A newer started_at (restart observed) with no newer heartbeat is
    insufficient under either container identity."""

    baseline = _sample(NOW - timedelta(seconds=10), NOW - timedelta(hours=1))
    restarted_but_silent = _sample(baseline.last_heartbeat, NOW - timedelta(seconds=5))
    for replaced in (False, True):
        ok, reason = _verify(restarted_but_silent, baseline, replaced=replaced)
        assert not ok
        assert "no NEW heartbeat" in reason


def test_scenario_b_other_workers_fresh_heartbeat_does_not_pass() -> None:
    """Only the current worker id is ever queried (DB scoping pinned in the
    integration tests): for the verifier a missing own row is a failure even
    though some other worker's row is brand new — including the replaced-
    container fresh install, whose own row has not appeared yet."""

    ok, reason = _verify(None, None, replaced=True)
    assert not ok
    assert "no heartbeat row for worker 'worker'" in reason


def test_scenario_c_new_post_start_heartbeat_passes() -> None:
    baseline = _sample(NOW - timedelta(seconds=40), NOW - timedelta(hours=1))
    new_worker = _sample(NOW - timedelta(seconds=5), NOW - timedelta(seconds=15))
    ok, reason = _verify(new_worker, baseline, replaced=True)
    assert ok
    assert "new heartbeat for worker 'worker'" in reason
    assert "replacement worker" in reason  # replaced container: stated


def test_scenario_d_fresh_install_passes_on_first_row() -> None:
    """No baseline (fresh install: no worker container before, a new one
    after): the row appearing at all is the evidence, provided it is fresh.
    `started_at` has no baseline to move past and is not required."""

    first = _sample(NOW - timedelta(seconds=5), NOW - timedelta(seconds=10))
    ok, reason = _verify(first, None, replaced=True)
    assert ok
    assert "first heartbeat for worker 'worker'" in reason

    stale_first = _sample(NOW - timedelta(seconds=LIMIT), NOW - timedelta(seconds=LIMIT))
    ok, reason = _verify(stale_first, None, replaced=True)
    assert not ok
    assert "stale" in reason


def test_scenario_e_same_container_reverify_needs_only_a_new_heartbeat() -> None:
    """A same-container update may not reuse the old row: identical values
    FAIL, a moved last_heartbeat passes even though started_at legitimately
    stayed put (the worker kept running)."""

    baseline = _sample(NOW - timedelta(seconds=45), NOW - timedelta(days=1))

    ok, reason = _verify(baseline, baseline, replaced=False)
    assert not ok
    assert "no NEW heartbeat" in reason

    advanced = _sample(NOW - timedelta(seconds=5), baseline.started_at)
    ok, reason = _verify(advanced, baseline, replaced=False)
    assert ok
    assert "kept running" in reason


def test_race_scenario_old_workers_post_baseline_tick_must_fail() -> None:
    """W05-AUDIT-2, the exact race: the old worker ticks once more AFTER the
    baseline was captured and before compose stops it; the replacement
    worker is silent. last_heartbeat > baseline with the container replaced
    — but started_at is still the old process's — must FAIL, because that
    tick cannot prove the replacement worker's heartbeat loop runs."""

    baseline = _sample(NOW - timedelta(seconds=40), NOW - timedelta(hours=1))
    old_worker_final_tick = _sample(
        NOW - timedelta(seconds=20),  # newer than the baseline...
        baseline.started_at,  # ...but written by the OLD process
    )
    ok, reason = _verify(old_worker_final_tick, baseline, replaced=True)
    assert not ok
    assert "old worker wrote after the baseline but the replacement worker has not" in reason
    assert "replaced" in reason


def test_race_scenario_replacement_workers_first_heartbeat_passes() -> None:
    """The same race, resolved: the replacement worker writes its own tick —
    last_heartbeat AND started_at past the baseline with the container
    replaced."""

    baseline = _sample(NOW - timedelta(seconds=40), NOW - timedelta(hours=1))
    replacement_tick = _sample(NOW - timedelta(seconds=5), NOW - timedelta(seconds=15))
    ok, reason = _verify(replacement_tick, baseline, replaced=True)
    assert ok
    assert "replacement worker" in reason
    assert "started_at advanced" in reason


def test_replaced_container_requires_started_at_to_advance() -> None:
    """Generalized: with the container replaced, any sample still carrying
    the baseline's started_at fails, no matter how fresh its heartbeat."""

    baseline = _sample(NOW - timedelta(seconds=15), NOW - timedelta(hours=1))
    fresh_but_old_process = _sample(NOW - timedelta(seconds=1), baseline.started_at)
    ok, reason = _verify(fresh_but_old_process, baseline, replaced=True)
    assert not ok
    assert "replacement worker has not" in reason


def test_heartbeat_must_be_strictly_newer_than_baseline() -> None:
    baseline = _sample(NOW - timedelta(seconds=10), NOW - timedelta(hours=1))
    same_timestamp = _sample(baseline.last_heartbeat, NOW - timedelta(seconds=5))
    for replaced in (False, True):
        ok, _reason = _verify(same_timestamp, baseline, replaced=replaced)
        assert not ok


def test_age_limit_applies_to_new_heartbeats_too() -> None:
    baseline = _sample(NOW - timedelta(seconds=LIMIT * 10), NOW - timedelta(hours=1))
    ancient_but_newer = _sample(NOW - timedelta(seconds=LIMIT), NOW - timedelta(seconds=5))
    for replaced in (False, True):
        ok, reason = _verify(ancient_but_newer, baseline, replaced=replaced)
        assert not ok
        assert f"age {LIMIT:.0f}s >= limit {LIMIT}s" in reason


def test_container_identity_decision_comes_from_the_ids() -> None:
    """The replaced/not-replaced decision compares full container IDs:
    none→new is a replacement (fresh install), none→none compares equal
    (the deploy scripts refuse that case before verifying)."""

    assert is_container_replaced("", "abc123fullid")
    assert is_container_replaced("oldfullid", "newfullid")
    assert not is_container_replaced("samefullid", "samefullid")
    assert not is_container_replaced("", "")


def test_baseline_json_round_trip() -> None:
    assert dump_baseline(None) == "{}"
    assert parse_baseline("{}") is None

    sample = _sample(
        datetime(2026, 9, 6, 11, 59, 50, 123456, tzinfo=UTC),
        datetime(2026, 9, 6, 11, 0, 0, tzinfo=UTC),
    )
    parsed = parse_baseline(dump_baseline(sample))
    assert parsed == sample


def test_baseline_json_rejects_malformed_input() -> None:
    with pytest.raises(ValueError):
        parse_baseline("not json")
    with pytest.raises(ValueError):
        parse_baseline("[1, 2]")  # JSON, but not an object
    with pytest.raises(KeyError):
        parse_baseline('{"last_heartbeat": "2026-09-06T12:00:00+00:00"}')


def test_cli_rejects_bad_usage_without_touching_the_database() -> None:
    assert main(["heartbeat_verify"]) == 2
    assert main(["heartbeat_verify", "frobnicate"]) == 2
    # baseline takes no further arguments.
    assert main(["heartbeat_verify", "baseline", "{}"]) == 2
    # verify needs exactly baseline + pre/post container IDs (W05-AUDIT-2):
    # every other shape — including the pre-W05-AUDIT-2 one-argument form —
    # is a usage error, which deploy/lib.sh's capability probe relies on.
    assert main(["heartbeat_verify", "verify"]) == 2
    assert main(["heartbeat_verify", "verify", "{}"]) == 2
    assert main(["heartbeat_verify", "verify", "{}", "pre"]) == 2
    assert main(["heartbeat_verify", "verify", "{}", "pre", "post", "extra"]) == 2
