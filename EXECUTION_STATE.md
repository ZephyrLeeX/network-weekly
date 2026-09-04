# EXECUTION_STATE.md

## Current execution

```text
Current Wave: W00
Current Task: W00-T001
Current Task Status: READY
Branch: work/wave-00
```

## Last completed task

```text
None
```

## Last checkpoint

```text
None
```

## Blocked tasks

```text
None
```

## Open design gaps

```text
None
```

## Next ready candidates

```text
W00-T001 — Initialize repository structure
```

## Current Wave Gate

```text
W00-GATE — Foundation Gate
Status: TODO
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
