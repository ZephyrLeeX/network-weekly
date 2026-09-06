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
**Status:** REVIEW_PASSED  
**Depends On:** W03-T001, W02-T008  
**Blocks:** W03-T006  
**Acceptance:** member missing/reappeared/current missing are summarized correctly even when logical device remained reachable.  
**Implementation:** `reporting/irf_summary.py` — reads the long-term `irf_member_observations` history (a failed observation records nothing, so any in-week row is device-reported evidence; member absence is visible even when the management IP stayed reachable, §17). Per fabric (`expected_irf_member_count > 1` only): expected count, `observed_member_count` from the latest in-week observation, per-member chronological fold into missing windows `[first missing obs, reappearing obs)` (open when still missing through the in-week data), `missing_at_period_end` from the LATEST in-week observation only (post-period reappearance belongs to the next week), reliable role changes (both roles known, W02-T008 semantics), and `data_missing` for a week with no successful observation — evidence absence is 数据缺失 and never read as member loss (§6.2/§19).  
**Tests:** `tests/integration/test_weekly_irf.py` (8 cases: all-present week; missing→reappearance window with exact bounds; still-missing-at-period-end with open window; observations outside the period ignored incl. pre-week missing and post-week reappearing; in-week reliable role change; observation-less week = 数据缺失 not member loss; standalone devices excluded; multi-fabric ordering).

## W03-T005 — CRC/Error/Drop deltas and Monitoring Coverage
**Status:** REVIEW_PASSED  
**Depends On:** W03-T001, W02-T001, W02-T003, W02-T006  
**Blocks:** W03-T006  
**Acceptance:** reset-safe weekly deltas; Top observations; expected/success/partial/failed correct; Coverage formula correct; <95% produces data-integrity warning.  
**Implementation:** `reporting/counters_coverage.py` — THE single implementation for both statistics. Counter deltas (§16): one CTE with `lag()` per counter column (fcs_errors=CRC, in/out errors, in/out discards) over the planned-cycle window, aggregated with a forward-only `sum(case(...))` — a reset / negative delta / missing side contributes nothing (rebaseline), so fake huge increments are impossible; a column with no valid interval is NULL = 数据缺失, never 0; `total_delta` sums the columns that have data and is None when none do; ranking total-descending with deterministic tie-break (observation-only, §19: never changes overall status). Coverage (§18): `expected` = planned-cycle grid via the W02-T007 `cycle_slots` (2016 for a full week; disabled device → 0 planned cycles → coverage None, not 0%); counts from one grouped status query; any non-SUCCESS/PARTIAL status counts as failed; `coverage=(SUCCESS+PARTIAL)/expected*100`; PARTIAL always separate; `below_target` = overall OR any single device < 95% → 数据完整性不足.  
**Tests:** `tests/integration/test_weekly_counters_coverage.py` (10 cases: simple accumulation; counter reset mid-week keeps only real increments; never-reported column None-not-zero while constant counter is a genuine 0; no-counter-data interface unranked; Top ranking by total incl. multi-column sums; §18 status counts over a full 2016-cycle week with out-of-week exclusion; single-device <95% trips warning while 99% device does not; exactly-at-target not below; silent week 0% coverage; disabled device no planned cycles).

## W03-T006 — WeeklyStatisticsService and overall status
**Status:** REVIEW_PASSED  
**Depends On:** W03-T002, W03-T003, W03-T004, W03-T005  
**Blocks:** W03-T007  
**Acceptance:** one service produces all report-ready values; Normal/Attention/Abnormal rules deterministic; missing data remains explicit.  
**Implementation:** `reporting/service.py` — `build_weekly_report_data(session, period)` composes ONE `WeeklyReportData` from the W03-T002..T005 implementations only (no recomputation): per-device CPU/memory stats, sustained-high via the W02-T007 `detect_*` functions, interface Top 10, device/interface incident summaries, IRF summaries, counter Top 10, Coverage, plus priority-interface sustained high-utilization entries (`detect_interface_high_utilization`) and the overall status + deterministic Chinese summary text. §19 rules fixed and ordered: 异常 = ongoing device/interface Down or IRF member missing at period end; else 关注 = recovered device/interface Down, IRF missing window in week, CPU/memory sustained high, priority-interface high utilization, Coverage <95% (device or overall); else 正常. CRC/Error/Drop values never enter the rules (§19). Missing data stays None (§6.2); summary template is deterministic (byte-identical on rebuild).  
**Tests:** `tests/integration/test_weekly_service.py` (11 cases: empty week = 关注 + 数据完整性不足; full healthy week = 正常; open device incident 异常; recovery after period end 异常; recovered incident 关注; open interface incident 异常; CPU sustained high 关注; interface high utilization 关注 with interval pinned; 30M CRC delta does NOT change status; 异常-over-关注 precedence; summary text contains week code/status/coverage and is byte-deterministic).

