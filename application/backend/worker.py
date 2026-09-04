"""Worker process entrypoint, run as `python -m backend.worker`.

Wave 0 scope: process lifecycle plus the persistent heartbeat loop
(SYSTEM_SPEC.md §25). DEVICE_POLL, IRF observation, metric processing,
incident updates, retention, weekly statistics, DOCX generation and report
retry belong to later waves.
"""

import logging
import signal
import socket
import threading
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from backend import __version__
from backend.config import load_settings
from backend.db.engine import get_session_factory
from backend.heartbeat import build_heartbeat_info, record_heartbeat
from backend.log import setup_logging

logger = logging.getLogger(__name__)


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

    while not stop.is_set():
        try:
            with session_factory() as session:
                info = build_heartbeat_info(
                    worker_id=settings.worker_id,
                    version=__version__,
                    started_at=started_at,
                    hostname=hostname,
                )
                record_heartbeat(session, info)
                session.commit()
            logger.debug("heartbeat recorded for worker %s", settings.worker_id)
        except SQLAlchemyError as exc:
            # PostgreSQL may be briefly unavailable; keep looping and retry on
            # the next tick instead of crashing the worker (SYSTEM_SPEC §27.9).
            # Only the exception class is logged so connection details that
            # could contain credentials never reach the log output.
            logger.warning("heartbeat update failed (%s), retrying next tick", type(exc).__name__)
        stop.wait(settings.heartbeat_interval_seconds)

    logger.info("worker %s stopped", settings.worker_id)


if __name__ == "__main__":
    main()
