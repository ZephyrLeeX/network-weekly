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


def test_post_migration_failure_paths_are_schema_aware() -> None:
    """W05-AUDIT fix 3: after a committed migration, roll-back advice must
    carry the schema-compatibility caveat instead of an unconditional image
    rollback hint."""

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
    # All three post-migration failure paths (17/18/19) route through it.
    assert update.count("post_migration_failure") >= 3
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
