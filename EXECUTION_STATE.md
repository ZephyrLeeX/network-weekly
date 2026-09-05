# EXECUTION_STATE.md

## Current execution

```text
Current Wave: W03 — implementation COMPLETE, GATE BLOCKED (real week data)
Current Task: W03-GATE stopped per owner instruction 2026-09-05 — W03-T010's
  real-report verification has no real weekly data in this environment, so
  the gate is NOT judged PASS and work/wave-03 is NOT merged into main.
Branch: work/wave-03 (engineering verification all green; merge deferred
  until the W03-T010 real-data acceptance closes)
```

## Wave 3 checkpoint ledger

```text
W03-T001: 3fffeb6e582dd8ec88d0c9cb862ff224caecbc9e — REVIEW_PASSED
          (reporting/period.py: half-open [Mon 00:00, next Mon 00:00)
          Asia/Shanghai periods; ISO week-year codes 2026-W36; 53-week
          years; cross-week overlap membership; 15 unit tests)
W03-T002: bfd213458a377002444f934bd79c65dcab06d79f — REVIEW_PASSED
          (reporting/resources.py: avg/max/P95 via PostgreSQL
          percentile_cont(0.95); interface Top 10 by per-sample
          max(in,out) then per-interface P95; single statistical
          implementation; missing data None-not-0; 8 integration tests)
W03-T003: 253a51491d829bc04bffb13597e889ba9c8d9433 — REVIEW_PASSED
          (reporting/incidents.py: weekly device + priority-interface
          incident summaries from long-term records; cross-week and
          ongoing-at-period-end semantics for §19; 7 integration tests)
W03-T004: 5d10c5249eb0b3e58c48fc6773b8a5a41eae3233 — REVIEW_PASSED
          (reporting/irf_summary.py: per-fabric weekly summary — expected/
          current members, missing windows with reappearance, missing-at-
          period-end, reliable role changes; observation-less week is
          数据缺失, never member loss; 8 integration tests)
W03-T005: f6098039b75d2875c62b8b44568833577894f97a — REVIEW_PASSED
          (reporting/counters_coverage.py: reset-safe weekly CRC/error/
          drop deltas via forward-only lag-window increments — resets
          contribute nothing, never-reported columns are NULL-数据缺失;
          §18 Coverage expected=2016-cycle grid, (SUCCESS+PARTIAL)/
          expected*100, PARTIAL separate, <95% = 数据完整性不足;
          observation-only status; 10 integration tests)
W03-T006: ea60349d4bc1e6e18a0b896e756d8990fe67e0a2 — REVIEW_PASSED
          (reporting/service.py: build_weekly_report_data composes
          T002..T005 + sustained-high via W02-T007 implementation +
          deterministic §19 正常/关注/异常 rules + deterministic Chinese
          summary text; CRC/Error/Drop never change status; 11
          integration tests)
W03-T007: fa788731e2956201be602e8b1e2267bc2d449486 — REVIEW_PASSED
          (tests/integration/test_w03_golden_scenarios.py: 12 golden
          scenarios covering every IMPLEMENTATION_PLAN golden case —
          half-open boundary, ISO year boundary W2026-01, cross-week
          down, missing-sample continuity break, percentile_cont P95
          pinned, Top10 algorithm, counter reset no-fake-delta, PARTIAL
          coverage, <95% warning, whole-week no data, threshold override)
W03-T008: 0562ab01ff42b5ba61eebbaaff929c3be27a931f — REVIEW_PASSED
          (reporting/docx.py: python-docx renderer, fixed 8 sections,
          table-first, 数据缺失 explicit, blank editable 本周处理问题,
          temp file -> save -> reopen validation -> os.replace atomic;
          10 unit tests + 1 end-to-end integration test)
W03-T009: 23819aa406bfce97a54588fb10f790d91a304742 — REVIEW_PASSED
          (migration 0008 report_jobs + weekly_reports; partial unique
          index uq_report_jobs_active_week = one ACTIVE job per week;
          Monday 00:10 = period end + 10 min persistent scheduling;
          10-minute retry with sanitized last_error; worker-restart
          recovery; reporting/schedule.py WeeklyReportLoop as worker
          thread; 13 integration tests)
W03-T010: 557165cee396361984a7b9c63e597d43cea60be8 — engineering acceptance
          REVIEW_PASSED, real-report acceptance BLOCKED (below)
W03-GATE: 557165cee396361984a7b9c63e597d43cea60be8 — BLOCKED/STOPPED evidence
          (engineering verification green; NOT PASS; no merge)
```

