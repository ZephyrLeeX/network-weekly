"""W05-AUDIT fix 1 + W05-AUDIT-2 + W05-PRE-ACCEPTANCE-HARDENING: the deploy
heartbeat verifier state machine.

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
- the fresh-install (D) and post-write (C) cases must pass;
- HARDENING: a baseline with NO row no longer makes the first fresh row
  pass unconditionally. `captured_at` splits the no-row case: a fresh
  install (no worker container before) still passes on the first fresh
  row, but an EXISTING worker's replaced container requires the row's
  `started_at` to be past the capture instant — the old container's
  first-ever tick after the capture must FAIL. A legacy baseline without
  `captured_at` cannot attribute the row and fails closed.

The DB-facing query scoping is pinned separately against real PostgreSQL in
`tests/integration/test_heartbeat_verify_persistence.py`, together with the
full race data sequences.
"""

from datetime import UTC, datetime, timedelta

import pytest

from backend.ops.heartbeat_verify import (
    AGE_LIMIT_INTERVALS,
    HeartbeatBaseline,
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


def _baseline(
    sample: HeartbeatSample | None,
    *,
    captured_at: datetime | None = NOW - timedelta(seconds=40),
) -> HeartbeatBaseline:
    return HeartbeatBaseline(captured_at=captured_at, sample=sample)


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
        worker_id="worker",
        pre_id=pre,
        post_id=post,
    )


def test_scenario_a_stale_row_from_previous_worker_never_passes() -> None:
    """A 10s-old row left by the PREVIOUS worker, new worker silent: FAIL —
    both when the row vanished and when it is exactly the baseline."""

    baseline = _baseline(_sample(NOW - timedelta(seconds=10), NOW - timedelta(hours=1)))
    ok, reason = _verify(None, baseline)
    assert not ok
    assert "no heartbeat row for worker 'worker'" in reason

    ok, reason = _verify(baseline.sample, baseline)  # row unchanged since baseline
    assert not ok
    assert "no NEW heartbeat" in reason


def test_scenario_a_worker_restart_alone_is_not_evidence() -> None:
    """A newer started_at (restart observed) with no newer heartbeat is
    insufficient under either container identity."""

    baseline_row = _sample(NOW - timedelta(seconds=10), NOW - timedelta(hours=1))
    baseline = _baseline(baseline_row)
    restarted_but_silent = _sample(baseline_row.last_heartbeat, NOW - timedelta(seconds=5))
    for pre, post in (("same", "same"), ("old", "new")):
        ok, reason = _verify(restarted_but_silent, baseline, pre=pre, post=post)
        assert not ok
        assert "no NEW heartbeat" in reason


def test_scenario_b_other_workers_fresh_heartbeat_does_not_pass() -> None:
    """Only the current worker id is ever queried (DB scoping pinned in the
    integration tests): for the verifier a missing own row is a failure even
    though some other worker's row is brand new — including the replaced-
    container fresh install, whose own row has not appeared yet."""

    ok, reason = _verify(None, _baseline(None), pre="", post="newcontainerid")
    assert not ok
    assert "no heartbeat row for worker 'worker'" in reason


def test_scenario_c_new_post_start_heartbeat_passes() -> None:
    baseline = _baseline(_sample(NOW - timedelta(seconds=40), NOW - timedelta(hours=1)))
    new_worker = _sample(NOW - timedelta(seconds=5), NOW - timedelta(seconds=15))
    ok, reason = _verify(new_worker, baseline, pre="oldcontainerid", post="newcontainerid")
    assert ok
    assert "new heartbeat for worker 'worker'" in reason
    assert "replacement worker" in reason  # replaced container: stated


def test_scenario_d_fresh_install_passes_on_first_row() -> None:
    """No baseline row and NO worker container before the start: the row
    appearing at all is the evidence, provided it is fresh. `started_at`
    has no capture instant to move past and is not required."""

    first = _sample(NOW - timedelta(seconds=5), NOW - timedelta(seconds=10))
    ok, reason = _verify(first, _baseline(None), pre="", post="newcontainerid")
    assert ok
    assert "first heartbeat for worker 'worker'" in reason
    assert "fresh install" in reason

    stale_first = _sample(NOW - timedelta(seconds=LIMIT), NOW - timedelta(seconds=LIMIT))
    ok, reason = _verify(stale_first, _baseline(None), pre="", post="newcontainerid")
    assert not ok
    assert "stale" in reason

    # A legacy no-row baseline ({}) keeps fresh-install semantics.
    ok, reason = _verify(
        first, _baseline(None, captured_at=None), pre="", post="newcontainerid"
    )
    assert ok
    assert "fresh install" in reason


