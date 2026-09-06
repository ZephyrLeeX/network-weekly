#!/usr/bin/env bash
# acceptance_evidence.sh — read-only evidence collector for W05-T005
# (SYSTEM_SPEC.md §28 acceptance / Release Gate).
#
# Run ON THE PRODUCTION HOST against the INSTALLED stack. It prints a
# markdown evidence block: every line is either a host fact or a database
# aggregate. It never prints secret material (secrets.env content is NOT
# read; only its permission/owner bits are shown).
#
# This tool COLLECTS evidence; it does not grant acceptance. Judgement
# criteria for every section live in docs/ACCEPTANCE.md.
#
# Honors NETWORK_REPORT_INSTALL_ROOT / NETWORK_REPORT_DATA_ROOT /
# NETWORK_REPORT_CONFIG_ROOT (defaults /opt, /data, /etc) so the same
# script also works against a staging install.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=../deploy/lib.sh
source "$SCRIPT_DIR/../deploy/lib.sh"

section() { printf '\n## %s\n' "$*"; }
fact() { printf '%s\n' "$*"; }

printf '# W05-T005 acceptance evidence — collected %s\n' "$(date -Is)"
fact "host: $(uname -sr) / $(dpkg --print-architecture 2>/dev/null || echo unknown-arch)"

section "OS"
fact "$(grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release | tr '\n' ' ')"

section "runtime (docker)"
docker version --format 'engine {{.Server.Version}}'
docker compose version
docker inspect network-report-web-1 --format 'web image: {{.Config.Image}} (log {{json .HostConfig.LogConfig}})' 2>/dev/null \
    || fact "web container not found"
docker inspect network-report-worker-1 --format 'worker image: {{.Config.Image}}' 2>/dev/null \
    || fact "worker container not found"
docker inspect network-report-postgres-1 --format 'postgres image: {{.Config.Image}}' 2>/dev/null \
    || fact "postgres container not found"

section "compose ps"
COMPOSE ps

section "web /health"
COMPOSE exec -T web python -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)))" \
    || fact "/health FAILED"

section "worker heartbeat (age must be < 4x interval)"
COMPOSE exec -T worker python - <<'PY'
from datetime import UTC, datetime

from sqlalchemy import select

from backend.config import load_settings
from backend.db.engine import get_engine
from backend.db.models import WorkerHeartbeat

limit = load_settings().heartbeat_interval_seconds * 4
with get_engine().connect() as conn:
    rows = conn.execute(
        select(WorkerHeartbeat.worker_id, WorkerHeartbeat.version,
               WorkerHeartbeat.started_at, WorkerHeartbeat.last_heartbeat)
    ).all()
for worker_id, version, started_at, last in rows:
    age = (datetime.now(UTC) - last).total_seconds()
    print(f"worker_id={worker_id} version={version} started={started_at} "
          f"age_seconds={age:.0f} limit={limit} {'OK' if age <= limit else 'STALE'}")
PY

section "inventory (expect 10 logical devices)"
COMPOSE exec -T web python - <<'PY'
from sqlalchemy import select

from backend.db.engine import get_engine
from backend.db.models import Device, DeviceMember

with get_engine().connect() as conn:
    devices = conn.execute(
        select(Device.name, Device.model_family, Device.expected_irf_member_count)
        .order_by(Device.name.asc())
    ).all()
    members = conn.execute(select(DeviceMember.device_id, DeviceMember.member_id)).all()
for name, family, expected in devices:
    print(f"device={name} family={family} expected_irf_members={expected}")
print(f"total_devices={len(devices)} total_member_rows={len(members)}")
PY

section "poll runs per device, last 24h (expect 288 planned cycles/day each)"
COMPOSE exec -T web python - <<'PY'
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from backend.db.engine import get_engine
from backend.db.models import Device, DevicePollRun

since = datetime.now(UTC) - timedelta(hours=24)
with get_engine().connect() as conn:
    rows = conn.execute(
        select(Device.name, DevicePollRun.status, func.count())
        .join(DevicePollRun, DevicePollRun.device_id == Device.id)
        .where(DevicePollRun.cycle_started_at >= since)
        .group_by(Device.name, DevicePollRun.status)
        .order_by(Device.name.asc())
    ).all()
for name, status, count in rows:
    print(f"device={name} status={status} count={count}")
PY

section "oldest raw data (retention: the OLDEST row of each raw table must be within ~90 days, A13)"
# W05-AUDIT fix 2: this section used MAX(collected_at) — the NEWEST row — and
# therefore always reported OK while recent data existed. The tested module
# computes the true MIN() oldest-row age per table (§24, A13 BEYOND-90d).
COMPOSE exec -T web python -m backend.ops.retention_evidence \
    || fact "retention evidence query FAILED"

section "weekly reports (registry vs disk)"
COMPOSE exec -T web python - <<'PY'
import os

from sqlalchemy import select

from backend.config import load_settings
from backend.db.engine import get_engine
from backend.db.models import WeeklyReport

report_dir = load_settings().report_dir
with get_engine().connect() as conn:
    rows = conn.execute(
        select(WeeklyReport.week_code, WeeklyReport.status,
               WeeklyReport.period_start, WeeklyReport.period_end,
               WeeklyReport.generated_at, WeeklyReport.file_path)
        .order_by(WeeklyReport.week_code.asc())
    ).all()
for week, status, start, end, generated, path in rows:
    exists = bool(path) and os.path.isfile(path)
    print(f"week={week} status={status} period={start}..{end} "
          f"generated={generated} file_on_disk={exists}")
print(f"reports_dir={report_dir}")
PY
fact "reports dir listing:"
ls -la "$DATA_DIR/reports" 2>/dev/null || fact "reports dir missing"

section "secrets / inventory permission bits (content is NEVER read)"
stat -c '%a %u %n' "$CONFIG_DIR/secrets.env" "$CONFIG_DIR/devices.toml" 2>/dev/null \
    || fact "config files missing"

section "monitoring incidents / IRF observations (long-term tables)"
COMPOSE exec -T web python - <<'PY'
from sqlalchemy import func, select

from backend.db.engine import get_engine
from backend.db.models import (
    DeviceReachabilityIncident,
    InterfaceStateIncident,
    IrfMemberObservation,
)

with get_engine().connect() as conn:
    for label, table in (
        ("device_reachability_incidents", DeviceReachabilityIncident),
        ("interface_state_incidents", InterfaceStateIncident),
        ("irf_member_observations", IrfMemberObservation),
    ):
        print(f"{label}: {conn.execute(select(func.count()).select_from(table)).scalar_one()} rows")
PY

section "how to record"
fact "paste this output into docs/evidence/<item-id>.md per docs/ACCEPTANCE.md;"
fact "redact hostnames/IPs if the target network must stay unidentified."
