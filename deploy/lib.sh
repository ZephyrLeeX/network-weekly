#!/usr/bin/env bash
# Shared helpers for deploy/install.sh and deploy/update.sh (W05-T002/T003).
# Sourced, never executed directly. Nothing here talks to the Internet.

# Explicit exit codes (documented in docs/OPERATIONS.md):
#   10  environment/host problem (root, OS, arch)
#   11  Docker Engine or Compose plugin missing/broken
#   12  required image missing locally (offline install: load it first)
#   13  configuration or secrets file problem (permissions, missing source)
#   14  database migration failure
#   15  administrator initialization failure
#   16  inventory sync failure
#   17  service start failure
#   18  web health verification failure
#   19  worker heartbeat verification failure

# Host-side path roots; override only in test/staging environments.
INSTALL_ROOT=${NETWORK_REPORT_INSTALL_ROOT:-/opt}
DATA_ROOT=${NETWORK_REPORT_DATA_ROOT:-/data}
CONFIG_ROOT=${NETWORK_REPORT_CONFIG_ROOT:-/etc}

APP_DIR="$INSTALL_ROOT/network-report"
DATA_DIR="$DATA_ROOT/network-report"
CONFIG_DIR="$CONFIG_ROOT/network-report"

COMPOSE() {
    docker compose -p network-report \
        --project-directory "$APP_DIR" \
        --env-file "$APP_DIR/.env" \
        -f "$APP_DIR/docker-compose.yml" "$@"
}

die() {
    local code=$1
    shift
    printf 'FATAL (exit %s): %s\n' "$code" "$*" >&2
    exit "$code"
}

step() {
    printf '\n== %s ==\n' "$*"
}

# Require Docker Engine + Compose plugin, both usable by the calling user.
check_docker() {
    docker info >/dev/null 2>&1 \
        || die 11 "Docker Engine is not usable by this user (installed? group membership? 'systemctl status docker')"
    docker compose version >/dev/null 2>&1 \
        || die 11 "Docker Compose plugin is missing (package docker-compose-plugin on Debian 13)"
}