## W03-T007 — Weekly statistics golden tests
**Status:** REVIEW_PASSED  
**Depends On:** W03-T006  
**Blocks:** W03-T008  
**Acceptance:** period, cross-week, missing sample, threshold continuity, P95, Top 10, reset, PARTIAL and Coverage cases pass.  
**Implementation:** service-side addition completing §14 for the report: `WeeklyReportData.device_resources` now carries the per-device sustained-high intervals (start/end/duration from the shared W02 implementation) next to avg/max/P95 — `DeviceResourceReport`.  
**Tests:** `tests/integration/test_w03_golden_scenarios.py` — 12 golden scenarios, one per IMPLEMENTATION_PLAN case: `[Mon 00:00, next Mon 00:00)` half-open boundary (start slot in, end cycle out, 2016-slot grid); ISO year boundary week 2026-W01 spanning 2025-12-29..2026-01-05; 跨周设备 Down (started previous week, recovered in week, full duration, ongoing flags); 周内 Down+Recovery; 重点接口 Down+Recovery; missing sample breaking sustained-high continuity (85,85,MISSING,85,85,85 → one trailing interval, 900 s); CPU/Memory P95 pinned on percentile_cont interpolation (19.05); interface Top 10 algorithm (per-sample max-direction, per-interface P95, descending); CRC counter reset mid-week (550 real increments, never a fake spike); PARTIAL poll counted in coverage AND shown separately, its kept CPU sample present and the lost cycle missing-not-zero; Coverage <95% → 数据完整性不足 while the report still builds; 整周设备无数据 → 数据缺失 everywhere, coverage 0%, 关注; threshold override flowing into report sustained-high counts.

## W03-T008 — DOCX renderer
**Status:** REVIEW_PASSED  
**Depends On:** W03-T007  
**Blocks:** W03-T009  
**Scope:** `python-docx`; eight fixed sections; table-first layout; temporary file + atomic replacement.  
**Acceptance:** readable DOCX; missing data explicit; blank editable “本周处理问题” area; no secrets.  
**Implementation:** `reporting/docx.py` — `render_report_docx(data, output_dir)` builds the §6 report: title + 8 fixed Heading-1 sections (`SECTION_TITLES`, order pinned): ① basic-info table (week code, period start/end Asia/Shanghai, generated time, overall status, Coverage summary incl. PARTIAL count + 数据完整性不足 marker) + deterministic summary paragraph; ② device Down episodes (start/recovered/duration/ongoing); ③ IRF per fabric (expected/current, per-member missing windows with 期末仍未恢复, roles, reliable role changes, 数据缺失 when no successful observation); ④ CPU/memory avg/max/P95 + interval count per device plus per-interval start/end/duration table; ⑤ monitored-interface Down episodes; ⑥ Top 10 (rank/device/interface/P95/max) + high-utilization intervals; ⑦ CRC/Error/Drop Top 10 (观察信息,不改变总体状态) + per-device & overall Coverage table; ⑧ clear blank editable area (bordered empty 1×1 table + hint paragraph). §6.2: every missing value renders 数据缺失 (never 0; interval counts 0 are genuine counts). §5 write flow: `mkstemp` in the target directory → `document.save` → validation (reopen + exact 8-heading check, `DocxRenderError` on mismatch) → `os.replace` atomic same-filesystem rename; failed renders leave no artifacts and never touch the previous file. Normal style sets eastAsia font so CJK renders predictably.  
**Tests:** `tests/unit/test_reporting_docx.py` (10 cases: exactly-8-sections; §6.1 required fields incl. UTC→+08 display and coverage summary; missing → 数据缺失 with genuine zero interval counts; incident/counter tables incl. 未恢复-duration and missing-vs-zero columns; IRF missing windows + role rendering + open questions blank area; Top10 rank rows; same-week regenerate leaves exactly one file and no temp leftovers; render failure leaves zero artifacts; validator rejects wrong headings; duration formatting) + `tests/integration/test_weekly_docx_end_to_end.py` (persisted data → `build_weekly_report_data` → DOCX: 8 sections, 数据缺失 explicit, 仍处于 Down visible, 数据完整性不足, blank editable area, no secret-bearing markers).

## W03-T009 — Persistent report jobs, Monday schedule and retry
**Status:** REVIEW_PASSED  
**Depends On:** W03-T008  
**Blocks:** W03-T010  
**Acceptance:** Monday 00:10 job; 10-minute retry; worker restart recovery; maximum one active job per week; status/error persisted.  
**Implementation:** migration `0008` — `report_jobs` (lifecycle pending→running→succeeded|failed; trigger scheduled|manual; attempts; sanitized `last_error`; `next_retry_at`; started/finished/updated timestamps) with the partial UNIQUE index `uq_report_jobs_active_week` over `week_code WHERE status IN ('pending','running','failed')` making §4.3 "one active job per week" a DB guarantee while a succeeded week can still receive a fresh manual job (§4.4); `weekly_reports` (§24 long-term per-week registry: unique week_code, status success/failed, `file_path`, `generated_at`, `last_error`). ORM models + `__table_args__` mirror the DDL. `reporting/jobs.py` — `ensure_scheduled_job` (idempotent; skips weeks already successful; `ON CONFLICT DO NOTHING` on the partial index as the race backstop), `request_regenerate` (idempotent, refuses incomplete weeks), `recover_stale_running_jobs` (boot-time reset running→pending, §4.2/§27.1), `execute_report_job` (marks running; rebuilds the period in Asia/Shanghai and cross-checks the week code; builds statistics from persisted data only (§27.10); renders via the atomic §5 flow; on failure persists a sanitized readable summary + `next_retry_at = now + 10 min` (§4.3) and records the failure on `weekly_reports` WITHOUT ever overwriting a success row — the old DOCX stays current (§4.4)), `due_jobs` (retry-time gate). `reporting/schedule.py` — `WeeklyReportLoop` (worker thread, 60 s pass): recover-once, ensure the latest complete week's job once `now >= period_end + 10 min` (= Monday 00:10, §4.1; a worker down at 00:10 creates the job on a later pass — persistent responsibility, not an in-memory timer), then run due jobs one at a time (never two concurrent generations). `worker.py` runs the loop as a fourth thread.  
**Tests:** `tests/integration/test_report_jobs.py` (13 cases: schedule idempotency + one row; skip week with success report; incomplete-week rejection; success path persists report row + readable 8-section DOCX; failure path persists last_error + exactly `now+10 min` retry + failed registry row, not runnable before retry time, retry succeeds; regenerate failure keeps previous success row and byte-identical old DOCX; DB-level one-active-per-week incl. succeeded-week re-entry; regenerate incomplete-week rejection; stale running recovery then successful completion; full loop — nothing before 00:10, create+execute after 00:12, restart creates no duplicate; loop retry gating before/after retry time).

