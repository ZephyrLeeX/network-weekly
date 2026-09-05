# TASK_GRAPH.md

## Authority

本文件与 `EXECUTION_STATE.md` 是唯一 Task 状态权威来源。

States:

```text
TODO
READY
IN_PROGRESS
BLOCKED
IMPLEMENTED
REVIEW_FAILED
REVIEW_PASSED
```

只有依赖项为 `REVIEW_PASSED` 或依赖 Gate 为 PASS 时，后续 Task 才可进入 READY。

---

# Wave 0 — Engineering Foundation

## W00-T001 — Initialize repository structure
**Status:** REVIEW_PASSED  
**Depends On:** none  
**Blocks:** W00-T002, W00-T003  
**Scope:** create application/docs/scripts/tests structure; install the five control files at repository root.  
**Acceptance:** required directories and five control files exist; repository contains no implementation outside current Task.

## W00-T002 — Python project and quality toolchain
**Status:** REVIEW_PASSED  
**Depends On:** W00-T001  
**Blocks:** W00-T004, W00-T005  
**Scope:** `pyproject.toml`, `uv.lock`, pytest, ruff, type-check command, dependencies required by `SYSTEM_SPEC.md`.  
**Acceptance:** frozen dependency install works; test/lint/type commands are documented and pass.

## W00-T003 — Compose skeleton
**Status:** REVIEW_PASSED  
**Depends On:** W00-T001  
**Blocks:** W00-T004  
**Scope:** one application image; `web`, `worker`, `postgres`; persistent data paths.  
**Acceptance:** all three services start with correct separation of commands and persistent PostgreSQL/report storage.

## W00-T004 — PostgreSQL and Alembic baseline
**Status:** REVIEW_PASSED  
**Depends On:** W00-T002, W00-T003  
**Blocks:** W01-T001, W01-T002  
**Acceptance:** fresh PostgreSQL reaches Alembic head; production schema creation is migration-driven.

## W00-T005 — Runtime config, logging, health and worker heartbeat
**Status:** REVIEW_PASSED  
**Depends On:** W00-T002, W00-T004  
**Blocks:** W01-T003, W05-T001  
**Scope:** runtime config; explicit Asia/Shanghai; secret-safe logging; health endpoint; persistent worker heartbeat.  
**Acceptance:** timezone is explicit; secret patterns are redacted; web health and worker heartbeat are independently testable.

## W00-GATE — Foundation Gate
**Status:** PASS
**Depends On:** W00-T004, W00-T005
**Blocks:** Wave 1
**Gate:** web/worker/postgres/Alembic/tests/lint/type operational; dependencies match `SYSTEM_SPEC.md`.

## W00-AUDIT — Wave 0 audit hotfix
**Status:** REVIEW_PASSED
**Depends On:** W00-GATE
**Blocks:** none (quality/audit only; no product capability added)
**Scope:** web/uvicorn secret redaction wiring; integration-test DROP/CREATE guard (test-name + loopback + explicit opt-in + `psycopg.sql.Identifier`); compose runtime-config injection for web/worker; README stage/de-redaction corrections; reports volume writability as non-root; `dispose_engine` disposes the engine.
**Acceptance:** all six audit items verified (unit + integration + live container evidence); W00-GATE revalidated PASS; no empty migration; Wave 1 not started.

---

# Wave 1 — Real H3C Collection

## W01-T001 — Device inventory and secrets loading
**Status:** REVIEW_PASSED
**Depends On:** W00-GATE
**Blocks:** W01-T003, W01-T005
**Scope:** `/etc/network-report/devices.toml`; `/etc/network-report/secrets.env`; inventory sync; permission validation.
**Acceptance:** current 10 logical devices are representable; secrets file must be `0600`; secrets are never logged or returned.
**Checkpoint:** see EXECUTION_STATE.md checkpoint ledger.

## W01-T002 — Device, member, interface and aggregation schema
**Status:** REVIEW_PASSED
**Depends On:** W00-GATE
**Blocks:** W01-T004, W01-T005, W01-T006
**Scope:** Device, DeviceMember, Interface, aggregation member relationship; normalized interface identity.
**Acceptance:** 8 standalone devices + S10500X IRF + S12500 IRF topology is representable; ifIndex is mutable metadata.
**Checkpoint:** see EXECUTION_STATE.md checkpoint ledger (executed before W01-T001 so inventory sync has real schema coverage at its own checkpoint; both tasks depend only on W00-GATE).

