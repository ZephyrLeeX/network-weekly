# EXECUTION_STATE.md

## Current execution

```text
Current Wave: W05 — Deployment and Stability — engineering side
  COMPLETE (T001–T004 REVIEW_PASSED, branch work/wave-05, NOT yet merged
  to main — awaiting W05-GATE which is BLOCKED on real-environment
  acceptance; created 2026-09-06)
Current Task: W05-AUDIT-2 — Wave 5 audit follow-up COMPLETE and
  REVIEW_PASSED (commit 78f4e3b2ef64daaab6ca39993aa28068294e499d, branch
  work/wave-05): (1) heartbeat verifier race fixed — the verdict is now
  bound to the worker's real Docker container identity: a REPLACED
  container additionally requires started_at past the baseline, so the
  old worker's post-baseline final tick can never pass as "replacement
  worker healthy"; (2) A09 evidence moved from weekly_reports.generated_at
  (overwritten by manual regenerate) to the append-only scheduled
  report_jobs history; plus the two ACCEPTANCE.md typos (A12/A13→A15/A16,
  A15's 证据来源 A13→A15). W05-AUDIT = REVIEW_FAILED — superseded by
  W05-AUDIT-2 (its retention-evidence and rollback-guidance fixes stand,
  its heartbeat fix is resolved here). W05-T001..T004 REVIEW_PASSED,
  revalidated; W05-T005 stays IN_PROGRESS —
  REAL_ENVIRONMENT_EVIDENCE_PENDING; W05-GATE stays BLOCKED; no formal
  two-week acceptance timing started; main NOT merged.
Previous task: W05-T005 — Real-environment stability acceptance:
  scaffolding COMPLETE (docs/ACCEPTANCE.md A01–A16 + templates,
  scripts/acceptance_evidence.sh, docs/evidence/); acceptance itself
  BLOCKED until real-environment evidence — T005 must NOT be marked
  REVIEW_PASSED, W05-GATE stays BLOCKED (Owner 指令 2026-09-06).
W04-AUDIT merge into main: 872b1631f64ad83d02bff7088679cc5d6accf2e9
  (post-merge re-check on main: 278 unit + 241 integration PASS, ruff
  clean, mypy clean in 122 files, alembic upgrade head idempotent at
  0011 = head)
W03 merge into main: 131844461ade75c1517b1da91bd7d49bc1090521
W04 merge into main: 398204eb265091a211644ad772bc29a2ba878fa8
Wave 5 discipline (Owner instruction 2026-09-06):
  - W05-T001..T004 are engineering scope and may complete with
    REVIEW_PASSED after tests + smokes (fresh install, update, container
    replacement, health/heartbeat).
  - W05-T005 needs REAL environment evidence (Debian 13 amd64 install,
    offline runtime, 10 logical devices, standalone S10500X, S10500X IRF,
    S12500 IRF, aggregation/priority interfaces, >= 2 complete report
    weeks, 2 consecutive Mondays, DOCX failure 10-min retry, worker
    restart recovery, regenerate one-current, 90d retention, update.sh
    flow). No REVIEW_PASSED and no fabricated evidence without it.
  - W05-GATE stays BLOCKED until W05-T005 + W01-T007 + W03-T010 close.
W03-T010 = BLOCKED — REAL_WEEK_DATA_PENDING (blocks W05-GATE only).
W01-T007 = BLOCKED — FIELD_VALIDATION_PENDING (blocks W05-GATE only).
```

## Wave 5 checkpoint ledger