## W03-T010 — Manual regenerate service and real report verification
**Status:** BLOCKED — REAL_WEEK_DATA_PENDING（regenerate 服务实现 REVIEW_PASSED；真实周报人工核对验收在本环境无法满足，按业主指令不得伪造验收。Owner Decision 2026-09-06：该项不再阻塞 Wave 4 / W03-GATE，改为阻塞 W05-GATE — Release Gate）  
**Depends On:** W03-T009  
**Blocks:** W05-GATE（真实周报人工核对 acceptance；regenerate 服务实现已 REVIEW_PASSED，不再阻塞 W04-T004）  
**Acceptance:** regenerate replaces one current server DOCX for the week; real weekly report manually checked against source samples.  
**Implementation:** `request_regenerate` (W03-T009/T010, `reporting/jobs.py`) is the manual-regenerate entry: idempotent (an active job for the week is returned unchanged — repeated Web submissions later cannot duplicate it), refuses incomplete weeks, creates `trigger='manual'` jobs; execution goes through the same `execute_report_job` path, so the previous successful DOCX stays current/downloadable until the new attempt atomically replaces it (§4.4/§5), and a failed attempt records status + readable error without touching the old file. Wave 4's form (W04-T004) wires directly onto this service.  
**Tests:** `tests/integration/test_manual_regenerate.py` (3 cases: successful regenerate replaces the single current DOCX — same name, new bytes, exactly one file, registry updated; rapid double regenerate yields ONE job; failed regenerate leaves visible status/error and remains retryable).  
**BLOCKED item (release-blocking evidence, not engineering-blocking):** 真实周报人工核对需要真实设备一周持久化数据。本环境无可达真实 H3C 设备（W01-T007 FIELD_VALIDATION_PENDING，业主 2026-09-04 授权），dev 库核实 0 devices / 0 poll_runs / 0 metrics——不存在真实周数据。该项必须在真实环境完成连续一周采集后：人工将 DOCX 与源采样核对（period correct / values plausible / tables readable / 8 sections / missing data explicit / no secrets / 本周处理问题可编辑）。

## W03-GATE — Weekly Report Gate
**Status:** PASS — engineering gate（Owner Decision 2026-09-06：工程侧验证全部通过 W03-AUDIT-4 复验，允许继续 Wave 4；真实周数据人工 DOCX 核对 deferred to W05-GATE，由 W03-T010 携带，不得在本 gate 伪造）  
**Depends On:** W03-T009, W03-T010（engineering scope — regenerate 服务实现；其真实数据 acceptance 单独携带至 W05-GATE）  
**Blocks:** Wave 4  
**Gate:** trustworthy DOCX can be generated from persisted weekly data and survives failure/restart scenarios — engineering acceptance on tests + golden scenarios + failure/restart integration proofs.  
**Engineering verification evidence (work/wave-03, migrated PostgreSQL 0001→0008):** pytest 全绿（252 unit + 170 integration PASS）；ruff clean；mypy clean（100 files）；`alembic upgrade head` 幂等至 0008；compose smoke（web /health healthy、worker heartbeat + report loop、scheduler/IRF/retention 正常）。golden scenarios 覆盖 IMPLEMENTATION_PLAN 全部 golden cases；failure/restart 场景（生成失败 10 分钟重试、worker 重启恢复、同周仅一个活跃任务、regenerate 期间旧 DOCX 保留、原子替换、candidate job-binding + reconcile）全部由集成测试证明。W03-AUDIT..AUDIT-4 四轮审计修复全部 REVIEW_PASSED 并复验全绿。  
**Deferred to W05-GATE (NOT part of this gate):** 一份来自真实设备持久化周数据的 DOCX 人工核对（依赖真实环境采集；与 W01-T007 同源）。真实数据到位后在 Release Gate 完成核对，才可宣称 W03-T010 acceptance 关闭。

