"""Unit tests for the IRF observation loop timing (W02-T008)."""

import threading
from datetime import UTC, datetime, timedelta

from backend.collect.ssh import SshConfig
from backend.monitoring.irf import (
    IRF_OBSERVATION_INTERVAL,
    IrfDeviceContext,
    IrfObservationLoop,
    IrfObservationReport,
)

BASE = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def _context(name: str = "irf-a") -> IrfDeviceContext:
    return IrfDeviceContext(
        device_id=1,
        device_name=name,
        ssh=SshConfig(host="192.0.2.1", username="monitor", password="s3cret"),
    )


def _report(device_id: int = 1) -> IrfObservationReport:
    return IrfObservationReport(
        device_id=device_id, observed_members=(1, 2), missing_members=()
    )


def test_interval_is_fifteen_minutes() -> None:
    """§7.3: the IRF cadence is ~15 minutes."""

    assert IRF_OBSERVATION_INTERVAL == timedelta(seconds=900)


def test_pass_observes_every_loaded_device() -> None:
    observed: list[str] = []
    devices = [_context("irf-a"), _context("irf-b")]

    def observe(ctx: IrfDeviceContext) -> IrfObservationReport:
        observed.append(ctx.device_name)
        return _report()

    loop = IrfObservationLoop(lambda: devices, observe=observe)
    count = loop.run_pass()
    assert count == 2
    assert observed == ["irf-a", "irf-b"]


def test_pass_is_contained_when_observation_raises() -> None:
    observed: list[str] = []

    def observe(ctx: IrfDeviceContext) -> IrfObservationReport | None:
        observed.append(ctx.device_name)
        if ctx.device_name == "broken":
            raise RuntimeError("boom")
        return _report()

    devices = [_context("broken"), _context("healthy")]
    IrfObservationLoop(lambda: devices, observe=observe).run_pass()
    # The second device is still observed despite the first raising.
    assert observed == ["broken", "healthy"]


def test_run_loop_executes_passes_at_boundaries_until_stop() -> None:
    clock = FakeClock(BASE + timedelta(seconds=60))
    stop = threading.Event()
    passes = 0
    targets: list[datetime] = []

    def sleep_until(target: datetime) -> bool:
        nonlocal passes
        targets.append(target)
        passes += 1
        clock.now = target
        return passes < 3  # third wait requests stop

    loop = IrfObservationLoop(
        lambda: [_context()], clock=clock, sleep_until=sleep_until, observe=lambda ctx: _report()
    )
    loop.run_loop(stop)

    # 15-minute aligned boundaries only.
    assert targets == [
        BASE + timedelta(minutes=15),
        BASE + timedelta(minutes=30),
        BASE + timedelta(minutes=45),
    ]
