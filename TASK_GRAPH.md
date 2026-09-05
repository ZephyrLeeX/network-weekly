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
**Implementation:** `monitoring/scheduler.py` (epoch-aligned 5-minute boundaries; per-device in-flight registry — a poll outliving its cycle is never started twice; since W02-AUDIT the skipped planned cycle is bookkept as a FAILED poll run (`overlap`) without advancing the §9/§13 state machines; since W02-AUDIT-2 the registry holds only real poll futures — a poll spanning several cycles records one FAILED/overlap row per skipped cycle and is never started twice; loop always targets the next future boundary so restart never backfills §27.12; per-device executor threads contain failures §8); `monitoring/poll.py` (`poll_device`: Wave 1 `run_collection` re-used unchanged + topology sync + poll run + metrics in ONE transaction); `monitoring/credentials.py` (per-cycle device reload from DB + 0600 secrets; SNMP community required, SSH optional — SNMP-only devices simply have no §9.1 probe); worker runs heartbeat + scheduler threads on one stop event.
**Tests:** unit `tests/unit/test_monitoring_scheduler.py` (alignment, strictly-future cycles, no-overlap skip, restart no-backfill, per-device failure containment, loader-failure survival) + `tests/unit/test_monitoring_credentials.py`; integration `tests/integration/test_poll_device.py` (full cycle persisted end-to-end, FAILED run recorded with §9.1 probe result).

## W02-T003 — Utilization and counter rebaseline
**Status:** REVIEW_PASSED  
**Depends On:** W02-T001, W02-T002  
**Blocks:** W02-T006, W03-T002, W03-T005  
**Acceptance:** actual elapsed time used; invalid/reset counters rebaseline; ingress/egress utilization stored consistently; no false spikes.
**Implementation:** `monitoring/utilization.py` — `delta_octets * 8 / (actual elapsed s * effective speed_bps) * 100` per direction against the interface's latest stored sample; rebaseline (no value, baseline re-established) on: no previous sample, missing counters, negative delta (reset/unreliable wrap), speed missing/non-positive/CHANGED mid-interval, interval <= 0 or > 900 s (stale gap), computed > 100% (impossible). Wired into `pipeline.persist_poll_result` with a batched DISTINCT-ON baseline fetch.
**Tests:** unit `tests/unit/test_monitoring_utilization.py` (12 cases: normal delta vs actual elapsed, reset, per-direction independence, zero/negative/missing/changed speed, zero/negative interval, 900 s boundary, >100% guard); integration `test_utilization_chain_rebaseline_compute_reset` (baseline -> exact 80% delta -> counter reset; reset sample becomes new baseline, never a spike).

## W02-T004 — Device reachability state machine
**Status:** REVIEW_PASSED  
**Depends On:** W02-T002, W01-T005  
**Blocks:** W03-T003  
**Acceptance:** SNMP fail triggers one SSH confirmation; two failed cycles confirm Down; two reachable cycles confirm Recovery; SSH-only reachability does not convert missing SNMP samples into success.
**Implementation:** migration `0004` (`device_reachability_incidents` long-term per §9.4 + `device_monitoring_state` cycle tracking); `monitoring/reachability.py` — pure `advance_cycle_state` core + `apply_device_reachability` persistence. Failed cycle = SNMP channel yielded nothing AND the §9.1 SSH confirmation also failed/unavailable; 2 consecutive failed cycles → DOWN (`started_at` = first failed cycle); 2 consecutive reachable cycles → RECOVERED (`recovered_at` = first reachable cycle, SSH-only recovery included per §9.3); `last_cycle_started_at` continuity anchor — any skipped cycle resets both runs so gaps can never confirm a state. Wired into `poll_device` in the same transaction.
**Tests:** unit `tests/unit/test_monitoring_reachability.py` (9 transition cases incl. no-duplicate-incident, recovery interruption, gap resets); integration `tests/integration/test_reachability.py` (incident open/close persisted, second episode, SSH-only, gap, defaults).

## W02-T005 — Priority interface configuration service
**Status:** REVIEW_PASSED  
**Depends On:** W01-T004  
**Blocks:** W02-T006, W04-T005  
**Scope:** `monitored` flag and aggregation-aware persistence.  
**Acceptance:** aggregation interface can be monitored independently; member interfaces remain separately selectable; rediscovery preserves monitored state.
**Implementation:** `monitoring/interface_config.py` — `set_monitored` (single write path, never cascades to aggregation members per §11) and `interface_overview` (read model for the Wave 4 page: names, description, admin/oper, aggregation flag, member/member-of relationships, monitored). No schema change: the flag exists since migration 0002 and discovery never writes it (W01-T004), so rediscovery/ifIndex changes cannot lose the selection.
**Tests:** integration `tests/integration/test_interface_config.py` (aggregate select does not touch members; member independently selectable; toggle off; unknown id rejected; overview relationships; rediscovery with changed ifIndex preserves monitored; new interfaces default unmonitored).

