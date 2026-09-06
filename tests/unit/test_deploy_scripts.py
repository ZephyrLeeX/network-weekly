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