## W03-T010 / W03-GATE BLOCKED item (2026-09-05)

```text
W03-T010 — real weekly report manually checked against source samples:
  BLOCKED — REAL_WEEK_DATA_PENDING. The regenerate service acceptance is
  implemented and tested; the real-report verification is impossible in
  this environment (no reachable real H3C device, dev database verified:
  0 devices / 0 poll_runs / 0 metrics — same root cause as W01-T007).
  Owner instruction 2026-09-05: no fabricated acceptance; mark BLOCKED.
W03-GATE — STOPPED (NOT PASS) per owner instruction: engineering
  verification is fully green (pytest 248 unit + 150 integration PASS on
  migrated PostgreSQL 0001→0008; ruff clean; mypy clean 99 files; alembic
  upgrade head idempotent at 0008; compose rebuilt + smoke: web healthy,
  worker heartbeat persisted, device-poll + IRF + retention + weekly
  report loops alive, live container run generated an atomic, validated
  8-section DOCX for 2026-W35 from the persisted job path). The only
  missing gate input is the real weekly data manual check. Re-run the
  gate after one real collection week exists.
Merge: work/wave-03 NOT merged into main (merge is conditioned on
  W03-GATE PASS). Wave 4 not started.
```

## Wave 2 checkpoint ledger

```text
W02-T001: 9e1601de5e691f903810abe837935cc616649d2f — REVIEW_PASSED
          (migration 0003; poll runs + device/interface metrics; idempotent
          per-cycle persistence; SUCCESS/PARTIAL/FAILED + missing-stays-missing)
W02-T002: 2700b5942e03de8a80e1f8910740e01a6d90e7c0 — REVIEW_PASSED
          (aligned 5-min scheduler; no same-device overlap; restart continues
          future cycles only; poll pipeline single-transaction persistence;
          worker heartbeat + scheduler threads)
W02-T003: b39d72d94ce6c3ac27e2b76c061ad796bc505e5a — REVIEW_PASSED
          (counter delta x actual elapsed x effective speed; rebaseline on
          reset/negative delta/missing counters/speed invalid or changed/
          interval invalid/>100%; no fake spikes possible)
W02-T004: 7a38ee93f270dcd21fd1913c23a776b972242c49 — REVIEW_PASSED
          (migration 0004; 2-cycle Down/Recovery semantics with gap reset;
          SSH-only reachability never converts missing SNMP into success;
          long-term reachability incidents per §9.4)
W02-T005: 50b34287c97a0afbec3d72549c1a9882d8fa2953 — REVIEW_PASSED
          (set_monitored single write path, no aggregation cascade;
          interface_overview read model; rediscovery/ifIndex change
          preserves monitored — no schema change needed)
W02-T006: 4c76fcd9e76a4b052054ee11418a3de1a4db0b87 — REVIEW_PASSED
          (migration 0005; 2-valid-sample interface Down/Recovery; missing
          does not count nor break (§13.3 reading); sustained-high detection
          core shared with W02-T007)
W02-T007: 683e3d250fdb961a3be498691b4e0ab4400edea1 — REVIEW_PASSED
          (migration 0006 system_settings; 80%/3-sample defaults; validated
          runtime-configurable thresholds; cycle-grid series loaders; missing
          sample breaks threshold continuity per §14)
W02-T008: c693d53f8c67ad94ab328904a31a8f0193ffd018 — REVIEW_PASSED
          (migration 0007 irf_member_observations; ~15-min loop; IRF devices
          only; SSH failure records nothing; missing/reappeared/role change
          persisted for Wave 3)
W02-T009: 382e7131d84b00d4c358ded984a4da2b6ebc8138 — REVIEW_PASSED
          (batched §24 cleanup of raw metrics/poll runs; long-term data
          untouched by construction; retention_days >= 90 enforced)
W02-GATE: PASS (engineering gate, 2026-09-05)
  Gate verification checkpoint: 45ed77811911df414d2e4b020fcfb4a6b690b3c3
  (work/wave-02)
W02 merge commit into main: 1709676adf17214d24cfdf30ba14f8c8d2255c33
  (post-merge re-check on main: 205 unit + 72 integration PASS, ruff/mypy
  clean, alembic at 0007)
W02-AUDIT: 3a135350ca77d4a72effee3aafc21ac6c6fe67b6 — REVIEW_PASSED
  (§8 planned-cycle completeness, no-fake-SUCCESS collection semantics,
  whole-cycle poll idempotency, sustained-high interval duration §14/§15.3;
  no schema change, no migration; no product capability added)
W02-AUDIT merge commit into main: 17ee3b9104742d6b5ad31822b571a794846aa59d
  (post-merge re-check on main: 218 unit + 77 integration PASS, ruff/mypy
  clean, alembic at 0007)
W02-AUDIT-2: de40bbf0e2f3e8ccd38ec5d8970e6d042f1acf0b — REVIEW_PASSED
  (audit follow-up: scheduler overlap race — _in_flight keeps only real
  poll futures, per-cycle FAILED/overlap rows for a poll spanning cycles,
  never a second real poll; interfaces DEGRADED judged per required field
  (oper_state/speed/in/out_octets/in/out_errors/in/out_discards, HC/32-bit
  same-field fallback) instead of per column group; no schema change, no
  migration)
W02-AUDIT-2 merge commit into main: 952db0a2d330e87a64da04882a9b129bc80edb45
  (post-merge re-check on main: 223 unit + 77 integration PASS, ruff/mypy
  clean, alembic at 0007)
```

