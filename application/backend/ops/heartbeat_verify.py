"""Deploy-time worker heartbeat verification (W05-AUDIT fix 1).

Used by `deploy/lib.sh` (install.sh / update.sh) via
`python -m backend.ops.heartbeat_verify`. The verifier answers one question:
did THIS worker (NETWORK_REPORT_WORKER_ID) write a heartbeat AFTER the
start/update being validated?

The pre-W05-AUDIT check ("any heartbeat row is fresh enough") was a false
positive: a row left behind by the previous worker — even seconds old —
passed while the new worker had never started. The state machine below
therefore compares against a BASELINE captured immediately before the
stack is (re)started:

1. the row for the current worker id must exist (a fresh heartbeat written
   by a DIFFERENT worker id is invisible to this query and never passes);
2. with a baseline: `last_heartbeat` must be strictly NEWER than the
   baseline's (a same-image re-run may also see the row move forward);
3. without a baseline (fresh install / first heartbeat of this worker id):
   the row appearing at all is the new evidence;
4. the heartbeat age must still be below `interval * 4` (§25 liveness).

`started_at` is evaluated as supplementary evidence only: the reason string
reports whether the worker process actually restarted. It is deliberately
NOT a pass condition — `update.sh` on an unchanged stack re-verifies in
place without recreating the worker, and that legitimate case must pass.

Subcommands (baseline JSON holds only timestamps — never secret material):

  baseline            print the current worker's heartbeat as a JSON object
                      ({} when it has no row yet); run BEFORE the start
  verify <baseline>   exit 0 iff rules 1-4 hold; a reason is always printed

`deploy/lib.sh` carries an equivalent inline fallback for the same rules so
`update.sh --image <previous>` keeps working when the TARGET image predates
this module (a rollback target): keep the two in sync.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection, select

from backend.config import load_settings
from backend.db.engine import get_engine
from backend.db.models import WorkerHeartbeat

# §25 liveness bound: a passing heartbeat must be fresher than this many
# heartbeat intervals (deploy scripts wait up to 2 minutes for the worker).
AGE_LIMIT_INTERVALS = 4

USAGE = "usage: python -m backend.ops.heartbeat_verify baseline|verify <baseline-json>"


@dataclass(frozen=True)
class HeartbeatSample:
    """The two `worker_heartbeat` columns the verifier needs."""

    last_heartbeat: datetime
    started_at: datetime


def dump_baseline(sample: HeartbeatSample | None) -> str:
    """Serialize a baseline (or `{}` for "no row yet") for the shell to hold."""

    if sample is None:
        return "{}"
    return json.dumps(
        {
            "last_heartbeat": sample.last_heartbeat.isoformat(),
            "started_at": sample.started_at.isoformat(),
        }
    )


def parse_baseline(raw: str) -> HeartbeatSample | None:
    """Parse `dump_baseline` output; `{}` means the worker had no row."""

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("heartbeat baseline JSON must be an object")
    if not data:
        return None
    return HeartbeatSample(
        last_heartbeat=datetime.fromisoformat(str(data["last_heartbeat"])),
        started_at=datetime.fromisoformat(str(data["started_at"])),
    )


def fetch_sample(conn: Connection, worker_id: str) -> HeartbeatSample | None:
    """Read THIS worker's row only — never any other worker's."""

    row = conn.execute(
        select(WorkerHeartbeat.last_heartbeat, WorkerHeartbeat.started_at).where(
            WorkerHeartbeat.worker_id == worker_id
        )
    ).first()
    return None if row is None else HeartbeatSample(row[0], row[1])


def verify_heartbeat(
    sample: HeartbeatSample | None,
    baseline: HeartbeatSample | None,
    *,
    now: datetime,
    interval_seconds: int,
    worker_id: str,
) -> tuple[bool, str]:
    """Decide whether the current worker produced NEW post-start evidence.

    Returns (ok, reason); the reason is written to stdout by the CLI either
    way so a failed install/update prints what was actually observed.
    """

    limit = interval_seconds * AGE_LIMIT_INTERVALS

    if sample is None:
        return False, f"no heartbeat row for worker {worker_id!r} yet (limit {limit}s)"
    age = (now - sample.last_heartbeat).total_seconds()

    if baseline is None:
        if age >= limit:
            return False, (
                f"first heartbeat for worker {worker_id!r} is already stale: "
                f"age {age:.0f}s >= limit {limit}s"
            )
        return True, (
            f"first heartbeat for worker {worker_id!r} observed "
            f"(age {age:.0f}s < limit {limit}s)"
        )

    if sample.last_heartbeat <= baseline.last_heartbeat:
        return False, (
            f"worker {worker_id!r} wrote no NEW heartbeat after the baseline: row "
            f"{sample.last_heartbeat.isoformat()} <= baseline "
            f"{baseline.last_heartbeat.isoformat()} — a stale row from a "
            "previous worker must not pass"
        )
    if age >= limit:
        return False, (
            f"new heartbeat for worker {worker_id!r} is already stale: "
            f"age {age:.0f}s >= limit {limit}s"
        )

    restarted = sample.started_at > baseline.started_at
    return True, (
        f"new heartbeat for worker {worker_id!r} written after the baseline "
        f"(age {age:.0f}s < limit {limit}s, "
        f"worker {'restarted' if restarted else 'kept running'})"
    )


def main(argv: list[str]) -> int:
    """CLI entry point (`python -m backend.ops.heartbeat_verify ...`)."""

    if len(argv) < 2 or argv[1] not in ("baseline", "verify") or (
        argv[1] == "verify" and len(argv) != 3
    ):
        print(USAGE, file=sys.stderr)
        return 2

    settings = load_settings()
    if argv[1] == "baseline":
        with get_engine().connect() as conn:
            sample = fetch_sample(conn, settings.worker_id)
        print(dump_baseline(sample))
        return 0

    baseline = parse_baseline(argv[2])
    with get_engine().connect() as conn:
        sample = fetch_sample(conn, settings.worker_id)
    ok, reason = verify_heartbeat(
        sample,
        baseline,
        now=datetime.now(UTC),
        interval_seconds=settings.heartbeat_interval_seconds,
        worker_id=settings.worker_id,
    )
    print(reason)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
