# W05-LINUX-PORTABILITY — engineering evidence, 2026-09-11

Status: IMPLEMENTED / REAL_UBUNTU_SMOKE_PENDING. Branch: work/wave-05.
Implementation checkpoint: cd9b4d00450c9f66488ea001138cbbfb97f25be5.
This is automated engineering evidence, not real Ubuntu or formal acceptance.

- Removed Debian 13 / ID / VERSION_ID gates and dpkg architecture queries.
- Linux kernel required; uname -m accepts x86_64/amd64 as linux/amd64.
- Commands checked per script (complete audited lists in deploy/README.md).
- Docker daemon usability, Docker Compose plugin invocation and actual
  production Compose config validation; standalone docker-compose never
  substituted. The major-version restriction recorded here was superseded by
  W05-LINUX-PORTABILITY-FIX.
- Local application and postgres:17-alpine image inspection retained.
- Default roots require root; non-root UID 1000 staging requires all roots
  redirected. Filesystem/config operation failures explicitly exit 13.
- A01 judges actual capabilities + successful install; optional os-release
  content is informational only. Evidence script works without dpkg/rpm.
- Production Compose unchanged: web/worker/postgres, each pull_policy never,
  no build; original bind mounts, secrets 0600, UID/GID 1000 unchanged.
- No collector/business changes, new migration, package-manager/download
  commands, or external runtime dependency.

Verification:
- pytest: 365 passed (including 25 new executable capability tests).
- pytest -m integration: 266 passed, real disposable PostgreSQL 17;
  114.98 seconds. No real H3C evidence inferred from these tests.
- ruff check .: PASS.
- mypy: PASS, 137 source files.
- alembic upgrade head: empty disposable DB migrated 0001→0011; SQL query
  of alembic_version returned 0011; alembic heads = 0011; no migration diff.
- bash syntax and existing Compose/security/heartbeat regression checks PASS.
- rg 'dpkg --print-architecture' deploy scripts: zero matches.
- pytest reported two existing dependency deprecation warnings.

Test setup: initial virtualenv lacked locked httpx/python-multipart dependencies;
uv sync --locked completed the development environment (lockfile unchanged).
This development-only dependency preparation is not an offline deployment test.
Database tests required sandbox escalation for local Docker/loopback access.

Real-host smoke: NOT executed. Current host is Arch Linux x86_64 with Docker
Compose 5.5.1, not Ubuntu 24.04 LTS. The earlier installer rejection based on
that major version was incorrect and is superseded by W05-LINUX-PORTABILITY-FIX.
No host/Docker restart, Ubuntu install, real inventory sync or
full real evidence-collector PASS is claimed. Required Owner Ubuntu offline
smoke checklist is in docs/ACCEPTANCE.md. No second-distro smoke claimed.

W05-T005 remains IN_PROGRESS — REAL_ENVIRONMENT_EVIDENCE_PENDING.
W01-T007 BLOCKED — FIELD_VALIDATION_PENDING.
W03-T010 BLOCKED — REAL_WEEK_DATA_PENDING.
W05-GATE BLOCKED. Formal two-week acceptance NOT complete. main NOT merged.
Historical W05-AUDIT / W05-AUDIT-2 / W05-PRE-ACCEPTANCE-HARDENING checkpoints
and their outcomes are preserved.
