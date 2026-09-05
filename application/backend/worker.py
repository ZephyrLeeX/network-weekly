"""Worker process entrypoint, run as `python -m backend.worker`.

Wave 0 scope: process lifecycle plus the persistent heartbeat loop
(SYSTEM_SPEC.md §25). Wave 2 adds the five-minute DEVICE_POLL scheduler
(W02-T002) alongside the heartbeat; IRF observation, retention, weekly
statistics, DOCX generation and report retry join in later Wave 2/3 tasks.

Every loop is a thread on one stop event: a failing loop (database blip,
secrets trouble) logs and keeps the process alive (§27.9) so the other
responsibilities continue.
"""

import logging
import signal
import socket
import threading
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from backend import __version__
from backend.config import load_settings
from backend.db.engine import get_session_factory
from backend.heartbeat import build_heartbeat_info, record_heartbeat
from backend.log import setup_logging
from backend.monitoring.credentials import build_contexts, build_irf_contexts
from backend.monitoring.irf import IrfDeviceContext, IrfObservationLoop
from backend.monitoring.poll import DevicePollContext
from backend.monitoring.retention import run_retention
from backend.monitoring.scheduler import DevicePollScheduler
from backend.secrets import load_secrets

logger = logging.getLogger(__name__)

# Retention passes are cheap when nothing is expired; a restart simply
# re-runs the pass, so the schedule needs no persistence.
RETENTION_FIRST_DELAY_SECONDS = 60
RETENTION_PASS_INTERVAL_SECONDS = 6 * 3600


def _retention_loop(
    stop: threading.Event,
    session_factory: sessionmaker,
    retention_days: int,
    first_delay_seconds: int = RETENTION_FIRST_DELAY_SECONDS,
    interval_seconds: int = RETENTION_PASS_INTERVAL_SECONDS,
) -> None:
    """Run the batched §24 cleanup pass periodically until stopped."""

    stop.wait(first_delay_seconds)
    while not stop.is_set():
        try:
            run_retention(
                session_factory, now=datetime.now(UTC), retention_days=retention_days
            )
        except SQLAlchemyError as exc:
            logger.warning("retention pass failed (%s), retrying next interval", type(exc).__name__)
        except ValueError as exc:
            logger.error("retention configuration rejected: %s", exc)
            return
        stop.wait(interval_seconds)


def _heartbeat_loop(
    stop: threading.Event,
    session_factory: sessionmaker,
    worker_id: str,
    started_at: datetime,
    hostname: str,
    interval_seconds: int,
) -> None:
    """Persist the worker heartbeat every interval until stopped (§25)."""

    while not stop.is_set():
        try:
            with session_factory() as session:
                info = build_heartbeat_info(
                    worker_id=worker_id,
                    version=__version__,
                    started_at=started_at,
                    hostname=hostname,
                )
                record_heartbeat(session, info)
                session.commit()
            logger.debug("heartbeat recorded for worker %s", worker_id)
        except SQLAlchemyError as exc:
            # PostgreSQL may be briefly unavailable; keep looping and retry on
            # the next tick instead of crashing the worker (SYSTEM_SPEC §27.9).
            # Only the exception class is logged so connection details that
            # could contain credentials never reach the log output.
            logger.warning("heartbeat update failed (%s), retrying next tick", type(exc).__name__)
        stop.wait(interval_seconds)


def _load_devices(session_factory: sessionmaker, secrets_file: Path) -> list[DevicePollContext]:
    """Reload enabled devices + credentials for one cycle.

    Reloaded every cycle so inventory syncs and credential changes apply
    without a worker restart. Secrets trouble (missing file, 0600 violations)
    raises here and is contained per cycle by the scheduler.
    """

    secrets = load_secrets(secrets_file)
    with session_factory() as session:
        return build_contexts(session, secrets)


def main() -> None:
    settings = load_settings()
    setup_logging(settings)

    stop = threading.Event()

    def _request_stop(signum: int, _frame: object) -> None:
        logger.info("received signal %s, stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    started_at = datetime.now(UTC)
    hostname = socket.gethostname()
    session_factory = get_session_factory()
    logger.info("worker %s started (version %s)", settings.worker_id, __version__)

    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(
            stop,
            session_factory,
            settings.worker_id,
            started_at,
            hostname,
            settings.heartbeat_interval_seconds,
        ),
        name="heartbeat",
        daemon=True,
    )
    scheduler = DevicePollScheduler(
        load_devices=lambda: _load_devices(session_factory, settings.secrets_file)
    )
    poller = threading.Thread(
        target=scheduler.run_loop, args=(stop,), name="device-poll", daemon=True
    )

    def _load_irf_devices() -> list[IrfDeviceContext]:
        secrets = load_secrets(settings.secrets_file)
        with session_factory() as session:
            return build_irf_contexts(session, secrets)

    irf_loop = IrfObservationLoop(load_devices=_load_irf_devices)
    irf_observer = threading.Thread(
        target=irf_loop.run_loop, args=(stop,), name="irf-observation", daemon=True
    )
    retention = threading.Thread(
        target=_retention_loop,
        args=(stop, session_factory, settings.retention_days),
        name="retention",
        daemon=True,
    )

    heartbeat.start()
    poller.start()
    irf_observer.start()
    retention.start()
    stop.wait()
    # Give the loops a moment to notice the stop event before exit.
    heartbeat.join(timeout=5)
    poller.join(timeout=5)
    irf_observer.join(timeout=5)
    retention.join(timeout=5)
    logger.info("worker %s stopped", settings.worker_id)


if __name__ == "__main__":
    main()
