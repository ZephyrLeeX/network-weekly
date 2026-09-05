"""Unit tests for the W02-T002 five-minute DEVICE_POLL scheduler.

Timing semantics are exercised with injected clocks/sleeps: boundary
alignment, no same-device overlap, restart-continues-future (no backfill)
and per-device failure isolation.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from backend.collect.snmp import SnmpConfig
from backend.monitoring.poll import DevicePollContext
from backend.monitoring.scheduler import DevicePollScheduler, align_up

BASE = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)


def _ctx(name: str = "dev-1") -> DevicePollContext:
    return DevicePollContext(
        device_id=1,
        device_name=name,
        snmp=SnmpConfig(host="192.0.2.1", community="public"),
    )


class FakeClock:
    """Steerable clock for deterministic scheduler timing."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def __call__(self) -> datetime:
        return self.now


def test_align_up_on_boundary_returns_itself() -> None:
    assert align_up(BASE) == BASE


def test_align_up_mid_interval() -> None:
    assert align_up(BASE + timedelta(seconds=1)) == BASE + timedelta(minutes=5)
    assert align_up(BASE + timedelta(minutes=4, seconds=59)) == BASE + timedelta(minutes=5)


def test_align_up_requires_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        align_up(datetime(2026, 9, 5, 8, 0, 0))


def test_next_cycle_is_strictly_in_future() -> None:
    clock = FakeClock(BASE + timedelta(seconds=30))
    scheduler = DevicePollScheduler(load_devices=lambda: [], clock=clock)
    first = scheduler.next_cycle()
    assert first == BASE + timedelta(minutes=5)

    # After "executing" a cycle the next one never repeats the same boundary.
    scheduler._last_cycle = first  # noqa: SLF001  (white-box timing guard)
    clock.advance(1)
    assert scheduler.next_cycle() == BASE + timedelta(minutes=10)


def test_run_loop_starts_at_next_boundary_without_backfill() -> None:
    """A scheduler started mid-interval plans the next boundary, never the past."""

    clock = FakeClock(BASE + timedelta(seconds=90))
    stop = threading.Event()
    waits: list[datetime] = []

    def sleep_until(target: datetime) -> bool:
        waits.append(target)
        return False  # request stop on the first wait

    DevicePollScheduler(lambda: [], clock=clock, sleep_until=sleep_until).run_loop(stop)

    assert waits == [BASE + timedelta(minutes=5)]
    assert not any(target < clock.now for target in waits)  # nothing in the past


def test_run_loop_executes_cycles_until_stop() -> None:
    clock = FakeClock(BASE + timedelta(seconds=90))
    stop = threading.Event()
    waits: list[datetime] = []
    executed: list[datetime] = []

    def sleep_until(target: datetime) -> bool:
        waits.append(target)
        clock.advance((target - clock.now).total_seconds())
        return len(waits) < 3  # third wait requests stop

    def record_poll(ctx: DevicePollContext, cycle: datetime) -> str:
        executed.append(cycle)
        return "SUCCESS"

    scheduler = DevicePollScheduler(
        lambda: [_ctx("a")], clock=clock, sleep_until=sleep_until, poll=record_poll
    )
    scheduler.run_loop(stop)

    assert executed == [BASE + timedelta(minutes=5), BASE + timedelta(minutes=10)]
    assert waits[-1] == BASE + timedelta(minutes=15)


def test_run_cycle_polls_every_device() -> None:
    polls: list[tuple[str, datetime]] = []
    devices = [_ctx("a"), _ctx("b"), _ctx("c")]
    executor = ThreadPoolExecutor(max_workers=3)

    def record_poll(ctx: DevicePollContext, cycle: datetime) -> str:
        polls.append((ctx.device_name, cycle))
        return "SUCCESS"

    scheduler = DevicePollScheduler(lambda: devices, executor=executor, poll=record_poll)
    try:
        scheduler.run_cycle(BASE)
    finally:
        executor.shutdown(wait=True)
    assert sorted(polls) == [("a", BASE), ("b", BASE), ("c", BASE)]


def test_run_cycle_skips_device_still_in_flight() -> None:
    """A poll outliving its cycle is never started twice (§27.11)."""

    release = threading.Event()
    first_started = threading.Event()
    polls: list[tuple[str, datetime]] = []

    def blocking_poll(ctx: DevicePollContext, cycle: datetime) -> str:
        polls.append((ctx.device_name, cycle))
        if ctx.device_name == "slow":
            first_started.set()
            release.wait(timeout=5)
        return "SUCCESS"

    devices = [_ctx("slow"), _ctx("fast")]
    executor = ThreadPoolExecutor(max_workers=4)
    scheduler = DevicePollScheduler(
        lambda: devices,
        executor=executor,
        poll=blocking_poll,
    )
    try:
        scheduler.run_cycle(BASE)
        assert first_started.wait(timeout=5)
        scheduler._in_flight["fast"].result(timeout=5)  # noqa: SLF001

        # Next cycle: "slow" is still running -> skipped; "fast" runs.
        cycle2 = BASE + timedelta(minutes=5)
        scheduler.run_cycle(cycle2)
        scheduler._in_flight["fast"].result(timeout=5)  # noqa: SLF001

        release.set()
        scheduler._in_flight["slow"].result(timeout=5)  # noqa: SLF001

        # Cycle 3: "slow" is free again and must be polled once more.
        cycle3 = BASE + timedelta(minutes=10)
        scheduler.run_cycle(cycle3)
        scheduler._in_flight["slow"].result(timeout=5)  # noqa: SLF001
    finally:
        release.set()
        executor.shutdown(wait=True)

    slow_cycles = [cycle for name, cycle in polls if name == "slow"]
    fast_cycles = [cycle for name, cycle in polls if name == "fast"]
    assert slow_cycles == [BASE, cycle3]  # cycle2 skipped, no overlap
    assert fast_cycles == [BASE, cycle2, cycle3]


def test_poll_exception_is_contained_per_device() -> None:
    polls: list[str] = []

    def failing_poll(ctx: DevicePollContext, cycle: datetime) -> str:
        polls.append(ctx.device_name)
        if ctx.device_name == "bad":
            raise RuntimeError("boom")
        return "SUCCESS"

    devices = [_ctx("bad"), _ctx("good")]
    executor = ThreadPoolExecutor(max_workers=2)
    scheduler = DevicePollScheduler(
        lambda: devices,
        executor=executor,
        poll=failing_poll,
    )
    try:
        scheduler.run_cycle(BASE)
    finally:
        executor.shutdown(wait=True)

    # Both devices were attempted despite "bad" raising.
    assert sorted(polls) == ["bad", "good"]


def test_load_devices_failure_aborts_cycle_gracefully() -> None:
    def broken_loader() -> list[DevicePollContext]:
        raise RuntimeError("db unavailable")

    executor = ThreadPoolExecutor(max_workers=1)
    scheduler = DevicePollScheduler(
        broken_loader,
        executor=executor,
        poll=lambda ctx, cycle: "SUCCESS",
    )
    # Must not raise (§27.9: keep looping when PostgreSQL blips).
    scheduler.run_cycle(BASE)
    executor.shutdown(wait=True)


def test_fresh_scheduler_instance_does_not_backfill() -> None:
    """Restart semantics (§27.12): a restarted worker never runs past cycles."""

    clock = FakeClock(BASE + timedelta(minutes=47))
    scheduler = DevicePollScheduler(load_devices=lambda: [], clock=clock)
    # The first planned cycle is the next future boundary only.
    assert scheduler.next_cycle() == BASE + timedelta(minutes=50)