```text
W05-AUDIT-2: 78f4e3b2ef64daaab6ca39993aa28068294e499d — REVIEW_PASSED
          (audit follow-up, no new capability, NO new migration — head
          stays 0011; supersedes the W05-AUDIT heartbeat fix:
          (1) heartbeat verifier race (W05-T002/T003): the W05-AUDIT
          baseline state machine had no container-identity input — the OLD
          worker keeps running after the baseline is captured, so its final
          tick (last_heartbeat > baseline, seconds fresh) passed as
          "replacement worker healthy" while the replacement worker never
          started. Now the verdict is bound to real Docker container
          identity: lib.sh worker_container_id() (full docker inspect ID,
          ""=no running worker, query failure=error); install.sh/update.sh
          capture baseline + PRE id before up -d --wait and POST id after;
          backend/ops/heartbeat_verify.py CLI
          `verify <baseline> <pre-id> <post-id>` — replaced container ⇒
          started_at must ALSO advance past the baseline (old worker's
          post-baseline final tick = FAIL "the old container's final
          tick"); same-container re-verify needs only a new tick
          (started_at may stay put — never an unconditional condition);
          fresh install ({} baseline) passes on the first fresh row;
          missing post-start identity ⇒ FAIL (exit 19), never a
          same-container default; capability probe routes pre-W05-AUDIT-2
          images (usage exit 2 / missing module) to the inline fallback,
          which applies the SAME rules verbatim;
          (2) A09 evidence (W05-T005): docs/ACCEPTANCE.md proved "连续 2 个
          周一 00:10 自动报告" from weekly_reports.generated_at — the
          registry is the CURRENT success per week and a manual regenerate
          overwrites it, and WeeklyReport cannot prove trigger=scheduled.
          New backend/ops/report_job_evidence.py + read-only collector
          section "scheduled weekly report jobs": recent report_jobs rows
          WHERE trigger='scheduled' with week/trigger/status/attempts/
          expected (period end + 10 min = Monday 00:10 +08)/created/
          started/finished (Asia/Shanghai), on_schedule ±5 min annotation,
          weeks-with-succeeded-scheduled-job + two-consecutive summary
          (ISO wrap aware); manual jobs and error texts never printed;
          A09 now judges from report_jobs.created_at history
          (append-only), weekly_reports/DOCX corroboration only;
          (3) ACCEPTANCE.md typos: 人工核对类条目（A12/A13）→（A15/A16）;
          A15 证据来源 "A13 模板人工核对记录" → "A15 模板人工核对记录";
          tests: 329 unit + 261 integration PASS (new: race scenario both
          directions + replaced-requires-started_at-advance + same-container
          PASS with unmoved started_at + identity decision matrix +
          5-argv CLI shapes; fallback text invariants; race DATA sequence
          on real PostgreSQL incl. the same-container false-positive
          contrast + real-CLI verdict; manual-regenerate-does-not-destroy-
          scheduled-evidence, manual-only/failed-scheduled/consecutive
          weeks/ISO wrap); ruff clean; mypy clean (136 files); alembic
          upgrade head idempotent at 0011;
          smokes (staging roots, throwaway stack; images from this tree,
          from 838b5b6, and a raising-0012 variant): fresh install exit 0
          (baseline {} → pre <none> → post full ID → "first heartbeat"
          PASS); same-image update exit 0 (PRE==POST → "worker kept
          running", started_at legitimately unmoved); REAL image update
          head→v1 exit 0 (container replaced; target predates the new
          signature ⇒ capability probe routed to the inline fallback which
          produced the exact replacement-worker verdict); rollback to head
          exit 0 via the tested module ("started_at advanced past …");
          CRITICAL RACE with real containers: baseline → old worker's
          genuine post-baseline tick (40 s) PASSed same-container (the
          false-positive enabler, demonstrated) → worker force-recreated
          into a SILENT container (sleep 3600, new ID) → verifier FAIL
          exit 1 "old worker wrote after the baseline but the replacement
          worker has not" → real worker recreated, own tick → PASS exit 0
          "by the replacement worker"; with fresh rows for 'worker' AND
          'other-worker' the verifier as 'deploy-other' FAILs "no
          heartbeat row" (foreign rows never stand in); raising-0012 image
          → exit 14, .env restored, container IDs unchanged, /health ok;
          REAL exit-19 injection (heartbeat writes blocked by a trigger
          after a committed idempotent migration) → update.sh printed the
          mandated schema-aware block, ZERO "rollback with:" lines; block
          removed → update.sh exit 0; A09 on the dev stack: collector
          lists only scheduled rows (manual jobs absent), off-schedule
          honestly on_schedule=no; REAL manual regenerate of 2026-W35
          moved weekly_reports.generated_at ~2.4 h while the scheduled
          report_jobs row kept its original created_at/succeeded and the
          collector output was unchanged; full collector run exit 0)
W05-AUDIT: 838b5b613f80ed60999018ca8e061b798e81a51e — REVIEW_FAILED
          (superseded by W05-AUDIT-2; history preserved: WAS REVIEW_PASSED
          on 2026-09-06 — fix (2) retention evidence and fix (3)
          schema-aware rollback guidance stand; fix (1) heartbeat baseline
          state machine is resolved by W05-AUDIT-2 above)
          (original 2026-09-06 record, preserved: three audit fixes, no
          new capability, NO new migration — head stays 0011:
          (1) heartbeat verifier false positive (W05-T002/T003):
          wait_heartbeat accepted ANY fresh worker_heartbeat row — a stale
          row left by the previous worker passed as "new worker healthy".
          New tested module backend/ops/heartbeat_verify.py: row for
          NETWORK_REPORT_WORKER_ID must EXIST, last_heartbeat strictly >
          pre-start baseline ({} baseline = first row), age < interval*4;
          started_at reported ("worker restarted/kept running") but never
          a pass condition (same-image in-place re-verify must stay legal).
          install.sh + update.sh capture the baseline immediately before
          up -d; lib.sh carries an equivalent inline fallback so rollback
          targets predating the module keep working (smoke caught an
          argv[2]-index bug in that fallback — fixed);
          (2) acceptance evidence oldest raw data (W05-T005 A13):
          scripts/acceptance_evidence.sh used MAX(collected_at) labelled
          "oldest" — recent rows always masked beyond-90d data. New
          backend/ops/retention_evidence.py reports true MIN() oldest-row
          age for device_metrics/interface_metrics/device_poll_runs with
          BEYOND-90d/OK verdict; A13 wording aligned; W05-T004 retention
          implementation untouched;
          (3) rollback semantics (W05-T003): update.sh printed an
          unconditional "rollback with: update.sh --image OLD" on 17/18/19
          although the migration had already committed. New lib.sh
          post_migration_failure prints the mandated schema-aware block
          ("Database migration has already committed. ... Do NOT blindly
          roll back ... restore the pre-update database backup first");
          update-complete hint schema-qualified; docs/OPERATIONS.md §0/§4/§5
          rewritten (migration NOT committed → rollback safe; migration
          committed → rollback may require DB restore; no auto-downgrade,
          no automatic restore);
          tests: 318 unit + 253 integration PASS (new: verifier state
          machine scenarios A–E, verifier worker-id scoping + TIMESTAMPTZ
          round trip on real PG, oldest-row MIN with seeded 180d+1d rows →
          BEYOND-90d never OK, post_migration_failure executed for
          17/18/19, deploy-script invariants incl. no "rollback with:" in
          update.sh); ruff clean; mypy clean (133 files); alembic upgrade
          head idempotent at 0011;
          smokes (staging roots, throwaway stack, image built from
          838b5b6's tree): fresh install exit 0 (baseline {} →
          first-heartbeat PASS); force-recreate all containers → state
          intact; STALE-LEFTOVER scenario → FAIL "a stale row from a
          previous worker must not pass"; other-worker fresh row → FAIL
          "no heartbeat row for worker 'worker'" while that row was 18s
          fresh; worker restart → PASS "worker restarted"; same-image
          update rejected baseline-identical rows twice until the running
          worker's next tick ("kept running"); REAL image update ran
          0009→0010→0011 exit 0 with 10 devices + admin intact; raising-
          0012 image → exit 14, .env restored, 0 recreates; REAL exit-17
          injection (port conflict after committed migration) printed the
          mandated schema-aware block with ZERO "rollback with:" lines
          (18/19 messaging pinned by executing the same helper);
          documented rollback loop (explicit operator downgrade on the
          throwaway staging DB + update.sh --image prev) exit 0 via the
          legacy fallback; retention evidence with 180d+1d seeded rows →
          all three tables "oldest row ... age 180.0 days BEYOND-90d")
W05-T001: 64e827cc4eff04d126234256749fcd1ca0f74365 — REVIEW_PASSED
          (deploy/docker-compose.prod.yml: web/worker/postgres only, one
          pre-built app image, no build/no named volumes, DB + DOCX on
          /data/network-report bind mounts, devices.toml ro to web+worker,
          secrets.env ro to worker only + 0600 uid-1000, postgres not
          published; deploy/README.md layout + dev-volume one-time copy;
          12 compose-structure unit tests; smoke: throwaway /opt /data /etc
          roots, migration 0001→0011, 10-device inventory sync, probe DOCX,
          force-recreate of ALL containers → rows+file intact, /health ok,
          heartbeat fresh 10 s, 0600 secrets loaded with 0 failures)
W05-T002: fe493667724b5d6eea9c889ef68f73170542fc35 — REVIEW_PASSED
          (deploy/install.sh + deploy/lib.sh: exit codes 10–19 with
          diagnostics; Debian 13 amd64 + Docker/Compose checks; offline
          image checks + prod-compose pull_policy: never; /opt /data /etc
          created with perms; devices.toml/secrets.env initialized from
          docs examples only, no fake device secrets, existing wide-mode
          secrets refused (13) not tightened; alembic in-image; admin via
          env passthrough never argv; inventory sync; up --wait; health +
          heartbeat verification; idempotent re-run; staging root
          overrides; 6 new unit tests; smoke: fresh install on real
          Debian 13 roots-override host — 10 devices, admin scrypt-
          verifies, 0 password hits in logs, re-run preserved .env+admin,
          exit 12/13 failure paths)
W05-T003: 7b382e7ff9667ca072f3feabe232fe5a75d6941e — REVIEW_PASSED
          (deploy/update.sh: target image resolved + checked locally,
          previous .env kept as .env.bak, alembic on the NEW image,
          up -d --wait, wait_health/wait_heartbeat now return-status
          helpers; migration failure → .env restored + exit 14 with
          nothing restarted; restart/health/heartbeat failure → 17/18/19
          + exact rollback command; never touches /data or /etc; same-
          image re-run verifies in place; smoke: REAL 0009→0011 update
          from a W04-T001-commit-built image to a HEAD-built image, 10
          devices + 1 admin intact, raising-0012 image → exit 14 with old
          stack still healthy, missing image → exit 12, re-run idempotent)
W05-T004: 134ff1fe8101d9943d9c2f642cea432873614474 — REVIEW_PASSED
          (prod compose json-file 10MB×3 on all services — live-verified
          via docker inspect; tests/integration/test_retention_loop.py
          proves the worker §24 loop deletes >90d rows periodically,
          survives passes, honors stop, refuses sub-90-day config loudly;
          docs/OPERATIONS.md runbook (install/upgrade/rollback + pg_dump,
          health/heartbeat, job/retry/reconcile/install-pending/candidate,
          DEVICE_POLL PARTIAL/FAILED + failed_sections, SNMP/SSH, capacity,
          secrets perms, redaction; no secret values) pinned by
          test_operations_runbook.py; 300 unit + 243 integration PASS)
W05-T005: 1565c1005e616228181f6bff3109595d9aa256df — scaffolding only,
          NO REVIEW_PASSED
          (docs/ACCEPTANCE.md: items A01–A16 with pass criteria +
          evidence/manual-check templates, A15 closes W03-T010, A16
          closes W01-T007; scripts/acceptance_evidence.sh read-only
          collector — verified on a synthetic staging stack ONLY,
          explicitly not acceptance evidence; docs/evidence/README.md;
          A01..A16 all PENDING real environment)
```