## W02-T006 — Priority interface Down and high-utilization semantics
**Status:** REVIEW_PASSED  
**Depends On:** W02-T003, W02-T005  
**Blocks:** W03-T003, W03-T005  
**Acceptance:** two valid Down cycles confirm; two valid Up cycles recover; missing samples do not count; >=80% for three consecutive valid samples marks sustained high utilization.
**Implementation:** migration `0005` (`interface_state_incidents` long-term + `interface_monitoring_state`); `monitoring/interface_state.py` — only `monitored` interfaces participate; a sample is valid only when oper is exactly "up"/"down"; 2 consecutive valid Down samples → DOWN (started_at = first), 2 consecutive valid Up samples → RECOVERED; per §13.3 a missing/undetermined sample neither counts nor breaks the valid-sample run (interface reading of §13 — deliberately unlike the §14 continuity-breaking rule for thresholds); wired into `poll_device` after metric persistence. `monitoring/sustained.py` — pure sustained-high detection (>= threshold for N consecutive valid samples, missing breaks, default 3) + the §15.4 per-sample max(in, out) value; thresholds configurable in W02-T007.
**Tests:** unit `tests/unit/test_monitoring_interface_state.py` (transition cases + all RFC 2863 non-up/down states ignored + sustained-high runs incl. exactly-3, missing-breaks, two runs, custom N, max-direction); integration `tests/integration/test_interface_state.py` (incident open/close persisted, non-monitored ignored, undetermined not counted, full pipeline feeds the machine over two cycles).

## W02-T007 — CPU and memory sustained-high detection
**Status:** REVIEW_PASSED  
**Depends On:** W02-T002  
**Blocks:** W03-T002  
**Acceptance:** default >=80% for 15 minutes; thresholds configurable; missing samples break continuity.
**Implementation:** migration `0006` (`system_settings` KV table, §23); `monitoring/thresholds.py` — defaults CPU/memory/utilization 80% + 3 required samples (§14/§15.3), validated overrides (`set_threshold`/`load_thresholds`, corrupt rows fall back to default with a warning), series loaders over the planned 5-minute cycle grid via poll-run joins (`device_metric_series`, `interface_utilization_series`) so a cycle without a row is an explicit None point, and `detect_device_sustained_high` / `detect_interface_high_utilization` binding the W02-T006 pure detection core to the configured thresholds.
**Tests:** unit `tests/unit/test_monitoring_thresholds.py` (spec defaults, key/range validation incl. required-samples >= 2, cycle-slot alignment + half-open window); integration `tests/integration/test_sustained_high.py` (3-consecutive rule, missing cycle breaks continuity, single spikes rejected, missing memory stays None not 0, operator threshold/required-sample overrides change detection).

## W02-T008 — IRF periodic observation
**Status:** REVIEW_PASSED  
**Depends On:** W02-T001, W01-T005  
**Blocks:** W03-T004  
**Acceptance:** expected/current members persist; member missing/reappeared is detectable; role change stored when source data is reliable.
**Implementation:** migration `0007` (`irf_member_observations`, long-term per §24); `monitoring/irf.py` — `apply_irf_observation` persists one row per known member per SUCCESSFUL observation (present w/ reported role, absent as `observed=false`; role changes recorded only when both stored and reported roles are known; reappearance = previous flag false → present; member rows never deleted), `observe_irf_device` (Wave 1 `run_irf_observation` re-used; SSH failure records NOTHING so "missing" always means the device reported it), `IrfObservationLoop` (~15-min aligned single-thread passes, per-device containment), `build_irf_contexts` loader restricted to `expected_irf_member_count > 1` + SSH credentials — standalone devices are never SSH-probed for IRF (§17). Worker runs the loop as a third thread.
**Tests:** unit `tests/unit/test_monitoring_irf.py` (15-min interval, pass coverage/containment, aligned boundaries); integration `tests/integration/test_irf_observation.py` (present rows, missing-then-reappear, reliable role change w/ previous_role, unknown role never erases, loader skips standalone, loader requires SSH).