## User authorization (2026-09-04)

```text
No real H3C device is currently reachable. The user authorized:
  H3C official documentation (MIB Companion / Command Reference) is the
  primary engineering-acceptance basis for Wave 1; real S10500X/S12500
  acceptance is deferred but MUST be completed before the production
  release.
```

## FIELD_VALIDATION_PENDING (release-blocking, not Wave-2-blocking)

```text
W01-T007 — BLOCKED — FIELD_VALIDATION_PENDING. Must close before the
production Release Gate (W05-GATE). Outstanding real-device acceptance:
  1. Real standalone S10500X collection end-to-end.
  2. Real S10500X IRF collection end-to-end.
  3. Real S12500 IRF collection end-to-end.
Also pending on real devices: hh3c-entity-ext CPU/memory OID confirmation,
Comware aggregation-id == aggregation-ifIndex confirmation, replacement of
the synthetic fixtures in tests/fixtures/h3c/ with anonymized real captures,
and confirmation of the corrected IEEE8023-LAG-MIB .12/.13 columns.
```

## Last completed task

```text
W03-T010 (engineering scope) — regenerate service REVIEW_PASSED:
  request_regenerate is idempotent (one active job per week; repeated
  submissions return the same job), refuses incomplete weeks, and runs
  through execute_report_job: the previous successful DOCX stays current
  and downloadable until a successful attempt atomically replaces the
  single per-week file (§4.4/§5); failures persist status + readable
  error and never touch the old file.
  Tests: test_manual_regenerate.py (replace-one-DOCX, double-submit
  idempotency, failure visibility + retryability).
  The REAL-report acceptance of this task is BLOCKED — see the W03-T010/
  W03-GATE BLOCKED item above.
```

## Previous completed task

```text
W02-AUDIT (Wave 2 audit hotfix) — REVIEW_PASSED:
  1. §8 completeness: every planned cycle lands one device_poll_run —
     overlap skips recorded FAILED ('overlap') by the scheduler;
     missing per-device SNMP credentials and an unreadable secrets file
     land FAILED ('credentials') via unattemptable contexts. No overlap,
     no backfill; §9/§13 state machines never advance for these rows.
  2. No fake SUCCESS: empty cpu/memory collection is a section failure;
     interfaces missing key column groups (state/speed/octets/errors) is a
     DEGRADED section — samples kept, poll run PARTIAL.
  3. Whole-cycle idempotency: a replayed (device, cycle) never re-collects
     and never re-advances the reachability/interface state machines, so a
     duplicated Down sample cannot falsely confirm DOWN.
  4. §14/§15.3: sustained-high intervals now span
     [first ts, last ts + one cycle slot) — 3 consecutive 5-minute samples
     report 15 minutes, not 10; Wave 3 reads correct durations.
Evidence: pytest 218 unit + 77 integration PASS on migrated PostgreSQL
(0007); ruff clean; mypy clean (78 files); alembic upgrade head idempotent
at 0007 (no migration needed); compose rebuild + smoke: web healthy,
heartbeat persisted, scheduler starts at the next boundary with no
backfill, and the unattemptable dev cycle is visible as a FAILED poll run
(failed_sections=credentials) with device_monitoring_state untouched.
```