## W03-AUDIT — Wave 3 audit hotfix
**Status:** REVIEW_PASSED  
**Depends On:** W03-GATE  
**Blocks:** none (audit only; no product capability added, no schema change, no migration)  
**Scope:** four audit findings — stranded `running` report jobs; a healthy-looking empty deployment; counter Top 10 admitting no-data interfaces; IRF no-observation weeks summarized as 无缺失.  
**Implementation:**
- Report job running 卡死 (`reporting/schedule.py`, `reporting/jobs.py`): `WeeklyReportLoop` recovers stranded `running` jobs at the start of EVERY pass instead of once at loop start — a failed recovery (database briefly down) is retried by the following passes (§4.2/§27.9); generations run inline on the loop thread, so a `running` row at pass start is always a leftover. `execute_report_job`'s failure recording now has a fallback (`_record_failure`): when even the failure update cannot be persisted (database outage after a lost success update included), the job is requeued as `pending` with `next_retry_at = now + 10 min` (§4.3) — and if even that write fails, the next pass's recovery collects the stranded `running` row. A job can therefore never be stuck in `running` without a worker restart, and the 10-minute retry cadence survives database outages.
- Truly 0-device empty database (`reporting/counters_coverage.py`, `reporting/service.py`, `reporting/docx.py`): `CoverageSummary.below_target` is True for a deployment with NO devices (no coverage evidence can claim integrity; §6.2/§18.3), so the overall status is 关注 — never 正常. The summary states `未配置设备/数据缺失；数据完整性不足` and never `数据完整性满足要求`; the DOCX basic-info row shows `未配置设备/数据缺失（设备 0 台，无 Monitoring Coverage 数据）`, and the §18 table's overall row keeps 覆盖率 = 数据缺失 (None), not 0%. Devices with planned cycles (incl. disabled-device semantics) are unchanged.
- Counter Top 10 (`reporting/counters_coverage.py`): `counter_delta_top_entries` drops interfaces whose `total_delta` is None — no valid counter interval means no rankable observation (数据缺失 is not a value, §6.2) — so they can no longer fill Top-10 slots; a genuine total of 0 (constant counters) stays eligible.
- IRF 数据缺失摘要 (`reporting/service.py`): a fabric with no successful in-week observation yields the summary clause `IRF 成员状态数据缺失（<devices>）` instead of `IRF 成员无缺失` (§6.2/§17); mixed weeks list both 期末缺失 and 数据缺失 clauses; missing IRF evidence is still not an §19 异常 condition — the overall status keeps following §19 + Coverage.
**Tests:** `tests/integration/test_report_jobs.py` (+4: lost success update becomes a recorded failure with intact retry time; both terminal updates lost → requeued pending and completed by the same worker on a later pass; requeue lost → job stays `running` and is recovered by a later pass without restart; recovery failure → retried by the next pass); `tests/integration/test_weekly_service.py` (+2: 0-device database = 关注 + 未配置设备/数据缺失 + no 满足要求; IRF data-missing summary explicit, not 无缺失, not 异常); `tests/integration/test_weekly_counters_coverage.py` (corrected the None-total test that wrongly accepted it into the Top 10; +2: true-zero eligible / None-total excluded; fewer-than-10 rankable interfaces leave the rest unfilled); `tests/unit/test_reporting_docx.py` (+2: 0-device basic-info row; IRF data-missing fabric rendering); `tests/integration/test_weekly_docx_end_to_end.py` (+2: 0-device DOCX end-to-end; IRF data-missing DOCX end-to-end).  
**Acceptance:** pytest 250 unit + 160 integration PASS on migrated PostgreSQL (0001→0008); ruff clean; mypy clean (99 files); `alembic upgrade head` idempotent at 0008 (no migration needed); image rebuilt + compose smoke: web healthy (/health database ok), worker heartbeat persisted and fresh, weekly-report loop + retention alive; live manual regenerate of 2026-W35 through the §4.4 service in the worker container produced the atomic 8-section DOCX with the empty deployment honestly rendered (当前总体状态 关注, 未配置设备/数据缺失, 无 数据完整性满足要求). W03-T010 / W03-GATE stay BLOCKED — REAL_WEEK_DATA_PENDING; Wave 4 not started; W01-T007 still FIELD_VALIDATION_PENDING. Checkpoint: 936b1583f46376cf043d28286910952e711a49d7 (work/wave-03).