## W02-T009 — Ninety-day retention maintenance
**Status:** REVIEW_PASSED  
**Depends On:** W02-T001  
**Blocks:** W02-GATE  
**Acceptance:** batched cleanup removes expired raw metrics/poll runs without deleting long-term incident/report data.
**Implementation:** `monitoring/retention.py` — `run_retention` deletes interface metrics, device metrics then poll runs (children before runs, no big cascades) in configurable batches, each batch its own short transaction (§24); `max_batches_per_table` bounds a pass (backlog rate-limiting). Only the three raw tables are ever referenced — incidents, IRF observation history, report metadata/DOCX are long-term by construction. `NETWORK_REPORT_RETENTION_DAYS` (default 90) validated at load: values < 90 are refused (§24 "at least 90 days"). Worker runs the pass ~6 h after startup then every 6 h; a restart simply re-runs it (idempotent, needs no persistence).
**Tests:** integration `tests/integration/test_retention.py` (expired raw data deleted / recent kept, incidents+IRF+settings survive, batching bounds, below-90 rejected, interface metrics cleaned in own batches); unit `tests/unit/test_config.py` extension (default 90, override 180, 89/0/-30/non-numeric refused).

## W02-GATE — Monitoring Pipeline Gate
**Status:** PASS (engineering gate, 2026-09-05; revalidated PASS after W02-AUDIT-2)  
**Depends On:** W02-T004, W02-T006, W02-T007, W02-T008, W02-T009  
**Blocks:** Wave 3  
**Gate:** 5-minute monitoring data, incident semantics, IRF observations and retention behavior are trustworthy for weekly statistics.  
**Verification evidence (all on migrated PostgreSQL, 0001→0007):**
- pytest 205 unit + 72 integration PASS; ruff clean; mypy clean (77 files); alembic upgrade head idempotent at 0007.
- compose rebuild + smoke: web healthy (/health), postgres healthy, worker runs heartbeat + 5-min scheduler + 15-min IRF loop + retention threads; heartbeat persisted; scheduler started at the next boundary with NO backfill; missing dev secrets contained per cycle (§27.9), worker alive.
- `tests/integration/test_w02_gate_scenarios.py`: one device across 7 consecutive cycles — SUCCESS runs, utilization 80% over actual elapsed, counter-reset rebaseline (no spike), FAILED×2 → device DOWN (started_at = first failed cycle), reachable×2 → RECOVERED (recovered_at = first reachable cycle), sustained-high needs 3 consecutive valid samples, retention deletes old raw rows and spares the incident.
- Gate checklist: 5-minute scheduling ✓; no same-device overlap ✓; restart-continues-future ✓; SUCCESS/PARTIAL/FAILED persistence ✓; counter reset/rebaseline ✓; device Down/Recovery ✓; priority-interface Down/Recovery ✓; sustained CPU/memory/utilization ✓; IRF missing/reappearance/role change ✓; 90-day retention ✓.
- Real-device proof remains carried by W01-T007 (BLOCKED — FIELD_VALIDATION_PENDING), which blocks W05-GATE only; this gate does not affect it.