def test_scenario_e_same_container_reverify_needs_only_a_new_heartbeat() -> None:
    """A same-container update may not reuse the old row: identical values
    FAIL, a moved last_heartbeat passes even though started_at legitimately
    stayed put (the worker kept running)."""

    baseline_row = _sample(NOW - timedelta(seconds=45), NOW - timedelta(days=1))
    baseline = _baseline(baseline_row)

    ok, reason = _verify(baseline_row, baseline)
    assert not ok
    assert "no NEW heartbeat" in reason

    advanced = _sample(NOW - timedelta(seconds=5), baseline_row.started_at)
    ok, reason = _verify(advanced, baseline)
    assert ok
    assert "kept running" in reason


def test_race_scenario_old_workers_post_baseline_tick_must_fail() -> None:
    """W05-AUDIT-2, the exact race: the old worker ticks once more AFTER the
    baseline was captured and before compose stops it; the replacement
    worker is silent. last_heartbeat > baseline with the container replaced
    — but started_at is still the old process's — must FAIL, because that
    tick cannot prove the replacement worker's heartbeat loop runs."""

    baseline_row = _sample(NOW - timedelta(seconds=40), NOW - timedelta(hours=1))
    baseline = _baseline(baseline_row)
    old_worker_final_tick = _sample(
        NOW - timedelta(seconds=20),  # newer than the baseline...
        baseline_row.started_at,  # ...but written by the OLD process
    )
    ok, reason = _verify(
        old_worker_final_tick, baseline, pre="oldcontainerid", post="newcontainerid"
    )
    assert not ok
    assert "old worker wrote after the baseline but the replacement worker has not" in reason
    assert "replaced" in reason


def test_race_scenario_replacement_workers_first_heartbeat_passes() -> None:
    """The same race, resolved: the replacement worker writes its own tick —
    last_heartbeat AND started_at past the baseline with the container
    replaced."""

    baseline = _baseline(_sample(NOW - timedelta(seconds=40), NOW - timedelta(hours=1)))
    replacement_tick = _sample(NOW - timedelta(seconds=5), NOW - timedelta(seconds=15))
    ok, reason = _verify(replacement_tick, baseline, pre="oldcontainerid", post="newcontainerid")
    assert ok
    assert "replacement worker" in reason
    assert "started_at advanced" in reason


def test_replaced_container_requires_started_at_to_advance() -> None:
    """Generalized: with the container replaced, any sample still carrying
    the baseline's started_at fails, no matter how fresh its heartbeat."""

    baseline_row = _sample(NOW - timedelta(seconds=15), NOW - timedelta(hours=1))
    baseline = _baseline(baseline_row)
    fresh_but_old_process = _sample(NOW - timedelta(seconds=1), baseline_row.started_at)
    ok, reason = _verify(
        fresh_but_old_process, baseline, pre="oldcontainerid", post="newcontainerid"
    )
    assert not ok
    assert "replacement worker has not" in reason


def test_no_row_baseline_replacement_rejects_the_old_containers_first_tick() -> None:
    """HARDENING, the no-row race: the baseline held NO heartbeat row (the
    existing worker container had not ticked since it started), so the old
    container's FIRST tick after the capture used to pass as "fresh
    install". With PRE an existing container and PRE != POST, a row whose
    started_at predates the capture instant is the OLD container's — FAIL;
    only the replacement worker's own (started_at after the capture) row
    passes."""

    captured_at = NOW - timedelta(seconds=40)
    baseline = _baseline(None, captured_at=captured_at)

    old_worker_first_tick = _sample(
        NOW - timedelta(seconds=20),  # fresh...
        captured_at - timedelta(seconds=5),  # ...but started BEFORE the capture
    )
    ok, reason = _verify(
        old_worker_first_tick, baseline, pre="oldcontainerid", post="newcontainerid"
    )
    assert not ok
    assert "old container wrote after the baseline but the replacement worker has not" in reason
    assert "captured_at" in reason

    replacement_worker_tick = _sample(
        NOW - timedelta(seconds=5), NOW - timedelta(seconds=15)
    )
    ok, reason = _verify(
        replacement_worker_tick, baseline, pre="oldcontainerid", post="newcontainerid"
    )
    assert ok
    assert "replacement worker" in reason
    assert "captured_at" in reason


def test_no_row_baseline_replacement_same_container_still_passes() -> None:
    """HARDENING keeps the legitimate no-row same-container re-verify (the
    pre-start container existed but had not ticked yet; it is still the
    container after the start): the first fresh row passes and started_at
    may predate the capture."""

    baseline = _baseline(None)
    tick = _sample(NOW - timedelta(seconds=5), NOW - timedelta(minutes=10))
    ok, reason = _verify(tick, baseline, pre="samecontainerid", post="samecontainerid")
    assert ok
    assert "worker container unchanged" in reason