## W01-T003 — SNMP transport and basic H3C collection
**Status:** REVIEW_PASSED (engineering acceptance: H3C official MIB verification + tests; real-device evidence deferred to W01-T007)
**Depends On:** W01-T001
**Blocks:** W01-T004, W01-T006
**Scope:** bounded SNMP timeouts/retries; identity, CPU, memory, interface state, speed and counters.
**Acceptance:** real standalone S10500X can be collected; errors normalize cleanly; bulk/walk used where appropriate.
**Engineering acceptance basis:** OIDs verified against the standard MIB modules H3C Comware implements per the H3C MIB Companion — IF-MIB/ifXTable (RFC 2863: ifHighSpeed Mbps preferred over saturated ifSpeed; separate ifAdminStatus/ifOperStatus maps, ifOperStatus 1–7), EtherLike-MIB (RFC 3635: dot3HCStatsFCSErrors 1.3.6.1.2.1.10.7.11.1.2 preferred, dot3StatsFCSErrors 1.3.6.1.2.1.10.7.2.1.3 fallback). `collect_interfaces` cannot report SUCCESS when the core ifDescr walk fails or yields no usable row (§8). Fixtures in `tests/fixtures/h3c/` remain synthetic placeholders (see their README) to be replaced by anonymized real captures in W01-T007.
**Notes:** PySNMP 7.x is asyncio-only; `SnmpClient` provides a sync facade with per-request timeout/retries and a wall-clock walk deadline. H3C enterprise CPU/memory OIDs remain pending real-device confirmation.

## W01-T004 — Interface discovery and aggregation mapping
**Status:** REVIEW_PASSED (engineering acceptance: H3C official MIB verification + tests; real-device evidence deferred to W01-T007)
**Depends On:** W01-T002, W01-T003
**Blocks:** W01-T006, W02-T005
**Acceptance:** real interfaces persist; logical aggregation interfaces are identified; member mapping persists; ifIndex changes do not duplicate business interfaces.
**Engineering acceptance basis:** LAG membership OIDs corrected to the published IEEE8023-LAG-MIB (IEEE 802.1AX) — dot3adAggPortSelectedAggID = …1.2.1.1.12, dot3adAggPortAttachedAggID = …1.2.1.1.13; membership prefers Attached with Selected fallback, confined to the adapter/OID layer. Discovery reconciles both directions: one successful collection reporting no aggregations clears stale memberships and `is_aggregation` flags; a failed collection never reaches the sync, so state cannot be wiped on failure. Verified by unit + integration tests against migrated PostgreSQL.

## W01-T005 — Limited SSH transport and IRF discovery
**Status:** REVIEW_PASSED (engineering acceptance: H3C official command reference verification + tests; real-device evidence deferred to W01-T007)
**Depends On:** W01-T001, W01-T002
**Blocks:** W01-T006, W02-T004
**Scope:** explicit read-only identity/version/IRF commands plus lightweight reachability confirmation.
**Acceptance:** S10500X IRF and S12500 IRF member count/identity/role can be normalized from real-device evidence.
**Engineering acceptance basis:** `display version` parser handles the official Comware 7 S10500/S12500 output ("H3C Comware Software, Version 7.1.070, Release 7596P10") including the "Software Version" variant; SSH static collectors (`collect_software_info`, `collect_irf_members`) run the allowlisted `display version` / `display irf` / `display irf configuration` commands and normalize id/role (roles never guessed). Command Reference semantics verified; real output shapes fixed in W01-T007.

## W01-T006 — Normalized collection DTO and persistence
**Status:** REVIEW_PASSED (engineering acceptance: end-to-end unit + integration tests; real-device evidence deferred to W01-T007)
**Depends On:** W01-T003, W01-T004, W01-T005
**Blocks:** Wave 2
**Scope:** stable DTOs; section results; SUCCESS/PARTIAL/FAILED; persist valid data when another section fails.
**Acceptance:** one parser/section failure does not discard valid device data; secrets absent from persisted diagnostic fields.
**Engineering acceptance basis:** SSH reachability probe runs only when the SNMP management channel yielded no valid data at all (§9.1) — a single CPU/memory/LAG section failure no longer triggers SSH. SSH static collection is wired as caller-scheduled flows (`run_static_ssh_collection`, `run_irf_observation` for the ~15-minute Wave 2 cadence), never forced into the 5-minute poll. `device.software_version` and IRF member id/role persist (verified against migrated PostgreSQL); a later software-section failure never erases the stored version.

