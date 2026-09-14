# W05-LINUX-PORTABILITY-FIX — engineering evidence

Status: REVIEW_PASSED, 2026-09-14, branch `work/wave-05`.

The Compose preflight now tests deployment capabilities instead of parsing a
major version. It requires successful `docker info`, `docker compose version`,
`docker compose up --help` containing `--wait`, and `docker compose ps --help`
containing `--status`. Production install, update and acceptance paths continue
to execute `docker compose config -q`. The standalone `docker-compose` command
is never invoked or accepted as a substitute.

Synthetic tests cover Compose 2.x, 5.x and future major 99 with all required
capabilities, plus missing plugin, missing `--wait`, missing `--status`, and a
PATH containing only standalone `docker-compose`. Synthetic tests are not
real-host acceptance evidence.

No business code or migration is changed. Alembic must remain at `0011`.
W05-GATE remains BLOCKED, formal two-week acceptance is incomplete, and `main`
must remain unmerged.

Verification results:

- 42 focused deployment tests PASS. Compose 2.39.1, 5.5.1 and future 99.0.0
  pass with all capabilities. Missing plugin, exact `--wait`, exact `--status`,
  and standalone-only cases fail. Similar options cannot satisfy the probe.
- Real host Docker Compose 5.5.1: `version` succeeded; `up --help` exposed
  `--wait`; `ps --help` exposed `--status`; `check_docker` exited 0.
- Full unit suite: 371 PASS (two existing dependency deprecation warnings).
- Integration suite: 266 PASS against disposable PostgreSQL 17.
- `ruff check .` PASS; `mypy` PASS for 137 source files.
- Empty PostgreSQL migrated 0001→0011; direct `alembic_version` query returned
  `0011`; no migration file changed or was added.
- Production `install.sh`, `update.sh`, and `acceptance_evidence.sh` each retain
  an actual `COMPOSE config -q` execution.

Implementation checkpoint: `4fbdc97967f310c2cc2889d425df17f42d09ba6c`.
