"""W05-T002/T003: deployment scripts (deploy/*.sh) structural guarantees.

The install/update flow runs on an air-gapped Debian host, so these tests
pin the properties that must hold before it ever gets there: the scripts
stay syntactically valid bash, never fetch anything from the network, hand
the administrator password through the environment only (never argv) and
keep the explicit exit-code contract.
"""

import re
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def _bash_syntax_ok(script: Path) -> bool:
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, check=False
    )
    return result.returncode == 0


def test_scripts_are_valid_bash() -> None:
    for name in ("lib.sh", "install.sh", "update.sh"):
        script = DEPLOY / name
        assert script.exists(), name
        assert _bash_syntax_ok(script), name
    evidence = DEPLOY.parent / "scripts" / "acceptance_evidence.sh"
    assert evidence.exists()
    assert _bash_syntax_ok(evidence)


def test_scripts_are_fail_fast_and_never_touch_the_network() -> None:
    for name in ("install.sh", "update.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "set -euo pipefail" in text, name
        for forbidden in ("curl ", "wget", "apt-get", "apt install", "docker pull", "pip install"):
            assert forbidden not in text, f"{name}: {forbidden!r}"


def test_admin_password_never_enters_argv() -> None:
    install = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    # The `-e VAR=value` form would place the password in the docker CLI's
    # argv; only environment passthrough (`-e VAR` + export) is allowed.
    assert "-e NETWORK_REPORT_ADMIN_PASSWORD=" not in install
    assert install.count("export NETWORK_REPORT_ADMIN_PASSWORD=") >= 1
    assert "read -rs" in install  # prompted entry does not echo


def test_lib_defines_explicit_exit_codes_and_verifiers() -> None:
    lib = (DEPLOY / "lib.sh").read_text(encoding="utf-8")
    for code in range(10, 20):
        assert re.search(rf"#\s+{code}\s", lib), code
    for needed in ("verify_health()", "verify_heartbeat()", "check_docker()", "check_images()"):
        assert needed in lib, needed


def test_install_covers_the_spec_26_2_flow() -> None:
    install = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    for needed in (
        "Debian 13",
        "dpkg --print-architecture",
        "check_docker",
        "alembic upgrade head",
        "backend.admin_cli init",
        "backend.inventory_sync",
        "verify_health",
        "verify_heartbeat",
        'secrets.env must have permission 0600',
    ):
        assert needed in install, needed


def test_update_covers_the_spec_26_3_flow_without_destroying_state() -> None:
    update = (DEPLOY / "update.sh").read_text(encoding="utf-8")
    for needed in (
        "check_images",
        "alembic upgrade head",
        "up -d --wait",
        "wait_health",
        "wait_heartbeat",
        ".env.bak",  # previous .env kept for rollback
    ):
        assert needed in update, needed
    # A failed migration must restore the previous .env and restart nothing.
    assert "migration failed — .env restored" in update
    # Nothing in the update path may delete persistent state.
    assert re.search(r"(?<![-\w])rm\s", update) is None


def test_heartbeat_verifier_targets_only_the_current_worker() -> None:
    """W05-AUDIT fix 1: the verifier must demand a NEW post-start heartbeat.

    The old `select(...).limit(1)` check accepted ANY worker's fresh row, so
    a heartbeat left behind by the previous worker passed as evidence that
    the new worker had started.
    """

    lib = (DEPLOY / "lib.sh").read_text(encoding="utf-8")
    install = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    update = (DEPLOY / "update.sh").read_text(encoding="utf-8")

    # The tested module is the authoritative implementation; the inline
    # fallback (rollback to pre-W05-AUDIT images) applies the same rules.
    assert "backend.ops.heartbeat_verify" in lib
    assert "select(WorkerHeartbeat.last_heartbeat).limit(1)" not in lib
    assert lib.count("WorkerHeartbeat.worker_id == worker_id") >= 2, (
        "module invocation fallback and inline fallback must both filter by worker_id"
    )
    for helper in ("heartbeat_baseline()", "wait_heartbeat()"):
        assert helper in lib, helper
    # The baseline is captured BEFORE the stack is (re)started and passed to
    # the verifier — in both install.sh and update.sh.
    assert install.index("HEARTBEAT_BASELINE=$(heartbeat_baseline)") < install.index(
        'step "starting web + worker"'
    )
    assert 'verify_heartbeat "$HEARTBEAT_BASELINE"' in install
    assert update.index("HEARTBEAT_BASELINE=$(heartbeat_baseline)") < update.index(
        'step "restarting web + worker'
    )
    assert 'wait_heartbeat "$HEARTBEAT_BASELINE"' in update


def test_heartbeat_verdict_is_bound_to_worker_container_identity() -> None:
    """W05-AUDIT-2: the replacement decision must come from the worker's
    real Docker container identity before/after the (re)start — never from
    comparing image names — and a missing post-start identity must fail the
    verification instead of defaulting to "same container"."""

    lib = (DEPLOY / "lib.sh").read_text(encoding="utf-8")
    install = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    update = (DEPLOY / "update.sh").read_text(encoding="utf-8")

    # Real container identity: the FULL `docker inspect` ID, "" when absent;
    # a failed Docker query is an error, not "".
    assert "worker_container_id()" in lib
    assert "docker inspect --format '{{.Id}}'" in lib
    assert "COMPOSE ps -q worker" in lib

    # Pre/post identities are captured around the (re)start in both scripts.
    for script, start_step in (
        (install, 'step "starting web + worker"'),
        (update, 'step "restarting web + worker'),
    ):
        pre = script.index("PRE_WORKER_CONTAINER_ID=$(worker_container_id)")
        post = script.index("POST_WORKER_CONTAINER_ID=$(worker_container_id)")
        assert pre < script.index(start_step) < post, start_step

    # install.sh routes through the die-ing verifier with all three args.
    assert (
        'verify_heartbeat "$HEARTBEAT_BASELINE" '
        '"$PRE_WORKER_CONTAINER_ID" "$POST_WORKER_CONTAINER_ID"' in install
    )
    # update.sh routes through the returning waiter (post-migration failure
    # keeps the schema-aware 19 path).
    assert (
        'wait_heartbeat "$HEARTBEAT_BASELINE" '
        '"$PRE_WORKER_CONTAINER_ID" "$POST_WORKER_CONTAINER_ID"' in update
    )
    # A missing post-start identity is a hard failure in BOTH paths — never
    # a silent "same container".
    assert "worker container identity could not be determined" in lib
    assert "worker container identity could not be determined" in update


def test_heartbeat_verifier_fallback_shares_the_race_invariants() -> None:
    """W05-AUDIT-2 + W05-PRE-ACCEPTANCE-HARDENING: the inline fallback
    (rollback targets predating the W05-AUDIT-2 verifier) must decide
    replacement from the pre/post container IDs and demand an advanced
    started_at exactly like the tested module — a last_heartbeat newer than
    the baseline alone must never pass a replaced container, and a no-row
    baseline must be judged from captured_at (fail closed without it)."""

    lib = (DEPLOY / "lib.sh").read_text(encoding="utf-8")

    # The capability probe: only a verifier that ACCEPTS the pre/post IDs
    # may decide; older modules (usage exit 2 / missing module) fall back.
    assert "heartbeat_verify_accepts_container_ids()" in lib
    assert "verify '{' pre post" in lib

    # The fallback compares container identities and started_at — the two
    # invariants that close the old-worker-after-baseline race.
    fallback = lib.split('python - verify "$baseline" "$pre_id" "$post_id"')[1]
    fallback = fallback.split("\nPY\n")[0]  # the heredoc body only
    assert "pre_id != post_id" in fallback
    assert 'started_at' in fallback
    assert 'baseline_row["started_at"]' in fallback
    assert "started <= baseline_started" in fallback
    assert "replacement worker" in fallback
    # ...and still refuses a stale/identical row and a missing own row.
    assert "wrote no NEW heartbeat after the baseline" in fallback
    assert "no heartbeat row for worker" in fallback
    # HARDENING no-row rules, mirrored from the tested module: with no
    # baseline row, a replaced container needs started_at past captured_at,
    # a legacy baseline without captured_at fails closed, and only a fresh
    # install (no PRE container) passes on the bare first row.
    assert 'baseline["captured_at"]' in fallback
    assert "started <= captured_at" in fallback
    assert "no captured_at" in fallback
    assert "fresh install" in fallback
    assert "worker container unchanged" in fallback

    # The baseline itself always records captured_at — in the tested module
    # AND in the legacy fallback that serves pre-W05-AUDIT images.
    baseline_fallback = lib.split("heartbeat_verify baseline")[1]
    baseline_fallback = baseline_fallback.split('python - baseline <<\'PY\'')[1]
    baseline_fallback = baseline_fallback.split("\nPY\n")[0]
    assert '"captured_at"' in baseline_fallback


def test_post_migration_failure_paths_are_schema_aware() -> None:
    """W05-AUDIT fix 3 + W05-PRE-ACCEPTANCE-HARDENING: after a committed
    migration, roll-back advice must carry the schema-compatibility caveat
    instead of an unconditional image rollback hint — and EVERY failure past
    `alembic upgrade head` (including the heartbeat-baseline and the POST
    worker-container-identity captures) must route through the schema-aware
    path, never a bare die 17/18/19."""

    lib = (DEPLOY / "lib.sh").read_text(encoding="utf-8")
    update = (DEPLOY / "update.sh").read_text(encoding="utf-8")

    # The mandated remediation text lives in the shared helper.
    for needed in (
        "Database migration has already committed.",
        "The schema may no longer be compatible with the previous image.",
        "Do NOT blindly roll back the application image.",
        "See docs/OPERATIONS.md rollback procedure.",
        "restore the pre-update database backup first, then start the old image.",
    ):
        assert needed in lib, needed

    # Every failure AFTER the migration committed is schema-aware: from the
    # migration step onwards update.sh must contain no bare die 17/18/19.
    migration = update.index('step "database migration')
    tail = update[migration:]
    assert re.search(r"\bdie 1[789]", tail) is None
    # The migration-failure path itself keeps die 14 (.env restore, old
    # stack untouched) — the only bare die left in the migration tail.
    assert "die 14" in tail
    # All post-migration failure sites (restart 17, POST identity query 19,
    # missing POST identity 19, health 18, heartbeat 19, baseline/pre-ID
    # captures 19) route through the helper.
    assert update.count("post_migration_failure") >= 7
    assert (
        'POST_WORKER_CONTAINER_ID=$(worker_container_id) \\\n'
        '    || post_migration_failure 19' in update
    )

    # The unconditional hint is gone from update.sh entirely — including the
    # final "update complete" message, which must stay schema-qualified.
    assert "rollback with:" not in update
    assert "compatible with the" in update
    assert "restore the" in update


def test_post_migration_failure_function_prints_schema_aware_remediation() -> None:
    """The real helper, executed: prints the mandated block, exits with the
    given code, and never prints an unconditional rollback command."""

    lib = DEPLOY / "lib.sh"
    for code in ("17", "18", "19"):
        result = subprocess.run(
            ["bash", "-c", f'source "{lib}"; post_migration_failure {code} "detail text"'],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == int(code), code
        stderr = result.stderr
        assert "FATAL" in stderr and "detail text" in stderr, code
        assert "Database migration has already committed." in stderr, code
        assert "Do NOT blindly roll back the application image." in stderr, code
        assert "restore the pre-update database backup first" in stderr, code
        assert "rollback with" not in stderr, code