## Last checkpoint

```text
W03 gate-verification commit: (this commit, work/wave-03 — see git log)
W03-T010 checkpoint: (this commit, work/wave-03)
W03-T009 checkpoint: 23819aa406bfce97a54588fb10f790d91a304742 (work/wave-03)
W03-T008 checkpoint: 0562ab01ff42b5ba61eebbaaff929c3be27a931f (work/wave-03)
W03-T007 checkpoint: fa788731e2956201be602e8b1e2267bc2d449486 (work/wave-03)
W03-T006 checkpoint: ea60349d4bc1e6e18a0b896e756d8990fe67e0a2 (work/wave-03)
W03-T005 checkpoint: f6098039b75d2875c62b8b44568833577894f97a (work/wave-03)
W03-T004 checkpoint: 5d10c5249eb0b3e58c48fc6773b8a5a41eae3233 (work/wave-03)
W03-T003 checkpoint: 253a51491d829bc04bffb13597e889ba9c8d9433 (work/wave-03)
W03-T002 checkpoint: bfd213458a377002444f934bd79c65dcab06d79f (work/wave-03)
W03-T001 checkpoint: 3fffeb6e582dd8ec88d0c9cb862ff224caecbc9e (work/wave-03)
W02-AUDIT-2 checkpoint: de40bbf0e2f3e8ccd38ec5d8970e6d042f1acf0b (work/wave-02-audit-fix-2)
W02-AUDIT checkpoint: 3a135350ca77d4a72effee3aafc21ac6c6fe67b6 (work/wave-02-audit-fix)
W01-T003 fix checkpoint: 9c4b57bd7b0b6445b8b4b9144f1e9fdb84bc2898 (work/wave-01)
W01-T004 fix checkpoint: a8c394348206a3e65355a643fd6cef7939f0c8da (work/wave-01)
W01-T005 fix checkpoint: 56010bf3a7d823f7737de160abe163fcf2518ce0 (work/wave-01)
W01-T006 fix checkpoint: 040069681dc73a76e94fda6bc15390298783e0f1 (work/wave-01)
W01-GATE revalidation checkpoint: e2c1fba47e8ae0ef471ec04fe1f79492692e7d78 (work/wave-01)
W01 merge commit into main: d3b27515fc977e221c401e5ef23e4ed53d2917fd
W01-T002 checkpoint: 28a94107ef52d4546eb29ad10e72a588be96d33d (work/wave-01)
W01-T001 checkpoint: dc8e021f9b2059fa3867b5b3bc53c1e6dca0b22b (work/wave-01)
W00-AUDIT checkpoint: f9bf4ea4f3b48124e95e5cef3a6b78800a4549f8
W00-T005 checkpoint: ec27924b88dbfe92521a93a54ff9f6a1c59d87d1
W00-GATE verification commit: 9798e87 (work/wave-00), merged into main as a1f539344f854597eb9f980b620c2e6accd00606
```

## Checkpoint ledger (Wave 1)

```text
W01-T001: dc8e021f9b2059fa3867b5b3bc53c1e6dca0b22b — REVIEW_PASSED
W01-T002: 28a94107ef52d4546eb29ad10e72a588be96d33d — REVIEW_PASSED
W01-T003: 9c4b57bd7b0b6445b8b4b9144f1e9fdb84bc2898 — REVIEW_PASSED
          (engineering acceptance; field validation in W01-T007)
W01-T004: a8c394348206a3e65355a643fd6cef7939f0c8da — REVIEW_PASSED
          (engineering acceptance; field validation in W01-T007)
W01-T005: 56010bf3a7d823f7737de160abe163fcf2518ce0 — REVIEW_PASSED
          (engineering acceptance; field validation in W01-T007)
W01-T006: 040069681dc73a76e94fda6bc15390298783e0f1 — REVIEW_PASSED
          (engineering acceptance; field validation in W01-T007)
W01-GATE: PASS (engineering gate — see ledger below)
```