## Wave 4 checkpoint ledger

```text
W04-AUDIT: 4a14b4ce022fae2e83bedb869d2161cc0961793d — REVIEW_PASSED
          (Stored XSS: aggregation_members/member_of esc()ed item by item
          in the relationship cell; report list joins the week's LATEST
          ReportJob of any status — pending/running/failed + job error,
          succeeded + install-pending note + diagnostic (read-model only,
          no Wave 3 state machine change); migration 0011 CHECK (singleton
          IS TRUE) + kept UNIQUE(singleton), ORM mirrored; 8 new
          integration tests)
W04-T001: 620d2af3323f3b97c7349652bd138bf24dd90336 — REVIEW_PASSED
          (migration 0009 users + uq_users_single_admin singleton index;
          auth/passwords.py salted scrypt scrypt$N$r$p$salt$hash with
          hmac.compare_digest; auth/admin.py initialize_admin/get_admin/
          verify_admin_login/set_admin_password; admin_cli.py init with
          env-or-getpass password entry, never argv/stdout/logs;
          plaintext never persisted; 8 unit + 8 integration tests)
W04-T002: 7ed12d02fd2ac5bd3579b25c0bf632a289cb683d — REVIEW_PASSED
          (migration 0010 sessions; server-side persistence with 7-day
          absolute + 12-hour idle bounds, idle-slide-only touch;
          backend/web: require_admin gate, CSRF double-submit on every
          POST, login/logout with fresh-token fixation defense and fixed
          no-enumeration error text, HttpOnly SameSite=Lax cookie;
          4 unit + 22 integration tests incl. timeouts/fixation/CSRF)
W04-T003: 83faeaa1bbefeeece955eb4d64b215dbf5cc4dea — REVIEW_PASSED
          (GET /reports list — week/period(周一至周日)/generated_at/status/
          last_error/download, newest first, behind require_admin;
          GET /reports/{week}/download — registry-validated only: strict
          ISO week code, canonical file name inside resolved report dir,
          symlink + traversal refusal, failed/missing 404;
          15 unit + 12 integration tests)
W04-T004: 9c535b486ec047d5257503b33808333d4d26f9c3 — REVIEW_PASSED
          (POST /reports/{week}/regenerate — thin CSRF-protected adapter
          onto the Wave 3 request_regenerate service (no duplicated job
          logic); duplicates yield exactly one active job (service +
          uq_report_jobs_active_week); regenerate column + per-row CSRF
          form on the list; active job state (排队中/进行中/等待重试)
          rendered next to the report status; incomplete week refused
          with one fixed note; 9 integration tests)
W04-T005: 8715fab0cace43fe270ee5ea92ec550dbcf3913d — REVIEW_PASSED
          (GET /interfaces — device selector + W02-T005 interface_overview
          read model: name/description/admin-oper/聚合关系/monitored;
          POST /interfaces/{id}/monitored — CSRF toggle via set_monitored
          with NO cascade (aggregate ≠ members), transaction owned by the
          route; unknown interface 404; 9 integration tests)
W04-GATE: bc98a3600eece2add11ad05358ca005ea4707e0d — PASS (engineering
          gate, 2026-09-06; full evidence in the TASK_GRAPH W04-GATE
          entry: 278 unit + 233 integration, ruff/mypy clean, alembic at
          0010, live login→priority-interface→list→download→regenerate→
          logout flow against the running container, no secret leakage)
W04 merge commit into main: 398204eb265091a211644ad772bc29a2ba878fa8
  (post-merge re-check on main: 278 unit PASS, ruff/mypy clean, alembic
  at 0010)
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
W03-AUDIT: 936b1583f46376cf043d28286910952e711a49d7 — REVIEW_PASSED
          (audit hotfix, no schema change / no migration: 1) report-job
          recovery re-runs on every loop pass and a lost terminal
          success/failure update requeues the job with the 10-minute
          next_retry_at — no permanent `running` stall without restart;
          2) a truly 0-device database renders 未配置设备/数据缺失 +
          数据完整性不足 with overall 关注 — never 正常, never 数据完整性
          满足要求; 3) counter Top 10 excludes total_delta-None interfaces
          while a genuine 0 total stays eligible; 4) an IRF week without a
          successful observation states IRF 成员状态数据缺失, never 无缺失,
          status still §19+Coverage; 12 new tests)
W03-AUDIT-2: 5bdc22e138950940146de87d84c65184bacaa75c — REVIEW_PASSED
          (audit follow-up, no schema change / no migration: a regenerate
          attempt renders a separate validated candidate DOCX and never
          touches the current report; the weekly_reports success commit
          precedes the atomic switch, so any DB failure leaves the old
          bytes untouched; a lost-update commit installs its candidate,
          and interrupted installs / abandoned candidates are resolved by
          reconcile_report_files on every loop pass; 6 new integration
          tests + 2 new unit tests)
W03-AUDIT-3: e5a9396b9b357b2106157401b0c91e935158b850 — REVIEW_PASSED
          (audit follow-up, no schema change / no migration: candidate
          DOCXs are job-bound (…docx.candidate.<job_id>) and
          reconcile_report_files installs a candidate only for the exact
          succeeded job behind the week's CURRENT success row
          (generated_at == finished_at) — an older success never
          authorizes a failed attempt's candidate and a superseded
          candidate never overwrites a newer success; a lost success
          reply resolves to SUCCEEDED (no FAILED downgrade, no retry); an
          install failure after the commit is diagnosable on job
          last_error with a succeeded_install_pending outcome and is
          completed by the next pass; 2 new + 4 rewritten integration
          tests, 1 rewritten + 1 extended unit test)
W03-AUDIT-4: 760bdd61ee328049f22fe9677aeecfedfd3097e1 — REVIEW_PASSED
          (audit follow-up, no schema change / no migration: 1) a
          success-commit state that cannot be determined (database
          unreachable for the verification query, recovered right after)
          resolves to commit_unknown — the job-bound candidate is kept,
          nothing is recorded, nothing is scheduled, the row keeps its
          state, the next pass's recovery/reconciliation arbitrates; the
          landed success can never be degraded to FAILED + 10-min retry
          again, and _mark_failure refuses to downgrade a succeeded job
          as a hard guard; 2) reconcile_report_files clears a stale
          install-pending last_error strictly from DB + directory when
          the job is the week's current success with its DOCX on disk
          and no candidate left, or a newer success superseded the job —
          the diagnosable state cannot outlive its lost clearing commit;
          2 new integration tests, 1 updated)
```

