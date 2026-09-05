"""Aligned five-minute DEVICE_POLL scheduler (W02-T002, SYSTEM_SPEC.md §7.1/§27).

Invariants:

- Cycles are planned on wall-clock 5-minute boundaries; every device gets at
  most one planned poll per boundary, and a poll that outlives its cycle is
  never started twice (`_in_flight` per device). A device whose previous poll
  is still running is skipped for the new cycle — that cycle stays missing
  (no poll-run row) instead of overlapping or being faked (§27.11).
- Restart never backfills (§27.12): the loop always targets the *next future*
  boundary. Missed cycles stay missed; weekly Coverage honestly reflects them
  (§18.1 counts planned cycles, not executed ones).
- One device's failure never blocks the others: each poll runs on its own
  executor thread and every exception is contained and logged (§8/§27.7).

The loop takes its clock, sleep and executor as injectables so the timing
semantics are unit-testable without real time.
"""

import logging
import math
import threading
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from backend.monitoring.poll import DevicePollContext, poll_device

logger = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(seconds=300)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def align_up(now: datetime, interval: timedelta = POLL_INTERVAL) -> datetime:
    """The interval boundary at or after `now` (epoch-aligned, TZ-agnostic)."""

    if now.tzinfo is None:
        raise ValueError("align_up requires a timezone-aware datetime")
    seconds = interval.total_seconds()
    steps = math.ceil((now - _EPOCH).total_seconds() / seconds)
    return _EPOCH + timedelta(seconds=steps * seconds)


class DevicePollScheduler:
    """Runs one planned DEVICE_POLL cycle per 5-minute boundary."""

    def __init__(
        self,
        load_devices: Callable[[], list[DevicePollContext]],
        *,
        interval: timedelta = POLL_INTERVAL,
        clock: Callable[[], datetime] | None = None,
        sleep_until: Callable[[datetime], bool] | None = None,
        executor: Executor | None = None,
        poll: Callable[[DevicePollContext, datetime], str] = poll_device,
    ) -> None:
        self._load_devices = load_devices
        self._interval = interval
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep_until = sleep_until
        self._executor = executor
        # An internally created executor is drained on loop exit; an injected
        # one belongs to its caller.
        self._owns_executor = executor is None
        self._poll = poll
        self._in_flight: dict[str, Future[str]] = {}
        self._last_cycle: datetime | None = None

    def _ensure_executor(self) -> Executor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=32, thread_name_prefix="device-poll"
            )
        return self._executor

    def _wait_until(self, target: datetime, stop: threading.Event) -> bool:
        """Sleep until `target`; return False when a stop was requested."""

        if self._sleep_until is not None:
            return self._sleep_until(target)
        remaining = (target - self._clock()).total_seconds()
        return not stop.wait(timeout=max(0.0, remaining))

    def next_cycle(self) -> datetime:
        """The next planned cycle boundary, strictly after the last executed one."""

        cycle = align_up(self._clock(), self._interval)
        if self._last_cycle is not None and cycle <= self._last_cycle:
            cycle += self._interval
        return cycle

    def run_cycle(self, cycle: datetime) -> None:
        """Start one poll per enabled device that is not already in flight."""

        try:
            devices = self._load_devices()
        except Exception as exc:  # noqa: BLE001  (cycle must survive config/DB trouble)
            logger.error("device poll cycle %s aborted (%s)", cycle, type(exc).__name__)
            return

        executor = self._ensure_executor()
        skipped: list[str] = []
        for context in devices:
            previous = self._in_flight.get(context.device_name)
            if previous is not None and not previous.done():
                skipped.append(context.device_name)
                continue
            self._in_flight[context.device_name] = executor.submit(
                self._run_poll, context, cycle
            )
        if skipped:
            logger.warning(
                "cycle %s: previous poll still running, skipped for %s",
                cycle,
                ", ".join(sorted(skipped)),
            )
        logger.info("cycle %s: scheduled %d device poll(s)", cycle, len(devices) - len(skipped))

    def _run_poll(self, context: DevicePollContext, cycle: datetime) -> str:
        try:
            return self._poll(context, cycle)
        except Exception as exc:  # noqa: BLE001  (isolated per device; class name only)
            logger.error(
                "device poll for %s cycle %s failed (%s)",
                context.device_name,
                cycle,
                type(exc).__name__,
            )
            return "ERROR"

    def run_loop(self, stop: threading.Event) -> None:
        """Plan cycles until `stop` is set; never backfills missed cycles (§27.12)."""

        logger.info(
            "device poll scheduler started (interval %d s)", int(self._interval.total_seconds())
        )
        while not stop.is_set():
            cycle = self.next_cycle()
            if not self._wait_until(cycle, stop):
                break
            self._last_cycle = cycle
            self.run_cycle(cycle)
        if self._owns_executor and isinstance(self._executor, ThreadPoolExecutor):
            # Graceful shutdown: let in-flight polls finish before exit.
            self._executor.shutdown(wait=True)
        logger.info("device poll scheduler stopped")