## Blocked tasks

```text
W01-T007: BLOCKED — FIELD_VALIDATION_PENDING. No reachable real
  S10500X/S12500 in this environment (user authorization 2026-09-04).
  Blocks the production Release Gate (W05-GATE) only. Close by running
  W01-T007 evidence collection on real devices and re-running the full
  gate.
W03-T010: BLOCKED — REAL_WEEK_DATA_PENDING (real-report verification
  acceptance only; the regenerate service is implemented and tested).
  Same root cause as W01-T007: no reachable real device, hence no real
  weekly data. Blocks W03-GATE PASS. Close by collecting one complete
  real week and manually checking the DOCX against source samples.
W03-GATE: BLOCKED — REAL_WEEK_DATA_PENDING (STOPPED per owner 2026-09-05;
  engineering verification green, see W03-GATE entry in TASK_GRAPH.md).
  Blocks Wave 4 start and the work/wave-03 -> main merge.
```

## Open design gaps

```text
None.
Documented §13 interpretation (W02-T006): for priority interfaces, a
missing/undetermined sample neither counts as Up/Down NOR breaks the
run of valid samples (§13.3 wording; contrast with §14 thresholds where
a missing sample explicitly breaks continuity). Recorded in
backend/monitoring/interface_state.py and TASK_GRAPH W02-T006.
```

## Next ready candidates

```text
None — W03-GATE is BLOCKED (real week data) and W04-T001 depends on it.
To close W03: collect one complete real week on real devices, manually
check the generated DOCX against source samples (close W03-T010's BLOCKED
acceptance and W01-T007 evidence collection), then re-run the W03-GATE
verification and merge work/wave-03 into main.
```

## Current Wave Gate

```text
W03-GATE — Weekly Report Gate
Status: BLOCKED — REAL_WEEK_DATA_PENDING (STOPPED per owner 2026-09-05;
NOT PASS). Engineering verification fully green on work/wave-03:
  pytest 248 unit + 150 integration PASS on migrated PostgreSQL (0008);
  ruff clean; mypy clean (99 files); alembic upgrade head idempotent at
  0008; compose image rebuilt (docker build; compose build is a silent
  no-op in this environment) + smoke: web healthy (/health database ok),
  worker heartbeat persisted, device-poll scheduler + IRF loop +
  retention + weekly-report loop alive; the report loop live-created and
  executed the 2026-W35 scheduled job (catch-up per §4.2), producing one
  atomic, revalidated 8-section DOCX in the reports volume with honest
  数据缺失 rendering for an empty deployment.
Golden scenarios cover every IMPLEMENTATION_PLAN golden case; failure/
restart semantics (10-min retry, worker restart recovery, one active job
per week at DB level, regenerate keeps old DOCX until atomic replace)
are proven by integration tests.
Missing for PASS: one DOCX generated from REAL persisted weekly data,
manually checked against source samples (blocked with W01-T007's root
cause). Real-device proof remains carried by W01-T007 for W05-GATE.
```

## Product baseline

```text
Network Weekly Report System
One isolated network
10 logical H3C devices / 12 physical chassis
H3C S10500X / S12500 only
SNMP primary collection + limited read-only SSH
5-minute polling
DOCX only
Monday 00:10 automatic report
Asia/Shanghai
Single administrator
```

## Authority

```text
Product / architecture: SYSTEM_SPEC.md
Task state: TASK_GRAPH.md + EXECUTION_STATE.md
Execution roadmap: IMPLEMENTATION_PLAN.md
Agent rules: AGENTS.md
```

## Control rules

- Codex works only on `READY` tasks.
- Keep changes inside current Task Scope.
- Capabilities not defined by `SYSTEM_SPEC.md` are not part of the product.
- Every schema change uses Alembic.
- Real-device field validation: engineering `REVIEW_PASSED` is granted on
  H3C official documentation verification + passing tests; REAL-device
  evidence is mandatory before the production Release Gate and is tracked
  by W01-T007 (FIELD_VALIDATION_PENDING) — see AGENTS.md.
- Every `REVIEW_PASSED` Task requires an identifiable checkpoint commit SHA.
- After any meaningful Task-state change update this file and `TASK_GRAPH.md`.
