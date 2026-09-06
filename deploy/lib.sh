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

# GET /health until it reports application AND database ok.
verify_health() {
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
    die 18 "web /health did not report ok (application+database) within 2 minutes; 'docker compose -p network-report logs web' shows why"
}

# Wait for a fresh worker heartbeat row (independent of web health, §25).
verify_heartbeat() {
    local attempt
    for attempt in $(seq 1 24); do
        if COMPOSE exec -T worker python - <<'PY' 2>/dev/null
from datetime import UTC, datetime

from sqlalchemy import select

from backend.config import load_settings
from backend.db.engine import get_engine
from backend.db.models import WorkerHeartbeat

limit = load_settings().heartbeat_interval_seconds * 4
with get_engine().connect() as conn:
    row = conn.execute(select(WorkerHeartbeat.last_heartbeat).limit(1)).first()
if row is None:
    print("no heartbeat row yet")
    raise SystemExit(1)
age = (datetime.now(UTC) - row[0]).total_seconds()
print(f"worker heartbeat age {age:.0f}s (limit {limit}s)")
raise SystemExit(0 if age <= limit else 1)
PY
        then
            return 0
        fi
        sleep 5
    done
    die 19 "worker heartbeat missing or stale; 'docker compose -p network-report logs worker' shows why"
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
