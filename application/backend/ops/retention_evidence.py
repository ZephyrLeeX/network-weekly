"""Oldest-raw-data evidence for the 90-day retention acceptance (§24, A13).

Used by `scripts/acceptance_evidence.sh` via
`python -m backend.ops.retention_evidence`. Read-only: reports the age of
the OLDEST row (`MIN` timestamp) in each of the three raw tables.

The pre-W05-AUDIT collector computed `MAX` here and labelled it "oldest":
with any recent row present (which is the normal, healthy case) it always
reported OK and could never detect retained-beyond-90d data. The judgment
must come from the oldest surviving row — that is what the §24 retention
loop is allowed to delete — while long-term tables are untouched.

This module only READS the tables. The retention implementation itself lives
in `backend.monitoring.retention` (W05-T004) and is deliberately unchanged.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import Connection, func, select
from sqlalchemy.orm import InstrumentedAttribute

from backend.db.engine import get_engine
from backend.db.models import DeviceMetric, DevicePollRun, InterfaceMetric

#: §24: raw data is kept at least 90 days; the collector adds one day of
#: slack for the worker's 6-hour retention pass cadence (mirrors A13).
BEYOND_THRESHOLD_DAYS: Final[float] = 91.0

RawTable = type[DeviceMetric] | type[InterfaceMetric] | type[DevicePollRun]


def _raw_tables() -> tuple[tuple[str, RawTable, InstrumentedAttribute[datetime]], ...]:
    """(label, table, oldest-row timestamp column) for the three raw tables."""

    return (
        ("device_metrics", DeviceMetric, DeviceMetric.collected_at),
        ("interface_metrics", InterfaceMetric, InterfaceMetric.collected_at),
        ("device_poll_runs", DevicePollRun, DevicePollRun.cycle_started_at),
    )


def oldest_rows(conn: Connection) -> dict[str, datetime | None]:
    """The true oldest timestamp per raw table (None when the table is empty)."""

    return {
        label: conn.execute(select(func.min(column))).scalar_one()
        for label, _table, column in _raw_tables()
    }


def classify_age_days(age_days: float) -> str:
    """A13 verdict for one table's oldest-row age."""

    return "OK" if age_days <= BEYOND_THRESHOLD_DAYS else "BEYOND-90d"


def format_retention_line(label: str, oldest: datetime | None, now: datetime) -> str:
    """One evidence line: the oldest-row age and its A13 verdict."""

    if oldest is None:
        return f"{label}: empty"
    age_days = (now - oldest).total_seconds() / 86400
    return (
        f"{label}: oldest row {oldest.isoformat()} "
        f"(age {age_days:.1f} days) {classify_age_days(age_days)}"
    )


def render_retention_evidence(conn: Connection, now: datetime) -> str:
    """The full three-table evidence block, newline-terminated."""

    return "\n".join(
        format_retention_line(label, oldest, now)
        for label, oldest in oldest_rows(conn).items()
    ) + "\n"


def main(argv: list[str]) -> int:
    """CLI entry point (`python -m backend.ops.retention_evidence`)."""

    if len(argv) != 1:
        print("usage: python -m backend.ops.retention_evidence", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    with get_engine().connect() as conn:
        print(render_retention_evidence(conn, now), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