## W03-AUDIT-2 — Wave 3 audit follow-up hotfix
**Status:** REVIEW_PASSED  
**Depends On:** W03-GATE  
**Blocks:** none (audit only; no product capability added, no schema change, no migration)  
**Scope:** one audit finding — a regenerate could replace the current DOCX before its database success committed, so a DB failure left the registry describing report A while the disk already held report B.  
**Implementation:**
- Regenerate consistency (`reporting/docx.py`, `reporting/jobs.py`, `reporting/schedule.py`): rendering and installing are now two explicit steps. `render_report_candidate` renders + validates (temp file → save → reopen + 8-heading check, §5) a candidate DOCX under the deterministic side name `network-weekly-report-<week>.docx.candidate` in the same directory — the current report of the week is never touched by rendering. `execute_report_job` commits the `weekly_reports` success (file_path = the current-report path) and the `report_jobs` terminal success FIRST, and only then `install_report` atomically switches the candidate onto the current path — so any database failure happens while the previous successful DOCX bytes are still intact (§4.4/§5). A success-update failure discards the candidate (no residue), except when the row already carries this attempt's `generated_at` — the commit landed but its confirmation was lost to the same outage — in which case the candidate is the only copy of the succeeded report and is installed; when the database is still unreachable the candidate is left for reconciliation. A switch failure after the commit leaves the success terminal and the candidate survives: `reconcile_report_files` runs at EVERY `WeeklyReportLoop` pass (before scheduling) and completes the switch from the DB row — or discards candidates without a success row. First generation with no old report, the 10-minute retry cadence, one current DOCX per week, atomic replace and one-active-job-per-week are unchanged.
**Tests:** `tests/integration/test_report_file_consistency.py` (+6): the required end-to-end scenario (A success → regenerate → candidate B renders → simulated success-update DB failure → job FAILED, `weekly_reports` still A, current DOCX bytes == A, no candidate/temp residue → retry → bytes become B only after the retry succeeds); lost success-reply commit still installs its committed report; interrupted switch completed by the next loop pass with no duplicate scheduling; first-generation DB failure leaves no files and the retry creates the first DOCX; render failure keeps the old report without a candidate; reconcile completes success rows, discards abandoned candidates, ignores foreign `*.candidate` files. `tests/unit/test_reporting_docx.py` (+2): candidate→install two-phase layout; `candidate_week_code` round trip.  
**Acceptance:** pytest 252 unit + 166 integration PASS on migrated PostgreSQL (0001→0008); ruff clean; mypy clean (100 files); `alembic upgrade head` idempotent at 0008 (no migration needed); image rebuilt + compose smoke: web healthy (/health database ok), worker heartbeat persisted and fresh, IRF + weekly-report loops alive with zero errors; live manual regenerate of 2026-W35 through the §4.4 service inside the worker container on the new candidate flow: succeeded, 8 validated sections, exactly one current DOCX in the reports volume (no candidate/temp residue), registry row success pointing at it, empty deployment still rendered honestly (当前总体状态 关注, 未配置设备/数据缺失, 数据完整性不足, no 数据完整性满足要求). W03-T010 / W03-GATE stay BLOCKED — REAL_WEEK_DATA_PENDING; Wave 4 not started; W01-T007 still FIELD_VALIDATION_PENDING. Checkpoint: 5bdc22e138950940146de87d84c65184bacaa75c (work/wave-03).

## W03-AUDIT-3 — Wave 3 audit follow-up hotfix
**Status:** REVIEW_PASSED  
**Depends On:** W03-GATE  
**Blocks:** none (audit only; no product capability added, no schema change, no migration)  
**Scope:** one audit finding — candidates were identified only by week (`network-weekly-report-<week>.docx.candidate`), so reconciliation could install a failed regenerate's candidate against a previous success of the same week; a lost success reply degraded a committed success back to FAILED + retry; and an install failure after the DB success was reported as a full, unqualified success.  
**Implementation:**
- Job-bound candidates (`reporting/docx.py`): a candidate's name now carries its generating `report_jobs` id — `network-weekly-report-<week>.docx.candidate.<job_id>` (`render_report_candidate`/`render_report_docx` take a required `job_id`; `RenderedReport` carries it; `parse_candidate_name` returns `(week_code, job_id)`; `is_unbound_candidate_name` recognizes the legacy no-binding layout for discard-only handling). Two attempts of one week can therefore never collide or impersonate each other.
- Strict reconciliation authorization (`reporting/jobs.py`): `reconcile_report_files` installs a candidate only when the bound `ReportJob` exists, `job.week_code` equals the candidate's week, `job.status == succeeded`, and the week's `weekly_reports` row is the current success with `generated_at == job.finished_at` — the exact `(job, row)` pair `_mark_success` writes in one transaction. An older success A can never authorize a failed/running regeneration B's candidate; a candidate superseded by a newer success is discarded, never installed over it; unbound legacy and orphaned (job row gone) candidates are discarded; foreign `*.candidate` files are ignored. The succeeded job's install-pending `last_error` is cleared when its candidate is resolved (installed or superseded).
- Lost success reply (`reporting/jobs.py`): after a `_mark_success` failure, `_success_commit_state` classifies the attempt as committed (job succeeded with `finished_at == now` AND the success row `generated_at == now`), verifiably absent, or unknown (database still unreachable). "Committed" resolves the attempt to SUCCEEDED — the job's own candidate is installed, the job stays `succeeded` with `next_retry_at = NULL`, `due_jobs` has nothing to retry; never a FAILED downgrade. "Absent" discards the candidate and records the failed attempt. "Unknown" keeps the job-bound candidate — reconciliation later installs it if the commit landed, or discards it and re-queues the recovered job for a normal retry.
- Diagnosable install-pending (`reporting/jobs.py`): an `os.replace` failure after the committed success keeps the success terminal and the candidate on disk, records the owed switch on the existing `report_jobs.last_error` field ("success committed but the current DOCX is not switched yet (install pending; reconciliation will retry)" + candidate name + OSError type), and returns the distinct outcome status `succeeded_install_pending` — the un-switched file is never reported as a full success. The next pass's reconciliation completes the switch and clears `last_error`.
**Tests:** `tests/integration/test_report_file_consistency.py` (+2 new, 4 rewritten for job binding): the required outage scenario (A success → regenerate B → B's candidate rendered → `_mark_success` uncommitted AND DB still unreachable → candidate kept, job stuck `running` → next pass with DB recovered → reconciliation does NOT install B, current bytes still A, registry still A, job requeued `pending` → B's retry succeeds and only then the bytes become B); lost success reply → outcome SUCCEEDED, file installed, job stays `succeeded`, `next_retry_at` NULL, `due_jobs` empty, a later regenerate still clean; install `OSError` after the DB success → outcome `succeeded_install_pending`, current still A, candidate kept, `last_error` diagnosable, nothing due → next pass reconcile installs B and clears `last_error`; a stale candidate of a superseded success is discarded and the newer success stays current; the strict per-job reconcile matrix (authorized install / superseded / never-committed / orphaned job id / unbound legacy / foreign file); the retained AUDIT-2 scenarios (success-update failure keeps old bytes until the retry; first-generation DB failure leaves no files; render failure leaves no candidate). `tests/unit/test_reporting_docx.py`: two-phase candidate + name-parsing tests updated/extended for job-bound names; render fakes in `test_report_jobs.py`/`test_manual_regenerate.py`/end-to-end DOCX tests updated for the `job_id` parameter.  
**Acceptance:** pytest 252 unit + 168 integration PASS on migrated PostgreSQL (0001→0008); ruff clean; mypy clean (100 files); `alembic upgrade head` idempotent at 0008 (no migration — the existing `report_jobs.last_error` field carries the new state); image rebuilt + compose smoke: web healthy (/health database ok), worker heartbeat persisted and fresh (24 s), poll/IRF/weekly-report loops alive; live manual regenerate of 2026-W35 inside the worker container on the job-bound candidate flow: succeeded, 8 validated sections, exactly one current DOCX in the reports volume (no candidate/temp residue), registry row success pointing at it, job row `succeeded` with `last_error` NULL. W03-T010 / W03-GATE stay BLOCKED — REAL_WEEK_DATA_PENDING; Wave 4 not started; W01-T007 still FIELD_VALIDATION_PENDING. Checkpoint: e5a9396b9b357b2106157401b0c91e935158b850 (work/wave-03).

