# EXECUTION_STATE.md

## Current execution

```text
Current Wave: W01
Current Task: W01-GATE (BLOCKED — real-device evidence unavailable)
Current Task Status: W01-T001/T002 REVIEW_PASSED; T003–T006 implemented+tested, BLOCKED for real-device sign-off
Branch: work/wave-01 (NOT merged — gate not PASS)
```

## Last completed task

```text
W01-GATE non-device verification (no task status change):
  uv sync --frozen OK; pytest 111 unit + 24 integration pass; ruff clean;
  mypy clean; alembic upgrade head idempotent at 0002; compose rebuild +
  smoke: web healthy, worker heartbeat persistent.
```

## Last checkpoint

```text
W01-T001 checkpoint: dc8e021f9b2059fa3867b5b3bc53c1e6dca0b22b (work/wave-01)
W01-T002 checkpoint: 28a94107ef52d4546eb29ad10e72a588be96d33d (work/wave-01)
W00-AUDIT checkpoint: f9bf4ea4f3b48124e95e5cef3a6b78800a4549f8
W00-T005 checkpoint: ec27924b88dbfe92521a93a54ff9f6a1c59d87d1
W00-GATE verification commit: 9798e87 (work/wave-00), merged into main as a1f539344f854597eb9f980b620c2e6accd00606
```

## Checkpoint ledger (Wave 0)

```text
W00-T001: cd65eee2e5979785939f35f273d364f4cd4826f0
W00-T002: 7f00ce12021477f00cfeb9c0673edb8e5ebe92a2
W00-T003: ba575e6e231d627cd3906bac8b84572f94fe3f39
W00-T004: deed3fd266ed56b8fb7e936bbf972bd197e30cff
W00-T005: ec27924b88dbfe92521a93a54ff9f6a1c59d87d1
W00-GATE: PASS
W00-AUDIT: f9bf4ea (wave 0 audit hotfix, work/wave-00-audit-fix), merged into main as fb00f9227d42d57575c517121d93d8df684fc000
W00-GATE revalidated after audit hotfix: PASS
```

## Checkpoint ledger (Wave 1)

```text
W01-T002: 28a94107ef52d4546eb29ad10e72a588be96d33d
W01-T001: dc8e021f9b2059fa3867b5b3bc53c1e6dca0b22b
W01-T003: 891ad123d838b3dc2a144de760eb4bd0b78c08bc (BLOCKED: real-device evidence)
```

## Blocked tasks

```text
W01-T003: BLOCKED for REVIEW_PASSED — no reachable real S10500X/S12500 in
  this environment. Code implemented + unit-tested (synthetic fixtures);
  real-device collection evidence still required. Same condition is
  expected to apply to W01-T004/T005/T006 and W01-GATE.
```

## Open design gaps

```text
None
```

## Next ready candidates

```text
W01-T003 — SNMP transport and basic H3C collection (READY)
W01-T005 — Limited SSH transport and IRF discovery (READY after T001/T002)
W01-T004, W01-T006, W01-GATE — require real S10500X/S12500 evidence; if no
real device is reachable they must stay BLOCKED (no mock sign-off).
```

## Current Wave Gate

```text
W01-GATE — Real H3C Gate
Status: BLOCKED — real S10500X/S12500 evidence unavailable in this
environment; NOT PASS, NOT merged to main. All non-device gate checks
already PASS (tests/lint/types/migrations/compose smoke). When real device
access exists: run W01-T007 evidence collection (standalone S10500X,
S10500X IRF, S12500 IRF), replace synthetic fixtures in
tests/fixtures/h3c/, confirm OIDs in backend/collect/h3c/oids.py, then
re-run the gate and merge.
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
- Real-device collection tasks require real H3C evidence before final `REVIEW_PASSED`.
- Every `REVIEW_PASSED` Task requires an identifiable checkpoint commit SHA.
- After any meaningful Task-state change update this file and `TASK_GRAPH.md`.