## W03-T010 / W03-GATE item (2026-09-05 instruction, superseded by Owner Decision 2026-09-06)

```text
W03-T010 — real weekly report manually checked against source samples:
  BLOCKED — REAL_WEEK_DATA_PENDING (unchanged). The regenerate service
  acceptance is implemented and tested; the real-report verification is
  impossible in this environment (no reachable real H3C device, dev
  database verified: 0 devices / 0 poll_runs / 0 metrics — same root
  cause as W01-T007). Owner instruction 2026-09-05: no fabricated
  acceptance; mark BLOCKED.
Owner Decision 2026-09-06 (supersedes the 2026-09-05 gate stop):
  Wave 3 engineering side passed W03-AUDIT-4; Wave 4 may proceed; the
  real weekly data manual DOCX check is deferred to W05-GATE.
  - W03-T010 now blocks W05-GATE only (no longer Wave 4).
  - W03-GATE = PASS — engineering gate (evidence below; the real-report
    manual check is carried by W03-T010 into W05-GATE).
  - W04-T004 depends on the REVIEW_PASSED regenerate service
    implementation, not on the real-data acceptance.
W03-GATE — PASS (engineering gate), re-verified green on work/wave-03 at
  ffe98d8 (post W03-AUDIT-4 docs commit): pytest 252 unit + 170
  integration PASS on migrated PostgreSQL 0001→0008; ruff clean; mypy
  clean (100 files); alembic upgrade head idempotent at 0008; dev compose
  smoke: web healthy (/health database ok), worker heartbeat fresh,
  device-poll + IRF + retention + weekly-report loops alive.
Deferred to W05-GATE: the real weekly data manual DOCX check (with
  W01-T007). It must NOT be recorded as passed anywhere before the real
  data exists.
```

## W04-AUDIT hotfix (2026-09-06)

```text
W04-AUDIT — REVIEW_PASSED (checkpoint 4a14b4ce022fae2e83bedb869d2161cc0961793d,
work/wave-04-audit; NOT merged into main — awaiting audit). Three audit
items fixed; one migration (0011), no other schema change:

1. Stored XSS (web/pages.py): the priority-interface relationship cell
   joined device-reported aggregation member / member-of display names
   straight into HTML. _interface_row now esc()es each name item by item
   BEFORE assembling 聚合接口（成员：…）/ 属于聚合：… — the only markup
   left in the cell is the trusted <br>. A stored <script> / <img
   onerror=…> interface or aggregation name renders only as escaped text
   in every position (name, description, BOTH relationship directions).
2. Report job / install-pending Web observability (web/reports.py +
   web/pages.py — Web read-model/presentation only; the Wave 3 job state
   machine, executor and reconciliation are untouched): the list no
   longer looks at ACTIVE jobs only. It joins each week's MOST RECENT
   ReportJob of ANY status and renders next to the report status:
     pending   → 重新生成排队中
     running   → 重新生成进行中
     failed    → 重新生成失败，等待自动重试 + sanitized ReportJob.last_error
     succeeded → nothing, UNLESS the current-DOCX switch is still owed
                 (install pending): the fixed note 新报告已提交，但文件切
                 换待恢复；当前下载仍可能是上一份成功报告 + the
                 install-pending diagnostic from job.last_error.
   The shared read-only definition of that state is the new
   reporting/jobs.py::is_install_pending predicate. All job errors are
   HTML-escaped like every page text. A cleanly succeeded job adds no
   noise; the previous success stays downloadable while a switch is owed.
3. Single-admin DB invariant (migration 0011 + db/models.py): the old
   UNIQUE(singleton) over a BOOLEAN admitted one true AND one false row —
   two administrator rows could coexist. ck_users_singleton_true
   (CHECK (singleton IS TRUE)) now runs alongside the kept
   uq_users_single_admin unique index: a second singleton=true row
   violates the unique index and a singleton=false row violates the
   CHECK, so the table can only ever hold one user whatever writes reach
   the database. The ORM mirrors the constraint. All pre-existing rows
   are singleton=true (initialize_admin never wrote otherwise), the
   migration applies safely over a populated table, and the pre-existing
   dev admin still logs in (live-verified).

Tests: +8 integration. test_web_interfaces.py: stored <script>/<img
onerror> names render ONLY escaped in name, description and both
relationship directions, raw tags never in the HTML. test_web_regenerate.py:
succeeded install-pending job shows the fixed note + diagnostic with the
old DOCX still downloadable; failed job shows 重新生成失败 + its
ReportJob.last_error HTML-escaped (raw tags absent); the week's LATEST
job drives the note (fresh pending supersedes an older install-pending);
a cleanly succeeded job renders no note. test_auth_admin.py: row-space
matrix (row 1 true OK / second true refused / false refused); a
pre-0011 user row still verifies and logs in; migration 0011 CHECK
present next to the unique index.
Evidence: pytest 278 unit + 241 integration PASS on migrated PostgreSQL
(0001→0011); ruff clean; mypy clean (122 files); `alembic upgrade head`
0010→0011 on the dev database and idempotent at 0011; image rebuilt +
compose smoke: web healthy (/health database ok), worker heartbeat
persisted and fresh (16 s), poll/IRF/weekly-report loops alive with zero
errors; live HTTP checks against the running container: injected
<script>/<img onerror> device text renders only escaped (raw count 0),
seeded succeeded install-pending job shows the fixed note + diagnostic on
/reports (evidence rows removed afterwards; dev admin password
re-initialized for the smoke login). W04-GATE revalidated PASS; Wave 5
NOT started; W03-T010 stays BLOCKED — REAL_WEEK_DATA_PENDING; W01-T007
stays BLOCKED — FIELD_VALIDATION_PENDING.
```

