"""Wave 0 ORM models.

Only the foundation-required `worker_heartbeat` table (SYSTEM_SPEC.md §23).
Device, interface and metric tables arrive with the Wave 1/2 migrations.
All business timestamps use TIMESTAMPTZ (SYSTEM_SPEC.md §3/§23).
"""

from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class WorkerHeartbeat(Base):
    """Liveness record for one worker identity, upserted on every heartbeat.

    `last_heartbeat` proves the worker loop is running; `started_at` lets
    restarts be distinguished from a continuously running worker. Web health
    and worker heartbeat are deliberately independent checks.
    """

    __tablename__ = "worker_heartbeat"

    worker_id: Mapped[str] = mapped_column(Text, primary_key=True)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[str | None] = mapped_column(Text, nullable=True)
    hostname: Mapped[str | None] = mapped_column(Text, nullable=True)