## W02-AUDIT — Wave 2 audit hotfix
**Status:** REVIEW_PASSED  
**Depends On:** W02-GATE  
**Blocks:** none (audit only; no product capability added, no schema change)  
**Scope:** §8 planned-cycle completeness; no-fake-SUCCESS collection semantics; whole-cycle poll idempotency; sustained-high interval duration semantics (§14/§15.3); control-doc/comment corrections.  
**Implementation:**
- §8 completeness: every planned cycle of every enabled device lands exactly one `device_poll_runs` row. A cycle that cannot even be attempted is bookkept FAILED with a marker in `failed_sections` — `overlap` (the device's previous poll still in flight; recorded by the scheduler through `record_skipped_poll`) or `credentials` (missing SNMP community per device, or the secrets file unreadable for the whole cycle — every enabled device then gets an unattemptable context). Never overlapped (§27.11), never backfilled (§27.12). Such rows are bookkeeping, not device evidence: the §9 reachability and §13 interface state machines never advance for them, and the §9 continuity anchor treats them as gaps.
- §8 no-fake-SUCCESS: `collect_cpu`/`collect_memory` raise a section error when no usable sample is collected (never SUCCESS with empty data); `collect_interfaces` returns the assembled samples plus any missing key column group (state / speed / octets / errors; HC-or-32bit fallback counts as present) as a DEGRADED section — the collected samples are kept, the poll run lands PARTIAL instead of a dressed-up SUCCESS.
- Whole-cycle idempotency: `poll_device` skips an already-persisted `(device_id, cycle_started_at)` before collecting, and `persist_poll_result` now reports `newly_persisted` as a race backstop — the §9/§13 state machines advance only when the call created the run row, so a replayed Down sample can no longer falsely confirm device or interface DOWN.
- §14/§15.3 duration semantics: a sustained-high interval is the half-open window `[first sample ts, last sample ts + one cycle slot)`; 3 consecutive 5-minute samples now report 15 minutes (`duration_seconds` = sample_count × slot on the planned grid) instead of 10. Wave 3 reads start/end/duration from exactly this implementation (`monitoring/sustained.py`, bound to thresholds in `monitoring/thresholds.py`).
- Corrected the `DevicePollRun` model docstring and the scheduler/poll/credentials docstrings that described the old "skipped cycle gets NO row" behavior contradicting §8.
**Tests:** unit — sustained interval end/duration boundaries (exactly 3 samples → 900 s, long run, custom slot, non-positive slot rejected), empty cpu/memory → section error, interfaces degraded groups + 32-bit fallback not degraded, unpollable context coverage, scheduler skip recording + recorder-failure containment, worker secrets-failure loader; integration — duplicate-cycle idempotency (reachability count stays 1 after replay, no false device/interface DOWN, no duplicate metric rows), missing-credential FAILED run without state advance, overlap-skip FAILED run idempotent, sustained interval 900 s over persisted series.
**Acceptance:** pytest 218 unit + 77 integration PASS on migrated PostgreSQL (0007); ruff clean; mypy clean (78 files); `alembic upgrade head` idempotent at 0007 (no migration needed); compose rebuild + smoke: web healthy, worker records the unattemptable cycle FAILED (`credentials`) per cycle and stays alive; W02-GATE revalidated PASS; Wave 3 not started.

## W02-AUDIT-2 — Wave 2 audit follow-up hotfix
**Status:** REVIEW_PASSED  
**Depends On:** W02-GATE  
**Blocks:** none (audit only; no product capability added, no schema change, no migration)  
**Scope:** two audit findings — scheduler overlap race (`_in_flight` polluted by skip-recording futures) and interfaces DEGRADED judged per column GROUP instead of per required field.  
**Implementation:**
- Scheduler overlap race: `_in_flight` now holds ONLY real poll futures. The W02-AUDIT skip bookkeeping stored the skip-recording future in the registry; that future completes almost instantly, so on the NEXT cycle the device looked free and a second real poll was started overlapping the first. Now the skip recording is submitted but never stored: a poll that keeps running across cycles records one FAILED/overlap row per skipped cycle (`record_skipped_poll`, unchanged contract), the registry keeps the original poll future until it finishes, and only its completion re-opens the device (§27.11 + §8). Unit test covers a poll spanning 2+ cycles (2 skip rows, zero second polls, registry identity held).
- Interfaces DEGRADED per field: the `_INTERFACE_KEY_GROUPS` check ("any column of the group delivered = complete") let e.g. `in_octets` be missing while out-direction/error columns masked the gap. Replaced by `_INTERFACE_KEY_FIELDS` judging each report-required field on its own columns: `oper_state`, `speed`, `in_octets`, `out_octets`, `in_errors`, `out_errors`, `in_discards`, `out_discards` — HC variant and 32-bit fallback count as the SAME field. Any required field with no data at all → DEGRADED section (poll run PARTIAL) while the collected samples still persist (§8). `ifAdminStatus` stays informational: §13 Down/Recovery reads `oper_state` only.
**Tests:** unit — scheduler poll-spanning-2+-cycles (per-cycle overlap rows, no second poll, registry identity); interfaces degraded on single missing column (`in_errors` alone), whole in-direction gone, speed+octets gone, oper_state gone; NOT degraded when only ifAdminStatus is missing or HC counters fall back to 32-bit per field; session-level DEGRADED interfaces → PARTIAL with samples kept and no SSH probe triggered.
**Acceptance:** pytest 223 unit + 77 integration PASS on migrated PostgreSQL (0007); ruff clean; mypy clean (78 files); `alembic upgrade head` idempotent at 0007 (no migration needed); compose rebuild + smoke: web healthy (/health database ok), worker heartbeat persisted, device-poll scheduler + IRF loop + retention alive with no errors; W02-GATE revalidated PASS; Wave 3 not started; W01-T007 still FIELD_VALIDATION_PENDING.

---

# Wave 3 — Weekly Statistics and DOCX

## W03-T001 — Report period and ISO week code
**Status:** REVIEW_PASSED  
**Depends On:** W02-GATE  
**Blocks:** W03-T002, W03-T003, W03-T004, W03-T005  
**Acceptance:** exact `[Monday 00:00, next Monday 00:00)` Asia/Shanghai periods including ISO year-boundary tests.  
**Implementation:** `reporting/period.py` — `ReportPeriod` (frozen, half-open `[start, end)`, tz-aware only), `week_containing` (local Monday 00:00 grid in the fixed Asia/Shanghai business timezone), `previous_period` (last complete week — the §4.1 report target), `period_for_iso_week` (ISO week-year construction; 53-week years and New-Year spans handled by the ISO calendar), `week_code` (`2026-W36`), `overlaps_interval` (cross-week incident membership with exclusive recovery), `report_file_name` (§5). No statistics here — pure period identity.  
**Tests:** `tests/unit/test_reporting_period.py` (15 cases: local-Monday bounds, half-open membership, UTC→local week mapping, Monday 00:00 ownership, previous-complete-week at Mon 00:10 and Sunday, ISO 2026-W01 New-Year boundary, 52/53-week years, week-code round trip, invalid ISO week rejection, naive-datetime rejection, cross-week overlap semantics, file name).

## W03-T002 — CPU/Memory/P95 and interface Top 10
**Status:** REVIEW_PASSED  
**Depends On:** W03-T001, W02-T003, W02-T007  
**Blocks:** W03-T006  
**Acceptance:** average/max/P95 correct; PostgreSQL `percentile_cont(0.95)` used; Top 10 algorithm matches `SYSTEM_SPEC.md` and golden tests.  
**Implementation:** `reporting/resources.py` — THE single weekly-statistics implementation for these values (§15.4); `metric_statistics` (avg/max/P95/sample_count per device+field over the planned-cycle window; P95 is PostgreSQL `percentile_cont(0.95).within_group`, never Python; no valid samples → None = 数据缺失, never 0), `device_resource_statistics` (CPU+memory for one logical device, §14), `interface_top_entries` (§15.4: candidates have valid weekly utilization samples; per-sample value `max(in, out)` via a SQL expression mirrored 1:1 from `monitoring.sustained.max_direction_utilization`; per-interface P95 of that value ranks descending with deterministic tie-break device→interface name; `limit` for Top 10). Sustained-high intervals stay bound to the W02-T007 implementation — not reimplemented here.  
**Tests:** `tests/integration/test_weekly_resources.py` (8 cases: percentile_cont linear-interpolation P95 pinned on 20 known samples; missing cycles + NULL fields excluded, not zeroed; period-boundary exclusion; whole-week-no-data → None = 数据缺失; unknown-field rejection; Top 10 ordering by unified-sample P95 incl. NULL-direction handling and SQL↔Python max-direction equivalence; both-directions-NULL excluded; limit + deterministic tie-break).

## W03-T003 — Weekly device and priority-interface incident summary
**Status:** REVIEW_PASSED  
**Depends On:** W03-T001, W02-T004, W02-T006  
**Blocks:** W03-T006  
**Acceptance:** weekly Down/Recovery/ongoing presentation handles cross-week intervals correctly.  
**Implementation:** `reporting/incidents.py` — reads ONLY the long-term §9.4/§13 incident records (§24). Membership: an episode belongs to the week when `[started_at, recovered_at)` intersects the half-open period (recovery exactly at `start` = previous week; start exactly at `end` = next week). `IncidentEpisode` carries `started_at`, `recovered_at` (None = open), full observed duration (cross-week episodes report their whole length), `started_before_period` (跨周), `recovered_after_period_end` and `ongoing_at_period_end` (None recovery or recovery >= period.end) — the §19 异常/关注 evidence. `weekly_device_incidents` groups per logical device; `weekly_interface_incidents` per monitored interface with device/name/description context. Devices/interfaces with no in-week incident are omitted (absence of rows = no incidents, not missing data).  
**Tests:** `tests/integration/test_weekly_incidents.py` (7 cases: in-week down+recovery with duration; cross-week started-last-week recovered-in-week; open-at-period-end and recovered-after-period-end both ongoing; boundary exclusions; multi-episode grouping/ordering; interface incidents incl. ongoing cross-week outage and incident-free interfaces absent; empty week).

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