## W03-AUDIT hotfix (2026-09-06)

```text
W03-AUDIT — REVIEW_PASSED (checkpoint 936b1583f46376cf043d28286910952e711a49d7,
work/wave-03). Four audit items fixed; no schema change, no migration:
  1. Report job running 卡死: WeeklyReportLoop now recovers stranded
     `running` jobs at the start of EVERY pass (a failed recovery — e.g.
     database briefly down at boot — is retried by the following passes,
     §4.2/§27.9). When the terminal success/failure update itself is lost
     to a database outage, execute_report_job requeues the job as pending
     with next_retry_at = now + 10 min instead of leaving it `running`
     forever; if even the requeue fails, the next pass's recovery collects
     the stranded row. Worker keeps retrying every 10 minutes without a
     restart (§4.3). Tests: success-update lost → recorded failure; both
     terminal updates lost → requeued pending; requeue lost → recovered by
     a later pass; recovery failure → retried on next pass.
  2. Truly 0-device empty database: CoverageSummary treats an empty
     deployment as below-target (no coverage evidence can claim integrity);
     the summary states 未配置设备/数据缺失 + 数据完整性不足, the DOCX
     basic-info row shows the same, overall status is 关注 — never 正常 and
     never 数据完整性满足要求 (§6.2/§18.3). Coverage stays 数据缺失 (None),
     not 0%. Disabled devices with planned cycles keep the existing
     semantics (unchanged). Tests: 0-device service + unit DOCX + e2e DOCX.
  3. Counter Top 10: counter_delta_top_entries drops interfaces whose
     total_delta is None (no valid counter interval — 数据缺失 is not a
     rankable observation); a genuine total of 0 (constant counters) stays
     eligible. Corrected the old test that let a None-total interface fill
     a slot; added true-zero and fewer-than-10-rankable boundary tests.
  4. IRF 数据缺失摘要: a fabric with no successful in-week observation now
     yields the summary clause IRF 成员状态数据缺失（<devices>） instead of
     IRF 成员无缺失; mixed weeks report both 期末缺失 and 数据缺失 clauses;
     missing IRF evidence is still NOT an §19 异常 condition — overall
     status follows §19 + Coverage. Tests: service + unit DOCX + e2e DOCX.
Evidence: pytest 250 unit + 160 integration PASS on migrated PostgreSQL
(0001→0008); ruff clean; mypy clean (99 files); `alembic upgrade head`
idempotent at 0008 (no migration); image rebuilt + compose smoke: web
healthy (/health database ok), worker heartbeat persisted and fresh,
weekly-report loop + retention alive (dev secrets-missing cycle contained
per §27.9 as designed); live manual regenerate of 2026-W35 through the
§4.4 service inside the worker container produced the atomic 8-section
DOCX with the empty deployment honestly rendered (当前总体状态 关注,
Monitoring Coverage 摘要 = 未配置设备/数据缺失…数据完整性不足, no
数据完整性满足要求, no 正常).
W03-T010 / W03-GATE stay BLOCKED — REAL_WEEK_DATA_PENDING; Wave 4 not
started; W01-T007 still FIELD_VALIDATION_PENDING.
```

## W03-AUDIT-2 follow-up hotfix (2026-09-06)

```text
W03-AUDIT-2 — REVIEW_PASSED (checkpoint 5bdc22e138950940146de87d84c65184bacaa75c,
work/wave-03). One audit item fixed; no schema change, no migration:

Regenerate 文件/数据库一致性 (§4.4/§5): the old flow replaced the current
DOCX via os.replace BEFORE the success update; a database failure there
left the DB describing report A while the disk already held report B.
The flow is now two-phase and DB-first:
  1. render_report_candidate renders + validates (reopen + 8-heading
     check) a candidate DOCX under the deterministic side name
     network-weekly-report-<week>.docx.candidate in the same directory —
     the current report of the week is never touched by rendering;
  2. the weekly_reports success upsert (file_path = the current-report
     path) and the report_jobs terminal success commit;
  3. install_report atomically switches the candidate onto the current
     path (same-filesystem os.replace, §5) — only after a committed
     success, so ANY database failure happens while the previous bytes
     are still intact.
Failure handling:
  - success-update failure: the candidate is discarded (no failed
    attempt leaves files behind) — unless the row already carries this
    attempt's generated_at, i.e. the commit landed but its confirmation
    was lost to the same outage; then the candidate is the only copy of
    the succeeded report and is installed. When the database is still
    unreachable the candidate is left for reconciliation.
  - switch failure after the commit: the success stays terminal, the
    candidate survives, and reconcile_report_files (run at EVERY
    WeeklyReportLoop pass, before scheduling) completes the switch from
    the DB row — or discards candidates that have no success row.
  - ordinary render failure: no candidate ever exists; the old report
    and its success row are untouched (unchanged behavior, now pinned).
First generation (no old report), 10-minute retry cadence, one current
DOCX per week, atomic replace and one-active-job-per-week semantics are
unchanged.
Tests: tests/integration/test_report_file_consistency.py (+6): the
required end-to-end scenario (A success → regenerate → candidate B →
simulated success-update DB failure → job FAILED, weekly_reports still
A, current bytes == A, no candidate/temp residue → retry → bytes become
B only after the retry succeeds); lost success-reply commit still
installs its committed report; interrupted switch completed by the next
loop pass without duplicate scheduling; first-generation DB failure
leaves no files and retry creates the first DOCX; render failure keeps
the old report without a candidate; reconcile completes success rows
and discards/ignores non-report files. tests/unit/test_reporting_docx.py
(+2): candidate→install two-phase layout and candidate_week_code round
trip.
Evidence: pytest 252 unit + 166 integration PASS on migrated PostgreSQL
(0001→0008); ruff clean; mypy clean (100 files); `alembic upgrade head`
idempotent at 0008 (no migration); image rebuilt + compose smoke: web
healthy (/health database ok), worker heartbeat persisted and fresh,
IRF + weekly-report loops alive with zero errors; live manual regenerate
of 2026-W35 through the §4.4 service inside the worker container on the
new candidate flow: succeeded, 8 validated sections, exactly one current
DOCX in the reports volume (no candidate/temp residue), registry row
success pointing at it, empty deployment still rendered honestly (当前
总体状态 关注, 未配置设备/数据缺失, 数据完整性不足, no 数据完整性满足要求).
W03-T010 / W03-GATE stay BLOCKED — REAL_WEEK_DATA_PENDING; Wave 4 not
started; W01-T007 still FIELD_VALIDATION_PENDING.
```