## W01-T007 — Real-device collection acceptance
**Status:** BLOCKED — FIELD_VALIDATION_PENDING (no reachable real S10500X/S12500 in this environment; release-blocking, not Wave-2-blocking)
**Depends On:** W01-T006
**Blocks:** W05-GATE (production Release Gate)
**Acceptance:** one standalone S10500X, one S10500X IRF and one S12500 IRF validated end-to-end with anonymized fixtures/evidence: replace synthetic fixtures in `tests/fixtures/h3c/`, confirm H3C enterprise OIDs in `backend/collect/h3c/oids.py` (hh3c-entity-ext CPU/memory) and the Comware aggregation-id-ifIndex equivalence, then re-run the full gate.

## W01-GATE — Wave 1 Engineering Gate
**Status:** PASS (engineering gate)
**Depends On:** W01-T001, W01-T002, W01-T003, W01-T004, W01-T005, W01-T006
**Blocks:** Wave 2
**Gate:** all raw data required by weekly reporting can be obtained by the implemented transports, normalized into stable DTOs and persisted reliably, with section isolation and SUCCESS/PARTIAL/FAILED semantics — proven by unit + integration tests and H3C official documentation verification. Real-device proof is NOT part of this gate; it is carried by W01-T007, which blocks the production Release Gate (W05-GATE), not Wave 2.

---

# Wave 2 — Monitoring Pipeline

## W02-T001 — Poll-run and metric schema
**Status:** REVIEW_PASSED
**Depends On:** W01-GATE (PASS — READY granted 2026-09-05, owner authorized Wave 2)  
**Blocks:** W02-T002, W02-T003, W02-T008  
**Scope:** device_poll_runs, device_metrics, interface_metrics, timestamps and indexes.  
**Acceptance:** all data required for 90-day retention and weekly statistics is persistable.
**Implementation:** migration `0003`; one `device_poll_runs` row per `(device_id, cycle_started_at)` (unique, idempotent persistence via ON CONFLICT — a skipped/already-run cycle is never fabricated twice); `device_metrics` holds the device-level peak CPU/memory per cycle (NULL stays missing); `interface_metrics` holds per-interface states, speed snapshot, cumulative counters (ifXTable semantics reused) plus utilization columns filled from W02-T003. Retention/weekly-statistics time indexes on all three tables.
**Tests:** unit (table registration, cycle identity, retention columns, utilization columns) + integration (`tests/integration/test_monitoring_pipeline.py`: SUCCESS/PARTIAL/FAILED persistence, missing-stays-missing, unknown interface skipped, same-cycle idempotency, two devices one cycle).

## W02-T002 — Five-minute DEVICE_POLL scheduler
**Status:** REVIEW_PASSED  
**Depends On:** W02-T001, W01-T006  
**Blocks:** W02-T003, W02-T004, W02-T007  
**Acceptance:** aligned 5-minute cycles; no overlapping poll for the same device; restart resumes future cycles without mass realtime backfill.
**Implementation:** `monitoring/scheduler.py` (epoch-aligned 5-minute boundaries; per-device in-flight registry — a poll outliving its cycle is skipped, that cycle stays missing with no run row, never overlapped; loop always targets the next future boundary so restart never backfills §27.12; per-device executor threads contain failures §8); `monitoring/poll.py` (`poll_device`: Wave 1 `run_collection` re-used unchanged + topology sync + poll run + metrics in ONE transaction); `monitoring/credentials.py` (per-cycle device reload from DB + 0600 secrets; SNMP community required, SSH optional — SNMP-only devices simply have no §9.1 probe); worker runs heartbeat + scheduler threads on one stop event.
**Tests:** unit `tests/unit/test_monitoring_scheduler.py` (alignment, strictly-future cycles, no-overlap skip, restart no-backfill, per-device failure containment, loader-failure survival) + `tests/unit/test_monitoring_credentials.py`; integration `tests/integration/test_poll_device.py` (full cycle persisted end-to-end, FAILED run recorded with §9.1 probe result).

## W02-T003 — Utilization and counter rebaseline
**Status:** TODO  
**Depends On:** W02-T001, W02-T002  
**Blocks:** W02-T006, W03-T002, W03-T005  
**Acceptance:** actual elapsed time used; invalid/reset counters rebaseline; ingress/egress utilization stored consistently; no false spikes.

