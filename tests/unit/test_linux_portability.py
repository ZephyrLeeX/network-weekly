"""Execute real host preflight helpers with an isolated command PATH.

These are synthetic capabilities, never Ubuntu real-host smoke evidence.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "deploy/lib.sh"
BASH = shutil.which("bash") or "/bin/bash"
COMMANDS = (
    "bash dirname docker uname grep cut cat seq sleep id mkdir install chmod chown stat od tr "
    "cp sed date ls"
).split()


@pytest.fixture
def host(tmp_path: Path) -> dict[str, str]:
    for cmd in COMMANDS:
        target = shutil.which(cmd)
        if target:
            (tmp_path / cmd).symlink_to(target)
    for cmd, body in {
        "uname": 'case "$1" in -s) echo "${KERNEL:-Linux}";; *) echo "${ARCH:-x86_64}";; esac',
        "docker": '''case "$*" in
info) exit "${DAEMON_EXIT:-0}";;
"compose version")
    echo "Docker Compose version ${COMPOSE_VERSION:-2.39.1}"
    exit "${COMPOSE_EXIT:-0}"
    ;;
"compose up --help")
    [[ ${UP_HELP_EXIT:-0} == 0 ]] || exit "$UP_HELP_EXIT"
    echo "Usage: docker compose up [OPTIONS]"
    [[ ${HAS_WAIT:-1} == 1 ]] && echo "      --wait  Wait for services to be running|healthy"
    [[ ${HAS_WAIT_TIMEOUT:-0} == 1 ]] && echo "      --wait-timeout int  Wait timeout"
    ;;
"compose ps --help")
    [[ ${PS_HELP_EXIT:-0} == 0 ]] || exit "$PS_HELP_EXIT"
    echo "Usage: docker compose ps [OPTIONS]"
    [[ ${HAS_STATUS:-1} == 1 ]] && echo "      --status stringArray  Filter services by status"
    [[ ${HAS_STATUS_FILTER:-0} == 1 ]] && echo "      --status-filter string  Filter"
    ;;
image\\ inspect*) exit "${IMAGE_EXIT:-0}";;
esac
exit 0''',
    }.items():
        path = tmp_path / cmd
        path.unlink(missing_ok=True)
        path.write_text(f"#!{BASH}\n{body}\n")
        path.chmod(0o755)
    return {"PATH": str(tmp_path), "NETWORK_REPORT_APP_IMAGE": "synthetic:test"}


def run(host: dict[str, str], script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, "-c", f'set -euo pipefail; source "{LIB}"; {script}'],
        env=host,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("profile", ["install", "update", "evidence"])
@pytest.mark.parametrize("arch", ["x86_64", "amd64"])
def test_complete_linux_capabilities_pass(host: dict[str, str], profile: str, arch: str) -> None:
    host["ARCH"] = arch
    result = run(
        host,
        f"check_linux_host; check_host_commands {profile}; check_architecture; "
        "check_docker; check_images",
    )
    assert result.returncode == 0, result.stderr
    assert "linux/amd64" in result.stdout
    assert not (Path(host["PATH"]) / "dpkg").exists()
    assert not (Path(host["PATH"]) / "rpm").exists()


@pytest.mark.parametrize(
    ("key", "value", "code", "message"),
    [
        ("KERNEL", "Darwin", 10, "production deployment requires a Linux host"),
        ("KERNEL", "FreeBSD", 10, "production deployment requires a Linux host"),
        ("ARCH", "aarch64", 10, "current release requires linux/amd64"),
        ("DAEMON_EXIT", "1", 11, "Docker Engine is not usable"),
        ("COMPOSE_EXIT", "1", 11, "Docker Compose plugin is unavailable"),
        ("HAS_WAIT", "0", 11, "docker compose up --wait"),
        ("HAS_STATUS", "0", 11, "docker compose ps --status"),
        ("IMAGE_EXIT", "1", 12, "not present locally"),
    ],
)
def test_preflight_failure_contract(
    host: dict[str, str], key: str, value: str, code: int, message: str
) -> None:
    host[key] = value
    result = run(
        host,
        "check_linux_host; check_host_commands install; check_architecture; "
        "check_docker; check_images",
    )
    assert result.returncode == code
    assert message in result.stderr


@pytest.mark.parametrize("version", ["2.39.1", "5.5.1", "99.0.0"])
def test_compose_major_version_is_irrelevant_when_capabilities_exist(
    host: dict[str, str], version: str
) -> None:
    host["COMPOSE_VERSION"] = version
    result = run(host, "check_docker")
    assert result.returncode == 0, result.stderr


def test_only_standalone_docker_compose_is_rejected(
    host: dict[str, str], tmp_path: Path
) -> None:
    docker = Path(host["PATH"]) / "docker"
    docker.unlink()
    standalone = Path(host["PATH"]) / "docker-compose"
    standalone.write_text(f"#!{BASH}\necho 'Docker Compose version v2.39.1'\n")
    standalone.chmod(0o755)

    result = run(host, "check_docker")

    assert result.returncode == 11
    assert "Docker Engine is not usable" in result.stderr
    assert standalone.exists()


@pytest.mark.parametrize(
    ("missing", "similar", "message"),
    [
        ("HAS_WAIT", "HAS_WAIT_TIMEOUT", "docker compose up --wait"),
        ("HAS_STATUS", "HAS_STATUS_FILTER", "docker compose ps --status"),
    ],
)
def test_similar_option_does_not_replace_required_capability(
    host: dict[str, str], missing: str, similar: str, message: str
) -> None:
    host[missing] = "0"
    host[similar] = "1"

    result = run(host, "check_docker")

    assert result.returncode == 11
    assert message in result.stderr


@pytest.mark.parametrize("missing", ["install", "docker", "stat", "uname", "od"])
def test_missing_command_named(host: dict[str, str], missing: str) -> None:
    (Path(host["PATH"]) / missing).unlink()
    result = run(host, "check_linux_host; check_host_commands install")
    assert result.returncode == 10
    assert f"required host command is missing: {missing}" in result.stderr


@pytest.mark.parametrize(
    ("distro", "version"), [("ubuntu", "24.04"), ("debian", "13"), ("my-internal-linux", "1")]
)
def test_distro_metadata_never_controls_preflight(
    host: dict[str, str], distro: str, version: str
) -> None:
    # All potential os-release variables are irrelevant. Pin absence of the
    # system file access too: these functions cannot override them by sourcing it.
    host.update(ID=distro, VERSION_ID=version, ID_LIKE=distro)
    for file in (LIB, ROOT / "deploy/install.sh", ROOT / "deploy/update.sh"):
        text = file.read_text()
        for forbidden in ("os-release", "VERSION_ID", "ID_LIKE", "dpkg", "rpm"):
            assert forbidden not in text
    result = run(
        host,
        "check_linux_host; check_host_commands install; check_architecture; "
        "check_docker; check_images",
    )
    assert result.returncode == 0, result.stderr


def test_evidence_runs_without_package_tools(host: dict[str, str]) -> None:
    result = subprocess.run(
        [BASH, str(ROOT / "scripts/acceptance_evidence.sh")],
        env=host,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "informational only" in result.stdout
    assert "dpkg" not in (ROOT / "scripts/acceptance_evidence.sh").read_text()


def test_install_reports_filesystem_failure(host: dict[str, str], tmp_path: Path) -> None:
    # Real mkdir must fail under a regular file, before writing any deployment file.
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked")
    host.update(
        NETWORK_REPORT_INSTALL_ROOT=str(blocked),
        NETWORK_REPORT_DATA_ROOT=str(tmp_path / "data"),
        NETWORK_REPORT_CONFIG_ROOT=str(tmp_path / "etc"),
    )
    id_cmd = Path(host["PATH"]) / "id"
    id_cmd.unlink()
    id_cmd.write_text(f"#!{BASH}\necho 1000\n")
    id_cmd.chmod(0o755)
    result = subprocess.run(
        [BASH, str(ROOT / "deploy/install.sh")],
        env=host,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 13
    assert "filesystem/config operation failed" in result.stderr


def test_all_production_scripts_remain_offline() -> None:
    import re

    for path in [*ROOT.glob("deploy/*.sh"), ROOT / "scripts/acceptance_evidence.sh"]:
        text = path.read_text()
        assert not re.search(
            r"(?m)^\s*(?:sudo\s+)?(?:curl|wget|apt|apt-get|dnf|yum|apk|pacman)\s", text
        )
        assert "docker pull" not in text
        assert "pip install" not in text