## W03-AUDIT-3 follow-up hotfix (2026-09-06)

```text
W03-AUDIT-3 — REVIEW_PASSED (checkpoint e5a9396b9b357b2106157401b0c91e935158b850,
work/wave-03). One audit item fixed: report candidate/attempt consistency.
No schema change, no migration (the existing report_jobs.last_error field
carries the new diagnosable state).

1. Candidate 必须绑定具体 job/attempt (§4.4/§5, W03-AUDIT-3): the old
   deterministic per-week side name
   network-weekly-report-<week>.docx.candidate allowed ANY candidate of a
   week to be installed against ANY success row of that week — so after a
   database outage that swallowed an uncommitted regenerate's terminal
   update, the next pass's reconciliation installed the failed attempt's
   candidate over the still-current previous success A while the row kept
   describing A. Candidates are now job-bound:
   network-weekly-report-<week>.docx.candidate.<job_id>
   (render_report_candidate/render_report_docx take a required job_id),
   and reconcile_report_files authorizes an install only when ALL hold:
   the bound ReportJob exists, job.week_code == the candidate's week,
   job.status == succeeded, the weekly_reports row is the week's current
   success (status success, file_path set) AND row.generated_at ==
   job.finished_at — the exact pair _mark_success commits in one
   transaction. Therefore: a previous success A can never authorize a
   failed/running regeneration B's candidate; a candidate superseded by a
   newer success of the week is discarded, never installed over it; an
   unbound legacy candidate (pre-AUDIT-3 layout) and a candidate whose
   job row disappeared are discarded; foreign *.candidate files are
   still ignored. A discarded/installed succeeded job's install-pending
   last_error is cleared (nothing owed anymore).
2. lost-success-reply 修复: when _mark_success raised AFTER its commit
   landed (reply lost to the same outage), execute_report_job now
   verifies the attempt's exact commit (job succeeded with finished_at ==
   now AND the week's success row generated_at == now) and resolves the
   attempt to SUCCEEDED: the job's own candidate is installed, the job
   stays succeeded, next_retry_at stays NULL, due_jobs has nothing to
   retry — the job is never degraded back to FAILED and never given a
   10-minute retry. When the database is still unreachable (commit state
   undeterminable) the job-bound candidate is KEPT — safe either way,
   because reconciliation now resolves it strictly by job binding: a
   commit that actually landed is installed on a later pass, an
   uncommitted attempt's candidate is discarded and the recovered job
   re-enters the normal retry.
3. install 失败不再假装完整成功: an os.replace failure after the committed
   success keeps the success terminal, keeps the candidate, and is made
   diagnosable WITHOUT a schema change: report_jobs.last_error records
   "success committed but the current DOCX is not switched yet (install
   pending; reconciliation will retry)" and the executor's outcome is the
   distinct status succeeded_install_pending — the un-switched file is
   never reported as an unqualified success. The next pass's
   reconciliation completes the switch and clears last_error.
Tests: tests/integration/test_report_file_consistency.py (+2 new, 4
rewritten for job binding): the required outage scenario (A success →
regenerate B → B's candidate rendered → _mark_success uncommitted AND the
DB stays unreachable → candidate kept, job stuck running → next pass DB
recovered → reconciliation does NOT install B, current bytes still A,
registry still A, job requeued pending → B's retry succeeds and only then
the bytes become B); lost success reply → outcome SUCCEEDED, file
installed, job stays succeeded, next_retry_at NULL, due_jobs empty;
install OSError after the DB success → outcome succeeded_install_pending,
current still A, candidate kept, job.last_error diagnosable, no retry →
next pass reconcile installs B and clears last_error; stale candidate of
a superseded success discarded, newer success untouched; strict per-job
reconcile matrix (authorized install / superseded / never-committed /
orphaned job id / unbound legacy / foreign file); plus the retained
AUDIT-2 scenarios (success-update failure keeps old bytes until retry;
first-generation DB failure leaves no files; render failure leaves no
candidate). tests/unit/test_reporting_docx.py: candidate two-phase +
parsing tests updated/extended for job-bound names (parse_candidate_name,
is_unbound_candidate_name).
Evidence: pytest 252 unit + 168 integration PASS on migrated PostgreSQL
(0001→0008); ruff clean; mypy clean (100 files); `alembic upgrade head`
idempotent at 0008 (no migration); image rebuilt + compose smoke: web
healthy (/health database ok), worker heartbeat persisted and fresh
(24 s), poll/IRF/weekly-report loops alive (dev secrets-missing cycle
contained per §27.9 as designed); live manual regenerate of 2026-W35
inside the worker container on the job-bound candidate flow: succeeded,
8 validated sections, exactly one current DOCX in the reports volume (no
candidate/temp residue), registry row success pointing at it, job row
succeeded with last_error NULL.
W03-T010 / W03-GATE stay BLOCKED — REAL_WEEK_DATA_PENDING; Wave 4 not
started; W01-T007 still FIELD_VALIDATION_PENDING.
```

## W03-AUDIT-4 follow-up hotfix (2026-09-06)