# Require the images to exist locally; a production install never pulls.
# Precedence mirrors compose: an exported NETWORK_REPORT_APP_IMAGE wins over
# the installed .env, so the check covers the image that will actually run.
check_images() {
    local image
    local app_image
    app_image=${NETWORK_REPORT_APP_IMAGE:-$(grep -E '^NETWORK_REPORT_APP_IMAGE=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2-)}
    app_image=${app_image:-network-weekly-app:0.1.0}
    for image in "$app_image" postgres:17-alpine; do
        docker image inspect "$image" >/dev/null 2>&1 \
            || die 12 "image $image not present locally (offline install: 'docker load -i <archive>' first)"
    done
}

# GET /health until it reports application AND database ok; returns 1 on
# timeout (callers decide the exit code).
wait_health() {
    local attempt
    for attempt in $(seq 1 24); do
        if COMPOSE exec -T web python - <<'PY' 2>/dev/null
import json, sys, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as r:
        data = json.load(r)
    ok = r.status == 200 and data.get("status") == "ok" and data.get("database") == "ok"
except Exception:
    ok = False
print("health ok" if ok else "health not ready")
sys.exit(0 if ok else 1)
PY
        then
            return 0
        fi
        sleep 5
    done
    return 1
}

# Wait for a NEW heartbeat written by THIS worker (NETWORK_REPORT_WORKER_ID)
# after the baseline captured by heartbeat_baseline (W05-AUDIT fix 1, §25);
# returns 1 on timeout (callers decide the exit code).
#
# The pre-W05-AUDIT check ("any heartbeat row is fresh enough") passed on a
# stale row left behind by the previous worker — a false positive. The
# tested implementation lives in backend.ops.heartbeat_verify; the inline
# fallback below applies the SAME rules when the target image predates that
# module (a rollback target), keeping `update.sh --image <old>` usable.
# Keep the fallback in sync with backend/ops/heartbeat_verify.py.
wait_heartbeat() {
    local baseline=$1
    local attempt
    if COMPOSE exec -T worker python -c "import backend.ops.heartbeat_verify" 2>/dev/null; then
        for attempt in $(seq 1 24); do
            if COMPOSE exec -T worker python -m backend.ops.heartbeat_verify verify "$baseline" 2>/dev/null; then
                return 0
            fi
            sleep 5
        done
        return 1
    fi
    for attempt in $(seq 1 24); do
        if COMPOSE exec -T worker python - verify "$baseline" <<'PY' 2>/dev/null
import json, sys
from datetime import UTC, datetime

from sqlalchemy import select

from backend.config import load_settings
from backend.db.engine import get_engine
from backend.db.models import WorkerHeartbeat

baseline = json.loads(sys.argv[2])
worker_id = load_settings().worker_id
with get_engine().connect() as conn:
    row = conn.execute(
        select(WorkerHeartbeat.last_heartbeat, WorkerHeartbeat.started_at)
        .where(WorkerHeartbeat.worker_id == worker_id)
    ).first()
limit = load_settings().heartbeat_interval_seconds * 4
if row is None:
    print(f"no heartbeat row for worker {worker_id!r} yet (limit {limit}s)")
    raise SystemExit(1)
age = (datetime.now(UTC) - row[0]).total_seconds()
if baseline and row[0] <= datetime.fromisoformat(baseline["last_heartbeat"]):
    print(f"worker {worker_id!r} wrote no NEW heartbeat after the baseline")
    raise SystemExit(1)
if age >= limit:
    print(f"heartbeat for worker {worker_id!r} is stale: age {age:.0f}s >= limit {limit}s")
    raise SystemExit(1)
print(f"new heartbeat for worker {worker_id!r} written after the baseline (age {age:.0f}s)")
raise SystemExit(0)
PY
        then
            return 0
        fi
        sleep 5
    done
    return 1
}

# Print the pre-start heartbeat baseline for NETWORK_REPORT_WORKER_ID as a
# JSON object ({} when this worker has no row yet). Must run BEFORE the
# stack is (re)started; as a one-off container it works on a fresh install
# (no worker container yet) and always runs on the TARGET image (update.sh
# has already switched the image reference when it calls this).
heartbeat_baseline() {
    if COMPOSE run --rm --no-deps worker python -m backend.ops.heartbeat_verify baseline 2>/dev/null; then
        return 0
    fi
    # Legacy fallback for pre-W05-AUDIT target images (rollback target):
    # same query, same JSON. Keep in sync with backend/ops/heartbeat_verify.py.
    COMPOSE run --rm --no-deps worker python - baseline <<'PY' 2>/dev/null
import json, sys

from sqlalchemy import select

from backend.config import load_settings
from backend.db.engine import get_engine
from backend.db.models import WorkerHeartbeat

worker_id = load_settings().worker_id
with get_engine().connect() as conn:
    row = conn.execute(
        select(WorkerHeartbeat.last_heartbeat, WorkerHeartbeat.started_at)
        .where(WorkerHeartbeat.worker_id == worker_id)
    ).first()
if row is None:
    print("{}")
else:
    print(json.dumps({
        "last_heartbeat": row[0].isoformat(),
        "started_at": row[1].isoformat(),
    }))
PY
}

# install.sh verifiers: fail the script with the documented exit codes.
verify_health() {
    wait_health || die 18 "web /health did not report ok (application+database) within 2 minutes; 'docker compose -p network-report logs web' shows why"
}

verify_heartbeat() {
    local baseline=${1:-}
    [[ -n $baseline ]] || baseline='{}'
    wait_heartbeat "$baseline" \
        || die 19 "worker wrote no NEW heartbeat after this start; 'docker compose -p network-report logs worker' shows why"
}

# Failure AFTER the migration committed (W05-AUDIT fix 3): the schema may
# already have moved forward, so the previous unconditional "roll back the
# image" hint was unsafe — rolling back may require a DB restore first.
# Prints the schema-aware remediation and exits $1.
post_migration_failure() {
    local code=$1
    shift
    {
        printf 'FATAL (exit %s): %s\n\n' "$code" "$*"
        cat <<'EOF'
Database migration has already committed.
The schema may no longer be compatible with the previous image.
Do NOT blindly roll back the application image.

See docs/OPERATIONS.md rollback procedure.
If the previous image cannot run against the migrated schema,
restore the pre-update database backup first, then start the old image.
EOF
    } >&2
    exit "$code"
}

# True (exit 0) when the single administrator account already exists.
# Uses a one-off `run` container: the web service is not up yet at this
# point of the install flow.
admin_exists() {
    local answer
    answer=$(COMPOSE run --rm --no-deps web python - <<'PY' 2>/dev/null
from sqlalchemy import select

from backend.db.engine import get_engine
from backend.db.models import User

with get_engine().connect() as conn:
    row = conn.execute(select(User.id).limit(1)).first()
print("yes" if row is not None else "no")
PY
) || die 15 "cannot query the administrator account (is the database migrated?)"
    [[ $answer == yes ]]
}
