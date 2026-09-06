#!/usr/bin/env bash
# install.sh — Network Weekly Report System (W05-T002, SYSTEM_SPEC.md §26.2).
#
# Target: Debian 13 amd64 with Docker Engine + Compose plugin already
# installed and the two images loaded locally. This script never touches the
# Internet: no package installs, no image pulls, no downloads.
#
# Idempotent: re-running a successful install keeps the existing .env (and
# thus the PostgreSQL password), keeps devices.toml/secrets.env and the
# administrator account, re-syncs inventory and re-verifies health.
#
# Root-privilege paths (default /opt, /data, /etc) can be redirected with
# NETWORK_REPORT_INSTALL_ROOT / NETWORK_REPORT_DATA_ROOT /
# NETWORK_REPORT_CONFIG_ROOT for staging installs — then root is not
# required, but the caller's uid must be 1000 (the in-image app user).
#
# The administrator password is never placed in argv, stdout or logs: it is
# read from NETWORK_REPORT_ADMIN_PASSWORD when exported (unattended
# installs), otherwise prompted twice without echo, and handed to the
# one-off container only through its environment.
#
# Exit codes: see deploy/lib.sh (10..19, each failure prints a diagnostic).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

umask 077

step "host checks"
if [[ $(id -u) -ne 0 ]]; then
    if [[ "$INSTALL_ROOT" == /opt && "$DATA_ROOT" == /data && "$CONFIG_ROOT" == /etc ]]; then
        die 10 "must run as root (or redirect all three NETWORK_REPORT_*_ROOT roots for a staging install)"
    fi
    [[ $(id -u) -eq 1000 ]] || die 10 "non-root staging install requires uid 1000 (the in-image app user); this user is $(id -u)"
fi
[[ -r /etc/os-release ]] || die 10 "/etc/os-release not readable: is this Debian?"
# shellcheck disable=SC1091
. /etc/os-release
[[ ${ID:-} == debian && ${VERSION_ID:-} == 13 ]] \
    || die 10 "target OS must be Debian 13 (found ID=${ID:-unset} VERSION_ID=${VERSION_ID:-unset})"
[[ $(dpkg --print-architecture) == amd64 ]] \
    || die 10 "target architecture must be amd64 (found $(dpkg --print-architecture))"
check_docker

step "application directory $APP_DIR"
mkdir -p "$APP_DIR"
[[ -f $SCRIPT_DIR/docker-compose.prod.yml ]] \
    || die 13 "deploy/docker-compose.prod.yml not found next to install.sh"
install -m 0644 "$SCRIPT_DIR/docker-compose.prod.yml" "$APP_DIR/docker-compose.yml"

if [[ -f $APP_DIR/.env ]]; then
    echo "keeping existing $APP_DIR/.env"
else
    # Constrained alphabet: the password is interpolated into DATABASE_URL
    # without any URL escaping.
    POSTGRES_PASSWORD=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
    cat > "$APP_DIR/.env" <<EOF
NETWORK_REPORT_APP_IMAGE=${NETWORK_REPORT_APP_IMAGE:-network-weekly-app:0.1.0}
NETWORK_REPORT_DATA_DIR=$DATA_DIR
NETWORK_REPORT_CONFIG_DIR=$CONFIG_DIR
NETWORK_REPORT_WEB_PORT=${NETWORK_REPORT_WEB_PORT:-8000}
POSTGRES_USER=network_report
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_DB=network_report
NETWORK_REPORT_TIMEZONE=${NETWORK_REPORT_TIMEZONE:-Asia/Shanghai}
NETWORK_REPORT_LOG_LEVEL=${NETWORK_REPORT_LOG_LEVEL:-INFO}
NETWORK_REPORT_WORKER_ID=${NETWORK_REPORT_WORKER_ID:-worker}
NETWORK_REPORT_HEARTBEAT_INTERVAL_SECONDS=${NETWORK_REPORT_HEARTBEAT_INTERVAL_SECONDS:-30}
NETWORK_REPORT_RETENTION_DAYS=${NETWORK_REPORT_RETENTION_DAYS:-90}
EOF
    unset POSTGRES_PASSWORD
    echo "wrote $APP_DIR/.env (0600, generated PostgreSQL password)"
fi
chmod 0600 "$APP_DIR/.env"

step "persistent data directory $DATA_DIR"
mkdir -p "$DATA_DIR/postgres" "$DATA_DIR/reports"
if [[ $(id -u) -eq 0 ]]; then
    chown -R 1000:1000 "$DATA_DIR/reports"
else
    [[ $(stat -c %u "$DATA_DIR/reports") == 1000 ]] \
        || die 13 "$DATA_DIR/reports must be owned by uid 1000 for the non-root app container"
fi
chmod 0755 "$DATA_DIR" "$DATA_DIR/reports"

step "runtime configuration $CONFIG_DIR"
mkdir -p "$CONFIG_DIR"
[[ -f $SCRIPT_DIR/../docs/devices.toml.example && -f $SCRIPT_DIR/../docs/secrets.env.example ]] \
    || die 13 "docs/devices.toml.example and docs/secrets.env.example not found next to deploy/"
if [[ -f $CONFIG_DIR/devices.toml ]]; then
    echo "keeping existing devices.toml"
else
    install -m 0644 "$SCRIPT_DIR/../docs/devices.toml.example" "$CONFIG_DIR/devices.toml"
    echo "initialized devices.toml from the example — EDIT real management IPs before go-live"