def test_no_row_legacy_baseline_replacement_fails_closed() -> None:
    """A legacy `{}` baseline has no captured_at: with an existing worker's
    container replaced there is no way to attribute the first row to the
    replacement worker rather than to the pre-capture container — FAIL with
    an actionable reason, never a false "fresh install" pass. The legacy
    format must still PARSE (rollback compatibility), not crash."""

    baseline = parse_baseline("{}")
    assert baseline == HeartbeatBaseline(captured_at=None, sample=None)

    tick = _sample(NOW - timedelta(seconds=5), NOW - timedelta(seconds=15))
    ok, reason = _verify(tick, baseline, pre="oldcontainerid", post="newcontainerid")
    assert not ok
    assert "no captured_at" in reason
    assert "legacy" in reason

    # The same legacy baseline keeps fresh-install semantics without a PRE
    # container and same-container semantics when nothing was replaced.
    ok, reason = _verify(tick, baseline, pre="", post="newcontainerid")
    assert ok
    ok, reason = _verify(tick, baseline, pre="samecontainerid", post="samecontainerid")
    assert ok


def test_heartbeat_must_be_strictly_newer_than_baseline() -> None:
    baseline_row = _sample(NOW - timedelta(seconds=10), NOW - timedelta(hours=1))
    baseline = _baseline(baseline_row)
    same_timestamp = _sample(baseline_row.last_heartbeat, NOW - timedelta(seconds=5))
    for pre, post in (("same", "same"), ("old", "new")):
        ok, _reason = _verify(same_timestamp, baseline, pre=pre, post=post)
        assert not ok


def test_age_limit_applies_to_new_heartbeats_too() -> None:
    baseline = _baseline(_sample(NOW - timedelta(seconds=LIMIT * 10), NOW - timedelta(hours=1)))
    ancient_but_newer = _sample(NOW - timedelta(seconds=LIMIT), NOW - timedelta(seconds=5))
    for pre, post in (("same", "same"), ("old", "new")):
        ok, reason = _verify(ancient_but_newer, baseline, pre=pre, post=post)
        assert not ok
        assert f"age {LIMIT:.0f}s >= limit {LIMIT}s" in reason

    # The age limit also bounds a no-row replacement worker's first row.
    ancient_first = _sample(NOW - timedelta(seconds=LIMIT), NOW - timedelta(seconds=5))
    ok, reason = _verify(
        ancient_first, _baseline(None), pre="oldcontainerid", post="newcontainerid"
    )
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
    captured_at = datetime(2026, 9, 6, 11, 59, 20, tzinfo=UTC)
    assert parse_baseline(dump_baseline(None, captured_at=captured_at)) == (
        HeartbeatBaseline(captured_at=captured_at, sample=None)
    )

    sample = _sample(
        datetime(2026, 9, 6, 11, 59, 50, 123456, tzinfo=UTC),
        datetime(2026, 9, 6, 11, 0, 0, tzinfo=UTC),
    )
    parsed = parse_baseline(dump_baseline(sample, captured_at=captured_at))
    assert parsed == HeartbeatBaseline(captured_at=captured_at, sample=sample)


def test_baseline_json_parses_every_legacy_format() -> None:
    """Backward compatibility: a pre-hardening baseline (`{}` or
    `{last_heartbeat, started_at}`) parses instead of crashing a rollback —
    the verifier then fails the unprovable replacement case closed."""

    legacy_empty = parse_baseline("{}")
    assert legacy_empty.captured_at is None
    assert legacy_empty.sample is None

    legacy_row = parse_baseline(
        '{"last_heartbeat": "2026-09-06T11:59:50+00:00", '
        '"started_at": "2026-09-06T11:00:00+00:00"}'
    )
    assert legacy_row.captured_at is None
    assert legacy_row.sample == _sample(
        datetime(2026, 9, 6, 11, 59, 50, tzinfo=UTC),
        datetime(2026, 9, 6, 11, 0, 0, tzinfo=UTC),
    )

    no_row = parse_baseline('{"captured_at": "2026-09-06T11:59:20+00:00"}')
    assert no_row.captured_at == datetime(2026, 9, 6, 11, 59, 20, tzinfo=UTC)
    assert no_row.sample is None


def test_baseline_json_rejects_malformed_input() -> None:
    with pytest.raises(ValueError):
        parse_baseline("not json")
    with pytest.raises(ValueError):
        parse_baseline("[1, 2]")  # JSON, but not an object
    with pytest.raises(ValueError):
        # A started_at without its last_heartbeat is a corrupt baseline.
        parse_baseline('{"started_at": "2026-09-06T12:00:00+00:00"}')
    with pytest.raises(KeyError):
        parse_baseline('{"last_heartbeat": "2026-09-06T12:00:00+00:00"}')
    with pytest.raises(ValueError):
        parse_baseline('{"captured_at": "not a timestamp"}')


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
