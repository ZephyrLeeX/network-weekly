# EXECUTION_STATE.md

## Current execution

```text
Current Wave: W02 — CLOSED (engineering gate PASS, merged to main)
Current Task: none ready — Wave 3 not started (owner decision required)
Branch: work/wave-02 merged into main (see merge SHA below)
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
W02-GATE (Wave 2 — Monitoring Pipeline) PASS (engineering):
  pytest 205 unit + 72 integration PASS on migrated PostgreSQL (0007);
  ruff clean; mypy clean; alembic upgrade head idempotent at 0007;
  compose rebuild + smoke: web healthy, worker runs heartbeat + 5-min
  DEVICE_POLL scheduler + 15-min IRF observation loop + retention, heartbeat
  persisted, restart semantics verified live (next-boundary start, no
  backfill), missing dev secrets contained per cycle.
Gate scenario test walks one device end-to-end: utilization over actual
elapsed, counter-reset rebaseline, FAILED×2 -> DOWN, reachable×2 ->
RECOVERED, sustained-high semantics, retention vs long-term incidents.
```

## Last checkpoint

```text
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
  Blocks the production Release Gate (W05-GATE) only; does NOT block
  Wave 2. Close by running W01-T007 evidence collection on real devices
  and re-running the full gate.
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
Wave 3 (W03-T001 …) — all dependencies satisfied (W02-GATE PASS).
Owner decision required: do NOT start Wave 3 without authorization.
```

## Current Wave Gate

```text
W02-GATE — Wave 2 Monitoring Pipeline Gate
Status: PASS (2026-09-05). Engineering gate over W02-T001..T009:
  pytest 205 unit + 72 integration against migrated PostgreSQL (0007);
  ruff clean; mypy clean; alembic upgrade head idempotent at 0007;
  compose rebuild + smoke: web healthy, worker heartbeat + schedulers
  persistent, no backfill on restart, per-cycle error containment.
Covers: 5-min scheduling, no same-device overlap, restart semantics,
SUCCESS/PARTIAL/FAILED persistence, counter reset/rebaseline, device and
priority-interface Down/Recovery (2-cycle rules, gap resets), sustained
CPU/memory/utilization (>=80% x3, missing breaks), IRF observation
(missing/reappearance/reliable role change, SSH-failure records nothing),
90-day batched retention sparing long-term data.
Real-device proof is NOT part of this gate: it is carried by
W01-T007 (BLOCKED — FIELD_VALIDATION_PENDING), which blocks W05-GATE.
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