fi
if [[ -f $CONFIG_DIR/secrets.env ]]; then
    echo "keeping existing secrets.env"
else
    # Only the documented placeholder template is installed. Real device
    # secrets are NEVER generated automatically — the operator fills them in.
    install -m 0600 "$SCRIPT_DIR/../docs/secrets.env.example" "$CONFIG_DIR/secrets.env"
    echo "initialized secrets.env from the example — EDIT real credentials before go-live"
fi
if [[ $(id -u) -eq 0 && $(stat -c %u "$CONFIG_DIR/secrets.env") != 1000 ]]; then
    chown 1000:1000 "$CONFIG_DIR/secrets.env"
    echo "secrets.env ownership set to the deployment service account (1000:1000)"
fi
# An existing file's permissions are verified, never silently tightened: a
# too-permissive secrets file is an operator problem to surface, not hide.
mode=$(stat -c %a "$CONFIG_DIR/secrets.env")
[[ $mode == 600 ]] || die 13 \
    "secrets.env must have permission 0600 (found $mode) — refusing to install against a too-permissive secrets file; fix with: chmod 0600 $CONFIG_DIR/secrets.env"
if grep -q 'change-me' "$CONFIG_DIR/secrets.env"; then
    echo "WARNING: secrets.env still contains placeholder credentials; device polls will" \
         "record FAILED runs with clear errors until real values are set (they are" \
         "picked up without a restart)."
fi

step "image checks (offline)"
check_images

step "host port check"
WEB_PORT=$(grep -E '^NETWORK_REPORT_WEB_PORT=' "$APP_DIR/.env" | cut -d= -f2-)
WEB_PORT=${WEB_PORT:-8000}
if ! COMPOSE ps --status running web 2>/dev/null | grep -q .; then
    # Probe in a subshell: the socket fd closes with it.
    if (exec 3<>"/dev/tcp/127.0.0.1/$WEB_PORT") 2>/dev/null; then
        die 17 "TCP port $WEB_PORT is already in use by another process; free it or set NETWORK_REPORT_WEB_PORT in $APP_DIR/.env"
    fi
fi

step "starting PostgreSQL"
COMPOSE up -d --wait --no-deps postgres \
    || die 17 "PostgreSQL did not become healthy; 'docker compose -p network-report logs postgres' shows why"

step "database migration (alembic upgrade head)"
if ! COMPOSE run --rm --no-deps web alembic upgrade head; then
    die 14 "alembic upgrade head failed; the previous schema and containers are left untouched"
fi

step "single administrator"
if admin_exists; then
    echo "administrator account already initialized — keeping it"
elif [[ -n ${NETWORK_REPORT_ADMIN_PASSWORD:-} ]]; then
    COMPOSE run --rm --no-deps -e NETWORK_REPORT_ADMIN_PASSWORD \
        web python -m backend.admin_cli init admin \
        || die 15 "administrator initialization failed"
    echo "administrator 'admin' initialized from the exported password variable"
else
    while true; do
        printf 'Choose the administrator password: '
        read -rs PASSWORD
        printf '\n'
        printf 'Repeat the password: '
        read -rs PASSWORD2
        printf '\n'
        [[ -n $PASSWORD ]] || { echo "password must not be empty"; continue; }
        [[ $PASSWORD == "$PASSWORD2" ]] && break
        echo "passwords do not match, try again"
    done
    # Environment passthrough only: the value must never appear in argv of
    # the docker CLI (ps-visible) nor in any log line.
    export NETWORK_REPORT_ADMIN_PASSWORD="$PASSWORD"
    COMPOSE run --rm --no-deps -e NETWORK_REPORT_ADMIN_PASSWORD \
        web python -m backend.admin_cli init admin \
        || { unset NETWORK_REPORT_ADMIN_PASSWORD PASSWORD PASSWORD2; die 15 "administrator initialization failed"; }
    unset NETWORK_REPORT_ADMIN_PASSWORD PASSWORD PASSWORD2
    echo "administrator 'admin' initialized"
fi

step "device inventory sync"
COMPOSE run --rm --no-deps web python -m backend.inventory_sync \
    || die 16 "inventory sync failed (is $CONFIG_DIR/devices.toml valid TOML?)"

# Captured BEFORE the stack starts so the heartbeat verifier can insist on a
# heartbeat written after THIS start (W05-AUDIT fix 1): a row left by the
# previous worker — even seconds old — must never pass the verification.
step "capturing the pre-start worker heartbeat baseline"
HEARTBEAT_BASELINE=$(heartbeat_baseline) \
    || die 19 "cannot read the pre-start worker heartbeat baseline (is the database reachable?)"
echo "heartbeat baseline: $HEARTBEAT_BASELINE"

step "starting web + worker"
COMPOSE up -d --wait || die 17 "docker compose could not bring the stack up (see the output above)"

step "verifying web /health"
verify_health

step "verifying worker heartbeat"
verify_heartbeat "$HEARTBEAT_BASELINE"

step "install complete"
COMPOSE ps
cat <<EOF

Next steps:
  - Web UI:            http://<this-host>:8000/  (administrator 'admin')
  - Device inventory:  edit $CONFIG_DIR/devices.toml, then re-run install.sh (idempotent)
  - Device secrets:    edit $CONFIG_DIR/secrets.env (0600) — never auto-generated
  - Operator runbook:  docs/OPERATIONS.md
EOF
