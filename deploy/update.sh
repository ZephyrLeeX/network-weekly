#!/usr/bin/env bash
# update.sh — safe upgrade of an installed stack (W05-T003, SYSTEM_SPEC.md §26.3).
#
# Flow: target image checked locally → .env switched (previous .env kept as
# .env.bak) → alembic upgrade head ON THE NEW IMAGE → web+worker recreated →
# web /health verified → worker heartbeat verified.
#
# Failure semantics:
#   - The target image must exist locally (offline; nothing is pulled).
#   - A failed migration restores the previous .env and exits 14 WITHOUT
#     restarting anything: the old containers keep serving the old image.
#   - A failed restart/health/heartbeat exits 17/18/19. The migration has
#     ALREADY COMMITTED at that point, so the printed remediation is
#     schema-aware (W05-AUDIT fix 3): blindly rolling back the image is NOT
#     automatically safe — see docs/OPERATIONS.md §5 (rollback may require
#     restoring the pre-update database backup first).
#   - Never touches /data/network-report (DB + DOCX) or /etc/network-report
#     (devices.toml + secrets.env): those are host bind mounts outside the
#     application directory this script manages.
#
# Usage: update.sh [--image TAG]   (default: NETWORK_REPORT_APP_IMAGE from
# the environment, else the image already recorded in .env)
# Exit codes: usage 2; otherwise see deploy/lib.sh (10..19).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
    echo "usage: update.sh [--image TAG]" >&2
    exit 2
}

IMAGE_ARG=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --image|-i)
            [[ $# -ge 2 ]] || usage
            IMAGE_ARG=$2
            shift 2
            ;;
        *) usage ;;
    esac
done

step "installed stack"
[[ -f $APP_DIR/docker-compose.yml && -f $APP_DIR/.env ]] \
    || die 13 "no installed stack at $APP_DIR (docker-compose.yml + .env); run install.sh first"
CURRENT_IMAGE=$(grep -E '^NETWORK_REPORT_APP_IMAGE=' "$APP_DIR/.env" | cut -d= -f2-)
TARGET_IMAGE=${IMAGE_ARG:-${NETWORK_REPORT_APP_IMAGE:-$CURRENT_IMAGE}}
echo "current image: $CURRENT_IMAGE"
echo "target  image: $TARGET_IMAGE"

step "target image present locally (offline)"
export NETWORK_REPORT_APP_IMAGE="$TARGET_IMAGE"
check_images

if [[ $TARGET_IMAGE != "$CURRENT_IMAGE" ]]; then
    cp "$APP_DIR/.env" "$APP_DIR/.env.bak"
    chmod 0600 "$APP_DIR/.env.bak"
    sed -i "s|^NETWORK_REPORT_APP_IMAGE=.*$|NETWORK_REPORT_APP_IMAGE=$TARGET_IMAGE|" "$APP_DIR/.env"
    echo ".env updated (previous copy kept as $APP_DIR/.env.bak)"
else
    echo "target equals current image; re-verifying in place"
fi

step "database migration on the target image (alembic upgrade head)"
if ! COMPOSE run --rm --no-deps web alembic upgrade head; then
    if [[ $TARGET_IMAGE != "$CURRENT_IMAGE" ]]; then
        cp "$APP_DIR/.env.bak" "$APP_DIR/.env"
        echo "migration failed — .env restored to $CURRENT_IMAGE; no container was restarted"
    fi
    die 14 "alembic upgrade head failed on the target image; the running stack was left untouched ('docker compose -p network-report logs' shows the old services)"
fi

# Captured AFTER the migration (which does not touch heartbeat rows) and
# immediately before the recreate, so the heartbeat verifier (W05-AUDIT
# fix 1, W05-AUDIT-2) can insist on evidence written after THIS update: the
# old worker's last row must never pass, and when the recreate REPLACES the
# worker container its started_at must have advanced too — the old worker's
# post-baseline final tick otherwise masquerades as new-worker evidence.
step "capturing the pre-restart worker heartbeat baseline"
HEARTBEAT_BASELINE=$(heartbeat_baseline) \
    || die 19 "cannot read the pre-update worker heartbeat baseline (is the database reachable?)"
echo "heartbeat baseline: $HEARTBEAT_BASELINE"
PRE_WORKER_CONTAINER_ID=$(worker_container_id) \
    || die 19 "cannot read the pre-update worker container identity from Docker"
echo "pre-update worker container: ${PRE_WORKER_CONTAINER_ID:-<none>}"

step "restarting web + worker on the target image"
if ! COMPOSE up -d --wait; then
    post_migration_failure 17 "service restart failed; 'docker compose -p network-report ps' shows what happened"
fi

POST_WORKER_CONTAINER_ID=$(worker_container_id) \
    || die 19 "cannot read the worker container identity after the update"
echo "post-update worker container: ${POST_WORKER_CONTAINER_ID:-<none>}"

step "verifying web /health"
if ! wait_health; then
    post_migration_failure 18 "web /health failed after the update; 'docker compose -p network-report logs web' shows why"
fi

step "verifying worker heartbeat"
# `compose up` exiting 0 is not container identity: without the post-start
# worker container ID the replacement decision cannot be made — fail, never
# default to "same container" (W05-AUDIT-2).
if [[ -z $POST_WORKER_CONTAINER_ID ]]; then
    post_migration_failure 19 "worker container identity could not be determined after the update (is the worker running? 'docker compose -p network-report ps')"
fi
if ! wait_heartbeat "$HEARTBEAT_BASELINE" "$PRE_WORKER_CONTAINER_ID" "$POST_WORKER_CONTAINER_ID"; then
    post_migration_failure 19 "no heartbeat evidence from the updated worker (a new tick past the baseline, plus an advanced started_at when its container was replaced); 'docker compose -p network-report logs worker' shows why"
fi

step "update complete"
COMPOSE ps
cat <<EOF

Update complete. Previous image kept in $APP_DIR/.env.bak.
Roll back ONLY after checking that the previous image is compatible with the
(migrated) database schema — see docs/OPERATIONS.md §5:
  $SCRIPT_DIR/update.sh --image $CURRENT_IMAGE
If the previous image cannot run against the migrated schema, restore the
pre-update database backup first (docs/OPERATIONS.md §4), then roll back.
EOF
