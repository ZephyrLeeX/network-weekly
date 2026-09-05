"""90-day raw-data retention maintenance (W02-T009, SYSTEM_SPEC.md §24).

Deletes, in small separate transactions:

1. `interface_metrics` older than the cutoff (`collected_at`);
2. `device_metrics` older than the cutoff (`collected_at`);
3. `device_poll_runs` older than the cutoff (`cycle_started_at`).

Batching (small `DELETE ... WHERE id IN (SELECT ... LIMIT n)` statements,
each its own transaction) keeps transactions short and never holds a long
lock (§24). Children are deleted before their poll runs so a batch delete of
runs does not trigger a large cascade.

Deliberately NOT touched: device/interface incident records, IRF member
observations, weekly report metadata and DOCX files — all long-term (§24).
Retention only ever references the three raw tables above, so long-term
data is unreachable to it by construction.

Configured retention is validated at load time (§22-style explicit config):
`NETWORK_REPORT_RETENTION_DAYS` must be >= 90 (§24 "at least 90 days").
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import InstrumentedAttribute, Session, sessionmaker

from backend.db.models import DeviceMetric, DevicePollRun, InterfaceMetric

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 90
# §24: raw data is kept at least 90 days; smaller configured values are
# rejected at startup, not silently applied.
MIN_RETENTION_DAYS = 90
DEFAULT_BATCH_SIZE = 5000


@dataclass(frozen=True)
class RetentionReport:
    """Deleted-row counts of one retention pass (safe to log)."""

    cutoff: datetime
    interface_metrics_deleted: int
    device_metrics_deleted: int
    poll_runs_deleted: int

    @property
    def total_deleted(self) -> int:
        return (
            self.interface_metrics_deleted
            + self.device_metrics_deleted
            + self.poll_runs_deleted
        )


def _delete_batch(
    session: Session,
    table: type[InterfaceMetric] | type[DeviceMetric] | type[DevicePollRun],
    cutoff_column: InstrumentedAttribute[datetime],
    cutoff: datetime,
    batch_size: int,
) -> int:
    """Delete one batch of expired rows; returns the deleted count."""

    batch_ids = session.execute(
        select(table.id).where(cutoff_column < cutoff).limit(batch_size)
    ).scalars().all()
    if not batch_ids:
        return 0
    session.execute(delete(table).where(table.id.in_(batch_ids)))
    # The DELETE targets exactly the selected ids.
    return len(batch_ids)


def run_retention(
    session_factory: sessionmaker,
    *,
    now: datetime,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches_per_table: int | None = None,
) -> RetentionReport:
    """Delete expired raw metrics/poll runs in batches; returns the counts.

    `max_batches_per_table` bounds the pass (tests, or rate-limiting a first
    run over a large backlog); None means delete everything expired.
    """

    if retention_days < MIN_RETENTION_DAYS:
        raise ValueError(
            f"retention_days must be >= {MIN_RETENTION_DAYS} (SYSTEM_SPEC.md §24); "
            f"got {retention_days}"
        )
    if now.tzinfo is None:
        raise ValueError("run_retention requires a timezone-aware `now`")

    cutoff = now - timedelta(days=retention_days)
    interface_deleted = 0
    device_deleted = 0
    runs_deleted = 0

    for table, column, counter in (
        (InterfaceMetric, InterfaceMetric.collected_at, "interface"),
        (DeviceMetric, DeviceMetric.collected_at, "device"),
        (DevicePollRun, DevicePollRun.cycle_started_at, "runs"),
    ):
        deleted = 0
        batches = 0
        while True:
            with session_factory() as session:
                count = _delete_batch(session, table, column, cutoff, batch_size)
                session.commit()
            if count == 0:
                break
            deleted += count
            batches += 1
            if max_batches_per_table is not None and batches >= max_batches_per_table:
                break
        if counter == "interface":
            interface_deleted = deleted
        elif counter == "device":
            device_deleted = deleted
        else:
            runs_deleted = deleted

    report = RetentionReport(
        cutoff=cutoff,
        interface_metrics_deleted=interface_deleted,
        device_metrics_deleted=device_deleted,
        poll_runs_deleted=runs_deleted,
    )
    logger.info(
        "retention pass: cutoff %s, deleted %d interface metrics, %d device metrics, "
        "%d poll runs",
        report.cutoff,
        report.interface_metrics_deleted,
        report.device_metrics_deleted,
        report.poll_runs_deleted,
    )
    return report
