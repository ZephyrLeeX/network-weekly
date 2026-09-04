"""Worker process entrypoint, run as `python -m backend.worker`.

Wave 0 scope: a process that starts, stays alive and terminates cleanly on
SIGTERM/SIGINT. W00-T005 adds the persistent worker heartbeat loop here;
DEVICE_POLL, IRF observation, retention, weekly statistics and DOCX
generation belong to later waves (SYSTEM_SPEC.md §25).
"""

import logging
import signal
import threading

from backend import __version__

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    stop = threading.Event()

    def _request_stop(signum: int, _frame: object) -> None:
        logger.info("received signal %s, stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    logger.info("worker started (version %s)", __version__)
    stop.wait()
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
