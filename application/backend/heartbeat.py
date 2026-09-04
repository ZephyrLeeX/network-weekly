"""Persistent worker heartbeat (SYSTEM_SPEC.md §25: worker responsibilities).

The heartbeat lives in PostgreSQL so a worker's liveness can be judged
independently of the web health endpoint. Web healthy does not imply worker
healthy, and vice versa.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import Insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.models import WorkerHeartbeat


@dataclass(frozen=True)
class HeartbeatInfo:
    """Values persisted on every heartbeat tick."""

    worker_id: str
    last_heartbeat: datetime
    started_at: datetime
    version: str
    hostname: str | None = None


def build_heartbeat_info(
    worker_id: str,
    version: str,
    started_at: datetime,
    *,
    now: datetime | None = None,
    hostname: str | None = None,
) -> HeartbeatInfo:
    """Build the heartbeat values; `now` is injectable for deterministic tests.

    Timestamps are always timezone-aware UTC (stored as TIMESTAMPTZ).
    """

    return HeartbeatInfo(
        worker_id=worker_id,
        last_heartbeat=now if now is not None else datetime.now(UTC),
        started_at=started_at,
        version=version,
        hostname=hostname,
    )


def build_upsert_statement(info: HeartbeatInfo) -> Insert:
    """Upsert on worker_id so restarts update one stable row per worker."""

    return pg_insert(WorkerHeartbeat).values(
        worker_id=info.worker_id,
        last_heartbeat=info.last_heartbeat,
        started_at=info.started_at,
        version=info.version,
        hostname=info.hostname,
    ).on_conflict_do_update(
        index_elements=[WorkerHeartbeat.worker_id],
        set_={
            "last_heartbeat": info.last_heartbeat,
            "started_at": info.started_at,
            "version": info.version,
            "hostname": info.hostname,
        },
    )


def record_heartbeat(session: Connection | Session, info: HeartbeatInfo) -> None:
    """Persist one heartbeat tick. The caller commits."""

    session.execute(build_upsert_statement(info))
