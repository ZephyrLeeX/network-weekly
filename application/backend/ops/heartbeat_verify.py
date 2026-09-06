"""Deploy-time worker heartbeat verification (W05-AUDIT fix 1, W05-AUDIT-2).

Used by `deploy/lib.sh` (install.sh / update.sh) via
`python -m backend.ops.heartbeat_verify`. The verifier answers one question:
did the worker that is RUNNING NOW write a heartbeat AFTER the start/update
being validated?

The pre-W05-AUDIT check ("any heartbeat row is fresh enough") was a false
positive: a row left behind by the previous worker — even seconds old —
passed while the new worker had never started. The W05-AUDIT state machine
therefore compares against a BASELINE captured immediately before the stack
is (re)started.

W05-AUDIT-2 closes the race that remained in that design: the OLD worker
keeps running for a moment after the baseline is captured, so its very last
tick (`last_heartbeat` newer than the baseline, seconds fresh) can land
after the baseline but BEFORE compose stops it — while the replacement
worker never gets its heartbeat loop running. A heartbeat newer than the
baseline alone therefore proves nothing when the worker CONTAINER was
replaced. The verifier also takes the worker's Docker container identity
before and after the start (`worker_container_id` in deploy/lib.sh: ""
when there is no running worker container, otherwise the full `docker
inspect` ID — never a short ID or an image tag) and distinguishes:

* container NOT replaced (a true same-container re-verify, e.g. an update
  that did not recreate the worker): the heartbeat itself must move past
  the baseline; `started_at` may legitimately stay put;
* container REPLACED (fresh install none→new, or a recreated worker): the
  row must ALSO carry `started_at` newer than the baseline's — a heartbeat
  newer than the baseline with the OLD `started_at` is exactly the old
  worker's post-baseline final tick and must FAIL.

Rules (a reason is always printed):

1. the row for the current worker id must exist (a fresh heartbeat written
   by a DIFFERENT worker id is invisible to this query and never passes);
2. with a baseline: `last_heartbeat` must be strictly NEWER than the
   baseline's;
3. without a baseline (fresh install / first heartbeat of this worker id):
   the row appearing at all is the new evidence;
4. the heartbeat age must still be below `interval * 4` (§25 liveness);
5. ONLY when the container was replaced: `started_at` must also be newer
   than the baseline's (rules 2+4+5 together = the replacement worker's own
   heartbeat loop ran). `started_at` is deliberately NOT a condition when
   the container was not replaced — that would break the legitimate
   same-container re-verify.

Subcommands (baseline JSON holds only timestamps — never secret material):

  baseline                                print the current worker's heartbeat
                                          as a JSON object ({} when it has no
                                          row yet); run BEFORE the start
  verify <baseline> <pre-id> <post-id>    exit 0 iff rules 1-5 hold; the
                                          replacement decision is computed
                                          from the two container identities

`deploy/lib.sh` carries an equivalent inline fallback for the same rules so
rollback targets whose image predates the W05-AUDIT-2 verifier signature
keep working (`update.sh --image <old>`): keep the two in sync.
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

USAGE = (
    "usage: python -m backend.ops.heartbeat_verify baseline\n"
    "       python -m backend.ops.heartbeat_verify verify "
    "<baseline-json> <pre-container-id> <post-container-id>"
)


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


def is_container_replaced(pre_id: str, post_id: str) -> bool:
    """Whether the worker CONTAINER changed across the start/update.

    Compares the full Docker container identities read by
    `worker_container_id` (deploy/lib.sh) — never image tags: a same-image
    update may still recreate the worker, and an image switch may in
    principle keep the container. "" means "no running worker container":
    none→new is a replacement (fresh install), and none→none (the deploy
    scripts refuse that case outright) would compare equal.
    """

    return pre_id != post_id


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
    container_replaced: bool,
) -> tuple[bool, str]:
    """Decide whether the CURRENT worker produced NEW post-start evidence.

    Returns (ok, reason); the reason is written to stdout by the CLI either
    way so a failed install/update prints what was actually observed. The
    reasons distinguish: a new heartbeat from the replacement worker, a new
    heartbeat from the existing (same-container) worker, the old worker's
    post-baseline final tick while the replacement worker is still silent,
    a stale heartbeat, and a missing worker row.
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

    if container_replaced and sample.started_at <= baseline.started_at:
        return False, (
            f"old worker wrote after the baseline but the replacement worker "
            f"has not: worker {worker_id!r} last_heartbeat "
            f"{sample.last_heartbeat.isoformat()} > baseline but started_at "
            f"{sample.started_at.isoformat()} <= baseline "
            f"{baseline.started_at.isoformat()} while the worker container was "
            "replaced — that is the old container's final tick, not evidence "
            "that the replacement worker's heartbeat loop runs"
        )

    if container_replaced:
        return True, (
            f"new heartbeat for worker {worker_id!r} written after the baseline "
            f"by the replacement worker (age {age:.0f}s < limit {limit}s, "
            f"started_at advanced past {baseline.started_at.isoformat()})"
        )
    return True, (
        f"new heartbeat for worker {worker_id!r} written after the baseline "
        f"(age {age:.0f}s < limit {limit}s, worker kept running)"
    )


def main(argv: list[str]) -> int:
    """CLI entry point (`python -m backend.ops.heartbeat_verify ...`)."""

    if len(argv) < 2 or argv[1] not in ("baseline", "verify"):
        print(USAGE, file=sys.stderr)
        return 2
    if argv[1] == "baseline" and len(argv) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    if argv[1] == "verify" and len(argv) != 5:
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
        container_replaced=is_container_replaced(argv[3], argv[4]),
    )
    print(reason)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
