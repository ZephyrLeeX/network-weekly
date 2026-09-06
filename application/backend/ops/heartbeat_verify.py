"""Deploy-time worker heartbeat verification (W05-AUDIT fix 1, -2, HARDENING).

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

W05-PRE-ACCEPTANCE-HARDENING closes the remaining hole in that design: a
baseline with NO heartbeat row (`{}`) used to make the very first fresh row
pass as "fresh install" — but "no row yet" is also what the database looks
like when an EXISTING worker container simply has not ticked since it
started. The baseline now records WHEN it was captured (`captured_at`),
and with no prior row the verifier branches on the PRE identity:

* fresh install (no worker container before, "" → new): the first fresh row
  is the new evidence — unchanged;
* replacement of an existing worker (pre ≠ "" and pre ≠ post): the row must
  ALSO carry `started_at` AFTER the baseline capture — the old container's
  first-ever tick after the baseline (started_at before the capture) must
  FAIL. A LEGACY baseline without `captured_at` cannot prove which
  container wrote the row and fails closed (check the worker logs);
* same container: the row can only come from the container that is still
  running — first fresh row passes (started_at may stay put).

Rules (a reason is always printed):

1. the row for the current worker id must exist (a fresh heartbeat written
   by a DIFFERENT worker id is invisible to this query and never passes);
2. with a baseline row: `last_heartbeat` must be strictly NEWER than the
   baseline's;
3. without a baseline row: the row appearing at all is new evidence — but
   when an existing worker's container was replaced, `started_at` must be
   newer than the baseline's `captured_at` (legacy baselines without
   `captured_at` fail closed here);
4. the heartbeat age must still be below `interval * 4` (§25 liveness);
5. when the container was replaced WITH a baseline row: `started_at` must
   also be newer than the baseline's (rules 2+4+5 together = the
   replacement worker's own heartbeat loop ran). `started_at` is
   deliberately NOT a condition when the container was not replaced — that
   would break the legitimate same-container re-verify.

Baseline JSON (timestamps only — never secret material); `parse_baseline`
accepts every historical format so a rollback target reading a pre-hardening
baseline never crashes:

  {}                                      legacy: no row, capture time unknown
  {"last_heartbeat", "started_at"}        legacy: row, capture time unknown
  {"captured_at"}                         current: no row at capture time
  {"captured_at", "last_heartbeat",
   "started_at"}                          current: row at capture time

Subcommands:

  baseline                                print the current worker's heartbeat
                                          as a JSON object (with `captured_at`
                                          always; only that key when it has no
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


@dataclass(frozen=True)
class HeartbeatBaseline:
    """The state captured before the start/update.

    `sample` is the worker's row at capture time (`None` = no row yet);
    `captured_at` is the capture instant (`None` in the two legacy formats,
    which predate W05-PRE-ACCEPTANCE-HARDENING).
    """

    captured_at: datetime | None
    sample: HeartbeatSample | None


def dump_baseline(sample: HeartbeatSample | None, *, captured_at: datetime) -> str:
    """Serialize a baseline for the shell to hold; `captured_at` is always
    recorded so a no-row baseline can still bound a replacement worker's
    `started_at` (W05-PRE-ACCEPTANCE-HARDENING)."""

    data: dict[str, str] = {"captured_at": captured_at.isoformat()}
    if sample is not None:
        data["last_heartbeat"] = sample.last_heartbeat.isoformat()
        data["started_at"] = sample.started_at.isoformat()
    return json.dumps(data)


def parse_baseline(raw: str) -> HeartbeatBaseline:
    """Parse a baseline JSON in any historical format.

    `{}` and `{last_heartbeat, started_at}` are the pre-hardening formats
    (no `captured_at` — a verifier can then no longer prove which container
    wrote a first row, and `verify_heartbeat` fails that case closed).
    """

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("heartbeat baseline JSON must be an object")
    if not data:
        return HeartbeatBaseline(captured_at=None, sample=None)

    captured_at = (
        datetime.fromisoformat(str(data["captured_at"]))
        if "captured_at" in data
        else None
    )
    if "last_heartbeat" not in data:
        if "started_at" in data:
            raise ValueError(
                "heartbeat baseline JSON must not carry started_at without last_heartbeat"
            )
        return HeartbeatBaseline(captured_at=captured_at, sample=None)
    return HeartbeatBaseline(
        captured_at=captured_at,
        sample=HeartbeatSample(
            last_heartbeat=datetime.fromisoformat(str(data["last_heartbeat"])),
            started_at=datetime.fromisoformat(str(data["started_at"])),
        ),
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
    baseline: HeartbeatBaseline,
    *,
    now: datetime,
    interval_seconds: int,
    worker_id: str,
    pre_id: str,
    post_id: str,
) -> tuple[bool, str]:
    """Decide whether the CURRENT worker produced NEW post-start evidence.

    Returns (ok, reason); the reason is written to stdout by the CLI either
    way so a failed install/update prints what was actually observed. The
    reasons distinguish: a new heartbeat from the replacement worker, the
    old worker's post-baseline tick while the replacement worker is still
    silent (with and without a baseline row), a first heartbeat that cannot
    be attributed to the replacement worker (legacy baseline), a stale
    heartbeat, and a missing worker row.
    """

    limit = interval_seconds * AGE_LIMIT_INTERVALS
    container_replaced = is_container_replaced(pre_id, post_id)

    if sample is None:
        return False, f"no heartbeat row for worker {worker_id!r} yet (limit {limit}s)"
    age = (now - sample.last_heartbeat).total_seconds()
    if age >= limit:
        return False, (
            f"heartbeat for worker {worker_id!r} is already stale: "
            f"age {age:.0f}s >= limit {limit}s"
        )

    if baseline.sample is None:
        # No row at capture time: either nothing ever ran (fresh install) or
        # an existing worker container had not ticked yet (HARDENING — the
        # race where the old container's FIRST tick lands after the capture
        # and must never pass for its replacement).
        if not pre_id:
            return True, (
                f"first heartbeat for worker {worker_id!r} on a fresh install "
                f"(no worker container before the start; "
                f"age {age:.0f}s < limit {limit}s)"
            )
        if container_replaced:
            if baseline.captured_at is None:
                return False, (
                    f"baseline for worker {worker_id!r} has no captured_at "
                    f"(legacy pre-hardening format) and held no heartbeat row: "
                    "with the worker container replaced, a first heartbeat "
                    "cannot be proven to come from the replacement worker "
                    "rather than from the pre-capture container — check "
                    "'docker compose -p network-report logs worker' and "
                    "re-run the start/update"
                )
            if sample.started_at <= baseline.captured_at:
                return False, (
                    f"old container wrote after the baseline but the "
                    f"replacement worker has not: worker {worker_id!r} "
                    f"started_at {sample.started_at.isoformat()} <= baseline "
                    f"captured_at {baseline.captured_at.isoformat()} while the "
                    "worker container was replaced and the baseline held no "
                    "heartbeat row — that row predates the capture instant and "
                    "cannot prove the replacement worker's heartbeat loop runs"
                )
            return True, (
                f"first heartbeat for worker {worker_id!r} written after the "
                f"baseline capture by the replacement worker (started_at "
                f"{sample.started_at.isoformat()} > captured_at "
                f"{baseline.captured_at.isoformat()}, age {age:.0f}s < limit "
                f"{limit}s)"
            )
        return True, (
            f"first heartbeat for worker {worker_id!r} observed "
            f"with the worker container unchanged (age {age:.0f}s < limit "
            f"{limit}s, worker kept running)"
        )

    baseline_last = baseline.sample.last_heartbeat
    if sample.last_heartbeat <= baseline_last:
        return False, (
            f"worker {worker_id!r} wrote no NEW heartbeat after the baseline: row "
            f"{sample.last_heartbeat.isoformat()} <= baseline "
            f"{baseline_last.isoformat()} — a stale row from a "
            "previous worker must not pass"
        )

    if container_replaced and sample.started_at <= baseline.sample.started_at:
        return False, (
            f"old worker wrote after the baseline but the replacement worker "
            f"has not: worker {worker_id!r} last_heartbeat "
            f"{sample.last_heartbeat.isoformat()} > baseline but started_at "
            f"{sample.started_at.isoformat()} <= baseline "
            f"{baseline.sample.started_at.isoformat()} while the worker container was "
            "replaced — that is the old container's final tick, not evidence "
            "that the replacement worker's heartbeat loop runs"
        )

    if container_replaced:
        return True, (
            f"new heartbeat for worker {worker_id!r} written after the baseline "
            f"by the replacement worker (age {age:.0f}s < limit {limit}s, "
            f"started_at advanced past {baseline.sample.started_at.isoformat()})"
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
        print(dump_baseline(sample, captured_at=datetime.now(UTC)))
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
        pre_id=argv[3],
        post_id=argv[4],
    )
    print(reason)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