## W02-T004 — Device reachability state machine
**Status:** TODO  
**Depends On:** W02-T002, W01-T005  
**Blocks:** W03-T003  
**Acceptance:** SNMP fail triggers one SSH confirmation; two failed cycles confirm Down; two reachable cycles confirm Recovery; SSH-only reachability does not convert missing SNMP samples into success.

## W02-T005 — Priority interface configuration service
**Status:** TODO  
**Depends On:** W01-T004  
**Blocks:** W02-T006, W04-T005  
**Scope:** `monitored` flag and aggregation-aware persistence.  
**Acceptance:** aggregation interface can be monitored independently; member interfaces remain separately selectable; rediscovery preserves monitored state.

## W02-T006 — Priority interface Down and high-utilization semantics
**Status:** TODO  
**Depends On:** W02-T003, W02-T005  
**Blocks:** W03-T003, W03-T005  
**Acceptance:** two valid Down cycles confirm; two valid Up cycles recover; missing samples do not count; >=80% for three consecutive valid samples marks sustained high utilization.

## W02-T007 — CPU and memory sustained-high detection
**Status:** TODO  
**Depends On:** W02-T002  
**Blocks:** W03-T002  
**Acceptance:** default >=80% for 15 minutes; thresholds configurable; missing samples break continuity.

## W02-T008 — IRF periodic observation
**Status:** TODO  
**Depends On:** W02-T001, W01-T005  
**Blocks:** W03-T004  
**Acceptance:** expected/current members persist; member missing/reappeared is detectable; role change stored when source data is reliable.

## W02-T009 — Ninety-day retention maintenance
**Status:** TODO  
**Depends On:** W02-T001  
**Blocks:** W02-GATE  
**Acceptance:** batched cleanup removes expired raw metrics/poll runs without deleting long-term incident/report data.

## W02-GATE — Monitoring Pipeline Gate
**Status:** TODO  
**Depends On:** W02-T004, W02-T006, W02-T007, W02-T008, W02-T009  
**Blocks:** Wave 3  
**Gate:** 5-minute monitoring data, incident semantics, IRF observations and retention behavior are trustworthy for weekly statistics.

---

# Wave 3 — Weekly Statistics and DOCX

## W03-T001 — Report period and ISO week code
**Status:** TODO  
**Depends On:** W02-GATE  
**Blocks:** W03-T002, W03-T003, W03-T004, W03-T005  
**Acceptance:** exact `[Monday 00:00, next Monday 00:00)` Asia/Shanghai periods including ISO year-boundary tests.

## W03-T002 — CPU/Memory/P95 and interface Top 10
**Status:** TODO  
**Depends On:** W03-T001, W02-T003, W02-T007  
**Blocks:** W03-T006  
**Acceptance:** average/max/P95 correct; PostgreSQL `percentile_cont(0.95)` used; Top 10 algorithm matches `SYSTEM_SPEC.md` and golden tests.

## W03-T003 — Weekly device and priority-interface incident summary
**Status:** TODO  
**Depends On:** W03-T001, W02-T004, W02-T006  
**Blocks:** W03-T006  
**Acceptance:** weekly Down/Recovery/ongoing presentation handles cross-week intervals correctly.

## W03-T004 — IRF weekly summary
**Status:** TODO  
**Depends On:** W03-T001, W02-T008  
**Blocks:** W03-T006  
**Acceptance:** member missing/reappeared/current missing are summarized correctly even when logical device remained reachable.

## W03-T005 — CRC/Error/Drop deltas and Monitoring Coverage
**Status:** TODO  
**Depends On:** W03-T001, W02-T001, W02-T003, W02-T006  
**Blocks:** W03-T006  
**Acceptance:** reset-safe weekly deltas; Top observations; expected/success/partial/failed correct; Coverage formula correct; <95% produces data-integrity warning.

## W03-T006 — WeeklyStatisticsService and overall status
**Status:** TODO  
**Depends On:** W03-T002, W03-T003, W03-T004, W03-T005  
**Blocks:** W03-T007  
**Acceptance:** one service produces all report-ready values; Normal/Attention/Abnormal rules deterministic; missing data remains explicit.

## W03-T007 — Weekly statistics golden tests
**Status:** TODO  
**Depends On:** W03-T006  
**Blocks:** W03-T008  
**Acceptance:** period, cross-week, missing sample, threshold continuity, P95, Top 10, reset, PARTIAL and Coverage cases pass.

## W03-T008 — DOCX renderer
**Status:** TODO  
**Depends On:** W03-T007  
**Blocks:** W03-T009  
**Scope:** `python-docx`; eight fixed sections; table-first layout; temporary file + atomic replacement.  
**Acceptance:** readable DOCX; missing data explicit; blank editable “本周处理问题” area; no secrets.

