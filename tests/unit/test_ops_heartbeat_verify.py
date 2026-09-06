"""W05-AUDIT fix 1: the deploy heartbeat verifier state machine.

`backend.ops.heartbeat_verify` must only accept a heartbeat written by THIS
worker AFTER the baseline captured before the start/update — a stale row
from a previous worker (scenarios A/B/E) must never pass, while the
legitimate fresh-install (D) and post-write (C/E) cases must.

The DB-facing query scoping is pinned separately against real PostgreSQL in
`tests/integration/test_ops_heartbeat_verify.py`.
"""

from datetime import UTC, datetime, timedelta

import pytest

from backend.ops.heartbeat_verify import (
    AGE_LIMIT_INTERVALS,
    HeartbeatSample,
    dump_baseline,
    main,
    parse_baseline,
    verify_heartbeat,
)

NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
INTERVAL = 30
LIMIT = INTERVAL * AGE_LIMIT_INTERVALS  # 120s


def _sample(last_heartbeat: datetime, started_at: datetime) -> HeartbeatSample:
    return HeartbeatSample(last_heartbeat=last_heartbeat, started_at=started_at)


def _verify(sample: HeartbeatSample | None, baseline: HeartbeatSample | None) -> tuple[bool, str]:
    return verify_heartbeat(
        sample, baseline, now=NOW, interval_seconds=INTERVAL, worker_id="worker"
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
    supplementary evidence only — it must not satisfy the verifier."""

    baseline = _sample(NOW - timedelta(seconds=10), NOW - timedelta(hours=1))
    restarted_but_silent = _sample(baseline.last_heartbeat, NOW - timedelta(seconds=5))
    ok, reason = _verify(restarted_but_silent, baseline)
    assert not ok
    assert "no NEW heartbeat" in reason


def test_scenario_b_other_workers_fresh_heartbeat_does_not_pass() -> None:
    """Only the current worker id is ever queried (DB scoping pinned in the
    integration tests): for the verifier a missing own row is a failure even
    though some other worker's row is brand new."""

    ok, reason = _verify(None, None)
    assert not ok
    assert "no heartbeat row for worker 'worker'" in reason


def test_scenario_c_new_post_start_heartbeat_passes() -> None:
    baseline = _sample(NOW - timedelta(seconds=40), NOW - timedelta(hours=1))
    new_worker = _sample(NOW - timedelta(seconds=5), NOW - timedelta(seconds=15))
    ok, reason = _verify(new_worker, baseline)
    assert ok
    assert "new heartbeat for worker 'worker'" in reason
    assert "restarted" in reason  # started_at moved: restart is reported


def test_scenario_d_fresh_install_passes_on_first_row() -> None:
    """No baseline (fresh install): the row appearing at all is the
    evidence, provided it is fresh."""

    first = _sample(NOW - timedelta(seconds=5), NOW - timedelta(seconds=10))
    ok, reason = _verify(first, None)
    assert ok
    assert "first heartbeat for worker 'worker'" in reason

    stale_first = _sample(NOW - timedelta(seconds=LIMIT), NOW - timedelta(seconds=LIMIT))
    ok, reason = _verify(stale_first, None)
    assert not ok
    assert "stale" in reason


def test_scenario_e_same_image_update_still_requires_a_new_heartbeat() -> None:
    """A same-image update may not reuse the old row: identical values FAIL,
    a moved last_heartbeat passes even though the worker kept running."""

    baseline = _sample(NOW - timedelta(seconds=45), NOW - timedelta(days=1))

    ok, reason = _verify(baseline, baseline)
    assert not ok
    assert "no NEW heartbeat" in reason

    advanced = _sample(NOW - timedelta(seconds=5), baseline.started_at)
    ok, reason = _verify(advanced, baseline)
    assert ok
    assert "kept running" in reason


def test_heartbeat_must_be_strictly_newer_than_baseline() -> None:
    baseline = _sample(NOW - timedelta(seconds=10), NOW - timedelta(hours=1))
    same_timestamp = _sample(baseline.last_heartbeat, NOW - timedelta(seconds=5))
    ok, _reason = _verify(same_timestamp, baseline)
    assert not ok


def test_age_limit_applies_to_new_heartbeats_too() -> None:
    baseline = _sample(NOW - timedelta(seconds=LIMIT * 10), NOW - timedelta(hours=1))
    ancient_but_newer = _sample(NOW - timedelta(seconds=LIMIT), NOW - timedelta(seconds=5))
    ok, reason = _verify(ancient_but_newer, baseline)
    assert not ok
    assert f"age {LIMIT:.0f}s >= limit {LIMIT}s" in reason


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
    assert main(["heartbeat_verify", "verify"]) == 2
    assert main(["heartbeat_verify", "frobnicate"]) == 2
    assert main(["heartbeat_verify", "verify", "{}", "extra"]) == 2
