"""Unit tests for the §9 device reachability transition core (W02-T004)."""

from datetime import UTC, datetime, timedelta

from backend.monitoring.reachability import (
    STATE_DOWN,
    STATE_NORMAL,
    CycleTracking,
    ReachabilityObservation,
    advance_cycle_state,
)

T0 = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
STEP = timedelta(seconds=300)
FRESH = CycleTracking()


def _failed(cycle: datetime) -> ReachabilityObservation:
    return ReachabilityObservation(cycle_started_at=cycle, snmp_ok=False, ssh_reachable=False)


def _snmp_ok(cycle: datetime) -> ReachabilityObservation:
    return ReachabilityObservation(cycle_started_at=cycle, snmp_ok=True, ssh_reachable=None)


def _ssh_only(cycle: datetime) -> ReachabilityObservation:
    return ReachabilityObservation(cycle_started_at=cycle, snmp_ok=False, ssh_reachable=True)


def test_two_consecutive_failed_cycles_confirm_down() -> None:
    d1 = advance_cycle_state(FRESH, _failed(T0))
    assert d1.tracking.state == STATE_NORMAL
    assert d1.tracking.consecutive_failed_cycles == 1
    assert d1.incident_started_at is None  # one failure is not yet a Down

    d2 = advance_cycle_state(d1.tracking, _failed(T0 + STEP))
    assert d2.tracking.state == STATE_DOWN
    assert d2.incident_started_at == T0  # Down began with the FIRST failed cycle


def test_single_failed_cycle_does_not_create_incident() -> None:
    d = advance_cycle_state(FRESH, _failed(T0))
    assert d.incident_started_at is None
    # And a later isolated failure after a reachable cycle still does not.
    d2 = advance_cycle_state(d.tracking, _snmp_ok(T0 + STEP))
    d3 = advance_cycle_state(d2.tracking, _failed(T0 + 2 * STEP))
    assert d3.incident_started_at is None
    assert d3.tracking.state == STATE_NORMAL


def test_gap_between_failed_cycles_resets_the_run() -> None:
    d1 = advance_cycle_state(FRESH, _failed(T0))
    # A cycle is SKIPPED (no poll processed for T0+STEP).
    d2 = advance_cycle_state(d1.tracking, _failed(T0 + 2 * STEP))
    assert d2.tracking.state == STATE_NORMAL
    assert d2.incident_started_at is None
    assert d2.tracking.consecutive_failed_cycles == 1  # restarted the run


def test_ssh_reachable_snmp_failed_is_not_a_failed_cycle() -> None:
    d1 = advance_cycle_state(FRESH, _failed(T0))
    d2 = advance_cycle_state(d1.tracking, _ssh_only(T0 + STEP))
    assert d2.tracking.state == STATE_NORMAL
    assert d2.tracking.consecutive_failed_cycles == 0  # run interrupted (§9.1.4)
    assert d2.incident_started_at is None


def test_recovery_requires_two_reachable_cycles() -> None:
    d1 = advance_cycle_state(FRESH, _failed(T0))
    d2 = advance_cycle_state(d1.tracking, _failed(T0 + STEP))
    assert d2.tracking.state == STATE_DOWN

    # First reachable cycle: still Down.
    r1 = advance_cycle_state(d2.tracking, _snmp_ok(T0 + 2 * STEP))
    assert r1.tracking.state == STATE_DOWN
    assert r1.incident_recovered_at is None
    assert r1.tracking.consecutive_reachable_cycles == 1

    # Second consecutive reachable cycle: RECOVERED.
    r2 = advance_cycle_state(r1.tracking, _snmp_ok(T0 + 3 * STEP))
    assert r2.tracking.state == STATE_NORMAL
    assert r2.incident_recovered_at == T0 + 2 * STEP  # recovery began at first
    assert r2.tracking.consecutive_reachable_cycles == 0


def test_ssh_only_recovery_ends_down_but_counts() -> None:
    """§9.3: SSH recovery confirms reachable; SNMP data stays missing."""

    d1 = advance_cycle_state(FRESH, _failed(T0))
    d2 = advance_cycle_state(d1.tracking, _failed(T0 + STEP))
    r1 = advance_cycle_state(d2.tracking, _ssh_only(T0 + 2 * STEP))
    assert r1.tracking.state == STATE_DOWN
    r2 = advance_cycle_state(r1.tracking, _ssh_only(T0 + 3 * STEP))
    assert r2.tracking.state == STATE_NORMAL
    assert r2.incident_recovered_at == T0 + 2 * STEP


def test_failed_cycle_during_down_keeps_incident_open_and_interrupts_recovery() -> None:
    d1 = advance_cycle_state(FRESH, _failed(T0))
    d2 = advance_cycle_state(d1.tracking, _failed(T0 + STEP))
    r1 = advance_cycle_state(d2.tracking, _snmp_ok(T0 + 2 * STEP))
    f = advance_cycle_state(r1.tracking, _failed(T0 + 3 * STEP))
    assert f.tracking.state == STATE_DOWN
    assert f.incident_recovered_at is None
    assert f.tracking.consecutive_reachable_cycles == 0  # recovery run broken

    # Recovery must now count two fresh reachable cycles.
    r2 = advance_cycle_state(f.tracking, _snmp_ok(T0 + 4 * STEP))
    assert r2.tracking.state == STATE_DOWN
    r3 = advance_cycle_state(r2.tracking, _snmp_ok(T0 + 5 * STEP))
    assert r3.tracking.state == STATE_NORMAL
    assert r3.incident_recovered_at == T0 + 4 * STEP


def test_gap_during_recovery_run_resets_the_count() -> None:
    d1 = advance_cycle_state(FRESH, _failed(T0))
    d2 = advance_cycle_state(d1.tracking, _failed(T0 + STEP))
    r1 = advance_cycle_state(d2.tracking, _snmp_ok(T0 + 2 * STEP))
    # Skip T0+3*STEP, then a reachable cycle: the run restarts from 1.
    r2 = advance_cycle_state(r1.tracking, _snmp_ok(T0 + 4 * STEP))
    assert r2.tracking.state == STATE_DOWN
    assert r2.tracking.consecutive_reachable_cycles == 1


def test_state_down_survives_until_confirmed_even_across_cycles() -> None:
    """Down state persists until 2 reachable cycles confirm recovery."""

    d1 = advance_cycle_state(FRESH, _failed(T0))
    d2 = advance_cycle_state(d1.tracking, _failed(T0 + STEP))
    # Device stays down over many failed cycles.
    current = d2
    for i in range(3, 8):
        current = advance_cycle_state(current.tracking, _failed(T0 + i * STEP))
        assert current.tracking.state == STATE_DOWN
        assert current.incident_started_at is None  # no duplicate incident signal