## W03-T009 — Persistent report jobs, Monday schedule and retry
**Status:** TODO  
**Depends On:** W03-T008  
**Blocks:** W03-T010  
**Acceptance:** Monday 00:10 job; 10-minute retry; worker restart recovery; maximum one active job per week; status/error persisted.

## W03-T010 — Manual regenerate service and real report verification
**Status:** TODO  
**Depends On:** W03-T009  
**Blocks:** W03-GATE, W04-T004  
**Acceptance:** regenerate replaces one current server DOCX for the week; real weekly report manually checked against source samples.

## W03-GATE — Weekly Report Gate
**Status:** TODO  
**Depends On:** W03-T010  
**Blocks:** Wave 4  
**Gate:** trustworthy DOCX can be generated from persisted real weekly data and survives failure/restart scenarios.

---

# Wave 4 — Login and Operations Web

## W04-T001 — Single administrator and password storage
**Status:** TODO  
**Depends On:** W03-GATE  
**Blocks:** W04-T002  
**Acceptance:** one local administrator can be initialized; salted scrypt hash stored; plaintext password never persisted.

## W04-T002 — Server-side session and login/logout
**Status:** TODO  
**Depends On:** W04-T001  
**Blocks:** W04-T003, W04-T004, W04-T005  
**Acceptance:** 7-day absolute and 12-hour idle timeout; HttpOnly/SameSite cookie; unauthenticated protected requests denied; login/logout tests pass.

## W04-T003 — Report list and download
**Status:** TODO  
**Depends On:** W04-T002  
**Blocks:** W04-GATE  
**Acceptance:** reverse chronological report list; period/status/generated time/error visible; authenticated DOCX download works.

## W04-T004 — Manual regenerate Web action
**Status:** TODO  
**Depends On:** W04-T002, W03-T010  
**Blocks:** W04-GATE  
**Acceptance:** CSRF-protected; duplicate submissions do not create concurrent duplicate report jobs; status becomes visible to user.

## W04-T005 — Priority interface Web page
**Status:** TODO  
**Depends On:** W04-T002, W02-T005  
**Blocks:** W04-GATE  
**Acceptance:** device selection; interface name/description/state; aggregation relationship; monitored toggle; aggregation toggle does not auto-toggle members.

## W04-GATE — Operations Web Gate
**Status:** TODO  
**Depends On:** W04-T003, W04-T004, W04-T005  
**Blocks:** Wave 5  
**Gate:** login -> configure priority interfaces -> view/download/regenerate report workflow passes.

---

# Wave 5 — Deployment and Stability

## W05-T001 — Production directory and configuration layout
**Status:** TODO  
**Depends On:** W04-GATE, W00-T005  
**Blocks:** W05-T002  
**Acceptance:** `/opt`, `/data`, `/etc` layout documented; secrets permission checked; DB and DOCX files survive application-container replacement.

## W05-T002 — install.sh
**Status:** TODO  
**Depends On:** W05-T001  
**Blocks:** W05-T003, W05-T005  
**Acceptance:** Debian 13 target with Docker/Compose can initialize directories, DB, administrator, inventory and healthy web/worker services.

## W05-T003 — update.sh
**Status:** TODO  
**Depends On:** W05-T002  
**Blocks:** W05-T005  
**Acceptance:** target image update + Alembic migration + service restart + health/heartbeat verification succeeds.

## W05-T004 — Logging, retention scheduling and operator runbook
**Status:** TODO  
**Depends On:** W04-GATE  
**Blocks:** W05-T005  
**Acceptance:** logs bounded/rotatable; retention runs safely; common failure checks and report regeneration steps documented.

## W05-T005 — Real-environment stability acceptance
**Status:** TODO  
**Depends On:** W05-T002, W05-T003, W05-T004  
**Blocks:** W05-GATE  
**Acceptance:** all `SYSTEM_SPEC.md` Acceptance items have concrete test/evidence; at least two complete reporting weeks observed.

## W05-GATE — Release Gate
**Status:** TODO  
**Depends On:** W05-T005, W01-T007  
**Blocks:** release  
**Required:** report correctness/reliability PASS; monitoring semantics PASS; login/download/priority-interface PASS; install/update PASS; no known P0/P1 blocker; **W01-T007 real-device field validation CLOSED (standalone S10500X + S10500X IRF + S12500 IRF)**.
