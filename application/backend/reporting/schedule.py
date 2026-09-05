"""Worker report loop: Monday 00:10 schedule, retry, restart recovery
(W03-T009, SYSTEM_SPEC.md §4/§27).

One pass, every minute:

1. recover `running` jobs stranded by a previous worker (§4.2/§27.1) —
   done once at loop start;
2. if `now >= last Monday 00:00 + 10 min` (== Monday 00:10 Asia/Shanghai,
   §4.1), idempotently ensure the persistent job for the just-completed
   week exists (§4.2: the responsibility is the DB row, so a worker that
   was down at 00:10 creates the job on its next pass);
3. run every due job — pending, or failed with `next_retry_at <= now`
   (the 10-minute retry, §4.3) — one at a time, oldest first (§27: never
   two concurrent generations; the loop thread runs them inline).

A pass that fails (database blip) is contained: the loop logs and keeps
cycling (§27.9).
"""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from backend.reporting.jobs import (
    GENERATION_DELAY,
    ReportOutcome,
    due_jobs,
    ensure_scheduled_job,
    execute_report_job,
    recover_stale_running_jobs,
)
from backend.reporting.period import BUSINESS_TIMEZONE, previous_period

logger = logging.getLogger(__name__)

# How often the loop wakes to check schedule/retry state.
DEFAULT_CHECK_INTERVAL = timedelta(seconds=60)


def is_due_for_schedule(now: datetime, period_end: datetime) -> bool:
    """True once `now` passed the §4.1 generation instant (Monday 00:10)."""

    return now >= period_end + GENERATION_DELAY


class WeeklyReportLoop:
    """Owns the worker side of weekly report generation (§25)."""

    def __init__(
        self,
        session_factory: sessionmaker,
        output_dir: Path,
        *,
        check_interval: timedelta = DEFAULT_CHECK_INTERVAL,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], bool] | None = None,
        execute: Callable[..., ReportOutcome] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._output_dir = Path(output_dir)
        self._check_interval = check_interval
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._execute = execute if execute is not None else execute_report_job
        self._recovered = False

    def recover_once(self) -> int:
        """Reset jobs stuck in `running` from a previous worker (§4.2)."""

        with self._session_factory() as session:
            recovered = recover_stale_running_jobs(session, now=self._clock())
        if recovered:
            logger.info("recovered %d interrupted report job(s)", recovered)
        return recovered

    def ensure_scheduled(self) -> bool:
        """Ensure the completed week's job exists once past Monday 00:10."""

        now = self._clock()
        period = previous_period(now, BUSINESS_TIMEZONE)
        if not is_due_for_schedule(now, period.end):
            return False
        with self._session_factory() as session:
            job = ensure_scheduled_job(session, period, now=now)
        if job is not None and job.attempts == 0:
            logger.info("report job %d scheduled for %s", job.id, period.week_code)
            return True
        return False

    def run_due(self) -> int:
        """Execute all due report jobs, one at a time; returns run count."""

        ran = 0
        while True:
            with self._session_factory() as session:
                pending = due_jobs(session, now=self._clock())
            if not pending:
                return ran
            job = pending[0]
            logger.info(
                "executing report job %d (week %s, attempt %d)",
                job.id,
                job.week_code,
                job.attempts + 1,
            )
            self._execute(self._session_factory, job.id, self._output_dir)
            ran += 1

    def run_pass(self) -> None:
        """One schedule+retry pass; failures contained (§27.9)."""

        try:
            self.ensure_scheduled()
            self.run_due()
        except Exception as exc:  # noqa: BLE001 — keep the loop alive
            logger.error("weekly report pass failed (%s)", type(exc).__name__)

    def run_loop(self, stop: threading.Event) -> None:
        logger.info(
            "weekly report loop started (check interval %d s)",
            int(self._check_interval.total_seconds()),
        )
        try:
            self.recover_once()
        except Exception as exc:  # noqa: BLE001 — recovery retries next pass
            logger.error("report job recovery failed (%s)", type(exc).__name__)
        while not stop.is_set():
            self.run_pass()
            if self._sleep is not None:
                if not self._sleep(self._check_interval.total_seconds()):
                    break
            elif stop.wait(timeout=self._check_interval.total_seconds()):
                break
        logger.info("weekly report loop stopped")