```text
W03-AUDIT-4 — REVIEW_PASSED (checkpoint 760bdd61ee328049f22fe9677aeecfedfd3097e1,
work/wave-03). Two audit items fixed; no schema change, no migration.

1. unknown → FAILED 竞态修复 (§4.4): after a failed success update,
   execute_report_job classified the attempt via _success_commit_state
   (committed / absent / unknown) — but the "unknown" branch (database
   still unreachable for the verification query) STILL fell through to
   _record_failed_attempt. If the database recovered before the failure
   recorder ran while the success commit had actually landed (lost
   reply), the succeeded job was downgraded to FAILED + 10-minute retry.
   The state machine is now strict:
     committed  → install the candidate → SUCCEEDED / INSTALL_PENDING
     absent     → discard the candidate → FAILED + 10-min retry
     unknown    → keep the candidate, record NOTHING (no FAILED), schedule
                  NOTHING (no retry), leave the row in its current state —
                  the next pass's recovery (a stranded `running` row
                  re-enters the retry) and reconciliation (a landed
                  success gets its candidate installed) arbitrate.
   The unresolved attempt is reported as the distinct outcome status
   commit_unknown (never an ordinary FAILED, never an unqualified
   SUCCEEDED). Additionally _mark_failure now refuses to downgrade a
   succeeded job (logs and returns untouched) — a terminal success can
   never lose to a late failure record even if future call sites change.

2. install-pending 残留清理 (§4.4): the switch and the marker-clearing
   are two separate commits, so a database failure between them (the
   candidate's os.replace already succeeded, the last_error-clearing
   commit lost) left a succeeded job whose candidate is gone while the
   row still claims a switch is owed — and with no candidate left,
   candidate-based reconciliation could never see it again. The next
   pass's reconcile_report_files now also sweeps stale markers strictly
   from the database and the directory: a succeeded job carrying the
   install-pending last_error is cleared when the week's success row is
   exactly this job's commit (generated_at == finished_at) AND its
   recorded current DOCX exists on disk AND no candidate bound to the
   job remains — or when a strictly newer success of the week superseded
   the job (nothing can be owed anymore). A missing current DOCX keeps
   the marker: there the diagnosis is still true.

Tests: tests/integration/test_report_file_consistency.py (+2, 1 updated):
the required unknown-state scenario (A success → regenerate B → B's
success commit LANDS with a lost reply → exactly the commit-state query
hits a windowed database outage (commit state unknown) → the failure
recorder's database is already recovered → job still succeeded, finished_at
intact, next_retry_at NULL, no FAILED last_error, not in due_jobs,
candidate kept → a direct _mark_failure call is refused by the guard →
next pass reconcile installs B, current bytes change, registry still B's
commit, no duplicate job); the required residue scenario (install
OSError → diagnosable install-pending → reconcile completes the switch
but its marker-clearing commit is lost → current bytes are B with no
candidate while the row still says install pending → next healthy pass
clears the stale marker, bytes/registry untouched, nothing scheduled);
the outage-kept-candidate test now pins the unresolved commit_unknown
outcome with nothing recorded and nothing scheduled.
Evidence: pytest 252 unit + 170 integration PASS on migrated PostgreSQL
(0001→0008); ruff clean; mypy clean (100 files); `alembic upgrade head`
idempotent at 0008 (no migration); image rebuilt (docker build;
compose build is a silent no-op in this environment) + compose smoke:
web healthy (/health database ok), worker heartbeat persisted and fresh
(11 s), poll/IRF/weekly-report loops alive with zero errors; live manual
regenerate of 2026-W35 through the §4.4 service inside the worker
container on the new code: succeeded, 8 validated sections, exactly one
current DOCX in the reports volume (no candidate/temp residue), registry
row success pointing at it, job row succeeded with last_error NULL,
reconcile pass clean, empty deployment still rendered honestly (当前总体
状态 关注, 未配置设备/数据缺失, 数据完整性不足).
W03-T010 / W03-GATE stay BLOCKED — REAL_WEEK_DATA_PENDING; Wave 4 not
started; W01-T007 still FIELD_VALIDATION_PENDING.
```

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
W04-T005 (priority interface Web page) — REVIEW_PASSED:
  /interfaces selects a device, shows the W02-T005 overview read model
  (names, descriptions, admin/oper, 聚合关系) and toggles monitored via
  the service's single write path with CSRF; the aggregation toggle never
  touches members and members stay independently selectable (§11).
See "Wave 4 checkpoint ledger" above for the checkpoint SHA.
```

## Previous completed task (W04-T004)

```text
W04-T004 (manual regenerate Web action) — REVIEW_PASSED:
  the report list's 重新生成 form POSTs to /reports/{week}/regenerate
  behind require_admin + CSRF; the route adapts onto request_regenerate
  with no job logic of its own — duplicate submissions return the same
  active job, incomplete weeks are refused with a fixed note, and the
  active job state renders on the list while the old DOCX stays
  downloadable.
See "Wave 4 checkpoint ledger" above for the checkpoint SHA.
```

## Older completed task (W04-T003)

```text
W04-T003 (report list + DOCX download) — REVIEW_PASSED:
  /reports lists the weekly registry newest-first with week, 周一至周日
  period, generated_at (+0800), status, last_error and download; the
  download endpoint takes an ISO week code (never a path), validates it
  against the registry success row and the canonical file name inside the
  resolved report directory, and refuses traversal/symlink escapes,
  failed and missing reports with 404.
See "Wave 4 checkpoint ledger" above for the checkpoint SHA.
```

## Older completed task (W04-T002)

```text
W04-T002 (server-side session + login/logout) — REVIEW_PASSED:
  sessions table in PostgreSQL (row id = opaque bearer token); valid only
  inside BOTH the 7-day absolute and the 12-hour idle bound; login issues
  a fresh token (fixation defense) and rotates the CSRF cookie; logout
  destroys the row server-side; every protected page/action sits behind
  require_admin; every state-changing POST CSRF-checked first (403).
See "Wave 4 checkpoint ledger" above for the checkpoint SHA.
```

## Older completed task (W04-T001)

```text
W04-T001 (single administrator + salted scrypt password) — REVIEW_PASSED:
  migration 0009 users with the singleton unique index (§21 as a DB
  guarantee); auth/passwords.py self-describing salted scrypt hashes;
  auth/admin.py create/reinitialize/login-verify/password-rotate service;
  python -m backend.admin_cli init reading the password from env or
  getpass — never argv/stdout/logs; plaintext never persisted.
See "Wave 4 checkpoint ledger" above for the checkpoint SHA.
```

## Older completed task (W03-AUDIT-4)

```text
W03-AUDIT-4 (audit follow-up: unknown-commit-state race + install-pending
residue) — REVIEW_PASSED:
  1. A success-commit state that cannot be determined (database
     unreachable for the verification query, recovered right after) no
     longer falls through to the failure recorder: the attempt resolves
     to commit_unknown — candidate kept, no FAILED recorded, no retry
     scheduled, row state untouched; the next pass's recovery/
     reconciliation arbitrates. _mark_failure refuses to downgrade a
     succeeded job as a hard guard.
  2. reconcile_report_files clears a stale install-pending last_error
     strictly from DB + directory (the job is the week's current success
     with its DOCX on disk and no candidate left, or a newer success
     superseded the job) — the diagnosable state cannot outlive its lost
     clearing commit.