## W03-AUDIT-4 — Wave 3 audit follow-up hotfix
**Status:** REVIEW_PASSED  
**Depends On:** W03-GATE  
**Blocks:** none (audit only; no product capability added, no schema change, no migration)  
**Scope:** two audit findings — (1) a landed success commit could still be degraded to FAILED + 10-minute retry when its success-state query hit a database blip and the failure recorder ran against the recovered database; (2) an install-pending `last_error` could survive forever when its candidate's `os.replace` succeeded but the marker-clearing commit was lost — no candidate left for candidate-based reconciliation to resolve.  
**Implementation:**
- Strict commit-state machine (`reporting/jobs.py`): after a failed success update, `_success_commit_state` classifies the attempt and every branch now resolves independently: `committed` → install the candidate → SUCCEEDED / INSTALL_PENDING; `absent` → discard the candidate → FAILED + 10-min retry (§4.3); `unknown` (database unreachable for the verification query) → keep the job-bound candidate, record NOTHING (no FAILED), schedule NOTHING (no retry), leave the row in its current state — the next pass's recovery (a stranded `running` row re-enters the retry) and reconciliation (a landed success gets its candidate installed) arbitrate (§4.4, W03-AUDIT-4). The unresolved attempt is reported as the distinct outcome status `commit_unknown` — never an ordinary FAILED, never an unqualified SUCCEEDED. `_mark_failure` additionally refuses to downgrade a `succeeded` job (logs and returns untouched) — a terminal success can never lose to a late failure record even if call sites change.
- Install-pending residue sweep (`reporting/jobs.py`): `reconcile_report_files` (run at every pass) now also resolves stale markers strictly from the database and the directory: a `succeeded` job carrying the install-pending `last_error` is cleared when the week's success row is exactly this job's commit (`generated_at == finished_at`) AND its recorded current DOCX exists on disk AND no candidate bound to the job remains — the switch demonstrably completed; or when a strictly newer success of the week superseded the job — nothing can be owed anymore. A missing current DOCX keeps the marker: there the diagnosis is still true. No schema change (the existing `last_error` field carries the state).
**Tests:** `tests/integration/test_report_file_consistency.py` (+2 new, 1 updated): the required unknown-state scenario (A success → regenerate B → B's success commit LANDS with a lost reply → exactly the commit-state query hits a windowed database outage (commit state unknown) → the failure recorder's database is already recovered → job still `succeeded` with `finished_at` intact, `next_retry_at` NULL, no FAILED `last_error`, not in `due_jobs`, candidate kept → a direct `_mark_failure` call is refused by the guard → next pass reconcile installs B, current bytes change, registry still B's commit, no duplicate job); the required residue scenario (install `OSError` → diagnosable install-pending → reconcile completes the switch but its marker-clearing commit is lost → current bytes are B with no candidate while the row still says install pending → next healthy pass clears the stale marker, bytes/registry untouched, nothing scheduled); the outage-kept-candidate test now pins the unresolved `commit_unknown` outcome with nothing recorded and nothing scheduled (the outage factory gained a bounded blip window for the state query).  
**Acceptance:** pytest 252 unit + 170 integration PASS on migrated PostgreSQL (0001→0008); ruff clean; mypy clean (100 files); `alembic upgrade head` idempotent at 0008 (no migration); image rebuilt (docker build; compose build is a silent no-op in this environment) + compose smoke: web healthy (/health database ok), worker heartbeat persisted and fresh (11 s), poll/IRF/weekly-report loops alive with zero errors; live manual regenerate of 2026-W35 through the §4.4 service inside the worker container on the new code: succeeded, 8 validated sections, exactly one current DOCX in the reports volume (no candidate/temp residue), registry row success pointing at it, job row `succeeded` with `last_error` NULL, reconcile pass clean, empty deployment still rendered honestly (当前总体状态 关注, 未配置设备/数据缺失, 数据完整性不足). W03-T010 / W03-GATE stay BLOCKED — REAL_WEEK_DATA_PENDING; Wave 4 not started; W01-T007 still FIELD_VALIDATION_PENDING. Checkpoint: 760bdd61ee328049f22fe9677aeecfedfd3097e1 (work/wave-03).

---

# Wave 4 — Login and Operations Web

## W04-T001 — Single administrator and password storage
**Status:** REVIEW_PASSED  
**Depends On:** W03-GATE（PASS — engineering gate; READY per Owner Decision 2026-09-06）  
**Blocks:** W04-T002  
**Acceptance:** one local administrator can be initialized; salted scrypt hash stored; plaintext password never persisted.  
**Implementation:** migration `0009` — `users` (username unique, `password_hash`, singleton, TIMESTAMPTZ audit columns) with the UNIQUE index `uq_users_single_admin` on the always-true `singleton` flag making §21 "系统只有一个本地管理员账号" a database guarantee. `auth/passwords.py` — self-describing salted scrypt hashes `scrypt$N$r$p$<salt hex>$<digest hex>` (stdlib `hashlib.scrypt`, n=2¹⁴/r=8/p=1, 16-byte `secrets` salt per password, `hmac.compare_digest` verification; malformed stored hashes verify False, never raise). `auth/admin.py` — `initialize_admin` (create-or-reinitialize, idempotent for install flows; validation rejects empty credentials before any DB use), `get_admin`, `verify_admin_login` (unknown username indistinguishable from wrong password — both `None`, with a timing-balancing hash burn), `set_admin_password` (rotation with a fresh salt). `admin_cli.py` — `python -m backend.admin_cli init [USERNAME]` reads the password from `NETWORK_REPORT_ADMIN_PASSWORD` (install.sh path) or getpass with confirmation; never in argv/stdout/logs; registers the value with the secret-redaction log filter (§22.2). Plaintext exists only as function arguments (§21).  
**Tests:** unit `tests/unit/test_auth_passwords.py` (8: scrypt format + cost, random salt, correct/wrong verification, plaintext never in the stored hash incl. hex form, unicode round trip, malformed-hash → False, empty inputs) + integration `tests/integration/test_auth_admin.py` (8: single-account creation, persisted column is scrypt hash not plaintext, reinitialize-in-place keeps one row, correct/wrong/unknown login indistinguishability, password rotation invalidates old, empty credentials rejected before DB, database-level second-admin rejection via `uq_users_single_admin`, migration 0009 schema shape incl. TIMESTAMPTZ + both unique indexes). pytest 260 unit + 8 auth/migration integration PASS; ruff clean; mypy clean (106 files).  
**Checkpoint:** see EXECUTION_STATE.md checkpoint ledger.

## W04-T002 — Server-side session and login/logout
**Status:** REVIEW_PASSED  
**Depends On:** W04-T001  
**Blocks:** W04-T003, W04-T004, W04-T005  
**Acceptance:** 7-day absolute and 12-hour idle timeout; HttpOnly/SameSite cookie; unauthenticated protected requests denied; login/logout tests pass.  
**Implementation:** migration `0010` — `sessions` (row id IS the opaque bearer token, FK users CASCADE, TIMESTAMPTZ `created_at`/`last_seen_at`/`expires_at`, index on `expires_at`). `auth/sessions.py` — server-side persistence in PostgreSQL (survives web restarts): `create_session` (`secrets.token_urlsafe(32)`, `expires_at = now + 7d`), `get_valid_session` (valid only while `now < expires_at` AND `now < last_seen_at + 12h`; a valid lookup slides ONLY the idle anchor — the absolute bound is never extended), `destroy_session`, `purge_expired_sessions` (opportunistic housekeeping at login). Web layer (`backend/web/`): `deps.require_admin` — the single auth gate for protected pages/actions, resolving the `nw_session` cookie server-side and redirecting unauthenticated/expired requests to /login before anything renders; `security.py` CSRF double-submit cookie (`nw_csrf` HttpOnly SameSite=Lax + hidden `csrf_token` field, `hmac.compare_digest`) enforced FIRST on every state-changing POST (403 without touching state); `routes.py` — GET/POST /login (fixed identical error text for unknown username vs wrong password — no account enumeration; fresh session id + rotated CSRF at every successful login so a pre-login fixation token is never reused), POST /logout (destroys the row, clears cookies), GET / (authenticated home). Session cookie: HttpOnly + SameSite=Lax + Max-Age=7d; no `Secure` flag (plain-HTTP intranet service per §20/AGENTS). No new frontend stack — server-rendered HTML forms only (§20.4); deps `python-multipart` (runtime, form parsing) + `httpx` (dev-only, TestClient).  
**Tests:** unit `tests/unit/test_web_security.py` (4: CSRF match/mismatch/missing/empty, token uniqueness/length, stable cookie/field names + TTL constants) + integration `tests/integration/test_auth_sessions.py` (7: create/resolve with exact 7-day anchor, unknown/empty token, idle-slide-only touch, absolute expiry incl. exact boundary, idle expiry, destroy invalidates server-side, purge removes only expired) + integration `tests/integration/test_web_auth.py` (15: unauthenticated/forged cookie denied, login page CSRF, cookie flags on success, failure shows fixed error + no session, wrong-username vs wrong-password indistinguishable, logout invalidates + replay denied, logout/login CSRF missing-forged → 403 with session intact, fixation rotation, absolute timeout, idle timeout, activity slides idle-not-absolute, password/token never in pages). pytest 264 unit + 200 integration PASS; ruff clean; mypy clean (116 files); `alembic upgrade head` idempotent at 0010.  
**Checkpoint:** see EXECUTION_STATE.md checkpoint ledger.

## W04-T003 — Report list and download
**Status:** REVIEW_PASSED  
**Depends On:** W04-T002  
**Blocks:** W04-GATE  
**Acceptance:** reverse chronological report list; period/status/generated time/error visible; authenticated DOCX download works.  
**Implementation:** `web/reports.py` — `GET /reports` (behind `require_admin`) lists `weekly_reports` newest week first (W03-T009 `load_weekly_reports` re-used) with week, period rendered as the natural week `周一至周日` (Asia/Shanghai), generated_at in +0800, status 成功/失败, `last_error` when present, download link only on success rows, 暂无报告 for an empty registry (§20.2). `GET /reports/{week_code}/download` — the request NEVER carries a path: the ISO week code (strict `YYYY-Www` regex, then a real `period_for_iso_week` validation so impossible weeks 404) selects the registry row; the file is served only when the row is the week's `success` entry AND the stored path's name is exactly the canonical `network-weekly-report-<week>.docx` AND its resolved parent is the resolved report directory AND it is not a symlink AND the target is an existing regular file (§27.18) — tampered rows, traversal paths, symlink escapes, missing files and failed reports all 404; served as `FileResponse` attachment with the OOXML media type. Regenerate column/action arrives with W04-T004.  
**Tests:** unit `tests/unit/test_web_reports_validation.py` (15 week-code cases + media type constant) + integration `tests/integration/test_web_reports.py` (12: unauthenticated /reports + download 303; empty registry; required columns newest-first incl. 周一至周日 period, +0800 generated time, 成功/失败, last_error visible, download link only on success; download round-trip bytes + attachment header; failed/unknown/malformed/impossible weeks 404; tampered path outside report dir 404; traversal path 404; symlink escape 404; missing file 404; page never leaks file paths). pytest 278 unit + 214 integration PASS; ruff clean; mypy clean (119 files).  
**Checkpoint:** see EXECUTION_STATE.md checkpoint ledger.

## W04-T004 — Manual regenerate Web action
**Status:** REVIEW_PASSED  
**Depends On:** W04-T002, W03-T010 regenerate service implementation（REVIEW_PASSED — `reporting/jobs.py::request_regenerate`；真实数据 acceptance 已按 Owner Decision 2026-09-06 移至 W05-GATE，不阻塞本任务）  
**Blocks:** W04-GATE  
**Acceptance:** CSRF-protected; duplicate submissions do not create concurrent duplicate report jobs; status becomes visible to user.  
**Implementation:** `POST /reports/{week_code}/regenerate`（`web/reports.py`）— a thin adapter onto the Wave 3 `request_regenerate` service; NO job logic in the web layer. CSRF double-submit checked FIRST (403 without touching state); week code validated (regex + real ISO construction, invalid → 404); an incomplete week is refused by the service and rendered as one fixed note (`该统计周尚未结束` — the exception text is never rendered); success redirects back to the list. The report list now carries a 重新生成 column (per-row POST form with the hidden CSRF field, §20.4) and renders each week's ACTIVE job state next to the report status (`重新生成排队中/进行中/失败，等待自动重试`) — the action's effect is visible while the previous success stays downloadable (§4.4). Duplicate submissions are impossible to duplicate: service idempotency + the `uq_report_jobs_active_week` partial unique index (W03-T009) — the web route adds nothing that could bypass them.  
**Tests:** integration `tests/integration/test_web_regenerate.py` (9: regenerate creates exactly one manual pending job; 3 duplicate submissions still yield exactly one job row; missing/forged CSRF → 403 with no job; unauthenticated POST 303 with no job; incomplete week refused with the fixed note and no job; malformed weeks denied; active job status visible on the list while the old DOCX stays downloadable; regenerate form rendered with CSRF field). pytest 278 unit + 224 integration PASS; ruff clean; mypy clean (120 files).  
**Checkpoint:** see EXECUTION_STATE.md checkpoint ledger.

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
**Depends On:** W05-T005, W01-T007, W03-T010（Owner Decision 2026-09-06：真实周报人工 DOCX 核对从 W03-GATE 延期至此，作为明确依赖）  
**Blocks:** release  
**Required:** report correctness/reliability PASS; monitoring semantics PASS; login/download/priority-interface PASS; install/update PASS; no known P0/P1 blocker; **W01-T007 real-device field validation CLOSED (standalone S10500X + S10500X IRF + S12500 IRF)**; **W03-T010 real weekly report manually checked against source samples CLOSED (真实周数据人工核对)**.