See "W03-AUDIT-4 follow-up hotfix (2026-09-06)" above for full evidence.
```

## Older completed task (W03-AUDIT-3)

```text
W03-AUDIT-3 (audit follow-up: report candidate/attempt consistency) —
REVIEW_PASSED:
  Candidate DOCXs are job-bound (…docx.candidate.<job_id>) and
  reconciliation installs a candidate only for the exact succeeded job
  behind the week's current success — an older success never authorizes
  a failed attempt's candidate; a lost success reply resolves to
  SUCCEEDED (no FAILED downgrade, no retry); an install failure after
  the commit is diagnosable (job last_error + succeeded_install_pending
  outcome) and is completed by the next pass.
See "W03-AUDIT-3 follow-up hotfix (2026-09-06)" above for full evidence.
```

## Older completed task (W03-AUDIT-2)

```text
W03-AUDIT-2 (audit follow-up: regenerate file/DB consistency) —
REVIEW_PASSED:
  A regenerate attempt now renders a separate validated candidate DOCX
  and the current file is switched only after the weekly_reports success
  commit — any database failure leaves the previous bytes untouched; a
  lost-update commit installs its candidate; interrupted installs and
  abandoned candidates are resolved by per-pass reconciliation.
See "W03-AUDIT-2 follow-up hotfix (2026-09-06)" above for full evidence.
```

## Older completed task (W03-AUDIT)

```text
W03-AUDIT (Wave 3 audit hotfix) — REVIEW_PASSED:
  1. Report job running 卡死: per-pass recovery retry + requeue on a lost
     terminal success/failure update; no permanent `running` stall without
     a restart; the 10-minute retry cadence survives database outages.
  2. Truly 0-device database: 未配置设备/数据缺失 + 数据完整性不足, overall
     关注 — never 正常, never 数据完整性满足要求; Coverage 数据缺失, not 0%.
  3. Counter Top 10: total_delta-None interfaces excluded; genuine 0 kept.
  4. IRF summary: no-observation weeks state IRF 成员状态数据缺失, never
     IRF 成员无缺失; overall status unchanged per §19 + Coverage.
See "W03-AUDIT hotfix (2026-09-06)" above for full evidence.
```

## Earlier completed task (W03-T010 engineering scope)

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

## Earlier completed task (Wave 2)

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
W05-AUDIT-2 checkpoint: 78f4e3b2ef64daaab6ca39993aa28068294e499d (work/wave-05)
W05-AUDIT checkpoint: 838b5b613f80ed60999018ca8e061b798e81a51e (work/wave-05) — REVIEW_FAILED, superseded by W05-AUDIT-2
W04-AUDIT checkpoint: 4a14b4ce022fae2e83bedb869d2161cc0961793d (work/wave-04-audit)
W04-GATE verification: bc98a3600eece2add11ad05358ca005ea4707e0d (work/wave-04)
W04-T005 checkpoint: 8715fab0cace43fe270ee5ea92ec550dbcf3913d (work/wave-04)
W04-T004 checkpoint: 9c535b486ec047d5257503b33808333d4d26f9c3 (work/wave-04)
W04-T003 checkpoint: 83faeaa1bbefeeece955eb4d64b215dbf5cc4dea (work/wave-04)
W04-T002 checkpoint: 7ed12d02fd2ac5bd3579b25c0bf632a289cb683d (work/wave-04)
W04-T001 checkpoint: 620d2af3323f3b97c7349652bd138bf24dd90336 (work/wave-04)
W03 merge into main: 131844461ade75c1517b1da91bd7d49bc1090521
W03-AUDIT-4 checkpoint: 760bdd61ee328049f22fe9677aeecfedfd3097e1 (work/wave-03)
W03-AUDIT-3 checkpoint: e5a9396b9b357b2106157401b0c91e935158b850 (work/wave-03)
W03-AUDIT-2 checkpoint: 5bdc22e138950940146de87d84c65184bacaa75c (work/wave-03)
W03-AUDIT checkpoint: 936b1583f46376cf043d28286910952e711a49d7 (work/wave-03)
W03-T010 checkpoint: 557165cee396361984a7b9c63e597d43cea60be8 (work/wave-03)
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
  acceptance only; the regenerate service is implemented and tested and
  does NOT block Wave 4). Same root cause as W01-T007: no reachable real
  device, hence no real weekly data. Blocks W05-GATE (Owner Decision
  2026-09-06). Close by collecting one complete real week and manually
  checking the DOCX against source samples.
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
None started — Wave 4 is COMPLETE (W04-GATE PASS); Wave 5 was NOT
started per instruction. When authorized: W05-T001/W05-T004 depend on
W04-GATE (now PASS).
Outstanding release blockers (W05-GATE): W03-T010
REAL_WEEK_DATA_PENDING and W01-T007 FIELD_VALIDATION_PENDING.
```

## Current Wave Gate

```text
W04-GATE — Operations Web Gate: PASS (engineering gate, 2026-09-06;
revalidated PASS after W04-AUDIT on migrated PostgreSQL 0001→0011:
pytest 278 unit + 241 integration PASS, ruff/mypy clean, alembic at
0011, compose smoke + live XSS/install-pending HTTP checks green — see
the W04-AUDIT hotfix section above).
Original gate evidence: pytest 278 unit + 233 integration PASS on
migrated PostgreSQL (0001→0010); ruff clean; mypy clean (122 files);
alembic upgrade head idempotent at 0010; image rebuilt + compose smoke
on the new image: web healthy (/health database ok), worker heartbeat
persisted and fresh, device-poll loop alive. Live end-to-end flow
against the running web container: login (HttpOnly/SameSite=Lax session
cookie) -> priority-interface configuration with live no-cascade
verification (aggregate=t, members=f; then a member=t independently) ->
report list (2026-W35) -> download (valid 8-section DOCX, attachment
headers) -> regenerate (web form POST created one manual job; the worker
loop executed it to succeeded with one current DOCX, no residue) ->
logout (303; cookie replay 303; the sessions row destroyed). Password
grep count in web+worker logs: 0.
W03-GATE remains PASS (engineering gate); W03-T010
REAL_WEEK_DATA_PENDING and W01-T007 FIELD_VALIDATION_PENDING remain
BLOCKED and block W05-GATE only.
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
