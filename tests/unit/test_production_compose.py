"""W05-T001: production deployment layout (deploy/docker-compose.prod.yml).

The production Compose file is part of the deliverable, so its structure is
pinned by tests: services fixed to web/worker/postgres (§25), one shared
pre-built application image, all persistence on host bind mounts under the
data directory (no named volumes — DB and DOCX files must survive container
replacement), and the 0600 device-secrets file mounted read-only into the
worker only (§22.2).
"""

from pathlib import Path

import yaml

DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy"
COMPOSE = DEPLOY_DIR / "docker-compose.prod.yml"
README = DEPLOY_DIR / "README.md"


def _load() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_services_are_exactly_web_worker_postgres() -> None:
    services = _load()["services"]
    assert set(services) == {"web", "worker", "postgres"}


def test_web_and_worker_share_one_prebuilt_image_and_nothing_builds() -> None:
    services = _load()["services"]
    app_image = "${NETWORK_REPORT_APP_IMAGE:?set NETWORK_REPORT_APP_IMAGE in .env}"
    for name in ("web", "worker"):
        assert services[name]["image"] == app_image, name
        assert "build" not in services[name], name
    assert "build" not in services["postgres"]
    # Offline runtime: the pinned PostgreSQL tag must never be `latest`.
    assert services["postgres"]["image"] == "postgres:17-alpine"


def test_missing_images_never_trigger_a_registry_pull() -> None:
    for name, service in _load()["services"].items():
        assert service["pull_policy"] == "never", name


def test_no_named_volumes_anything_persistent_is_a_host_bind() -> None:
    compose = _load()
    # The development `reports` named volume must not reappear here: every
    # persistent location is a host path so container replacement cannot
    # lose it (W05-T001 acceptance).
    assert "volumes" not in compose


def test_postgres_data_lives_under_the_data_directory() -> None:
    volumes = _load()["services"]["postgres"]["volumes"]
    assert len(volumes) == 1
    # rsplit: the interpolation default marker `:?...` inside ${...} contains
    # a colon, so the split point is the LAST one.
    host, container = volumes[0].rsplit(":", 1)
    assert host.startswith("${NETWORK_REPORT_DATA_DIR") and host.endswith("/postgres")
    assert container == "/var/lib/postgresql/data"


def test_report_files_bind_mounted_for_web_and_worker() -> None:
    for name in ("web", "worker"):
        volumes = _load()["services"][name]["volumes"]
        reports = [v for v in volumes if v.endswith(":/var/lib/network-report/reports")]
        assert len(reports) == 1, name
        assert reports[0].startswith("${NETWORK_REPORT_DATA_DIR")
        assert reports[0].endswith("/reports:/var/lib/network-report/reports")


def test_worker_mounts_inventory_and_secrets_readonly() -> None:
    volumes = _load()["services"]["worker"]["volumes"]
    for file_name in ("devices.toml", "secrets.env"):
        mounts = [
            v
            for v in volumes
            if v.endswith(f":/etc/network-report/{file_name}:ro")
        ]
        assert len(mounts) == 1, file_name
        assert mounts[0].startswith("${NETWORK_REPORT_CONFIG_DIR")


def test_web_gets_inventory_but_never_device_secrets() -> None:
    volumes = _load()["services"]["web"]["volumes"]
    # Host-side one-off commands (install.sh inventory sync) run on the web
    # service and need the non-sensitive inventory file (§22.1) — but never
    # the device credentials.
    inventory = [
        v for v in volumes if v.endswith(":/etc/network-report/devices.toml:ro")
    ]
    assert len(inventory) == 1
    assert not any("secrets.env" in v for v in volumes)


def test_all_services_restart_automatically() -> None:
    for name, service in _load()["services"].items():
        assert service["restart"] == "unless-stopped", name


def test_web_and_postgres_have_real_healthchecks() -> None:
    services = _load()["services"]
    assert "/health" in str(services["web"]["healthcheck"]["test"])
    assert "pg_isready" in str(services["postgres"]["healthcheck"]["test"])


def test_app_runs_in_production_mode_with_container_paths() -> None:
    for name in ("web", "worker"):
        env = _load()["services"][name]["environment"]
        assert env["NETWORK_REPORT_ENVIRONMENT"] == "production", name
        # Inside the container the data dir is always this path; the host
        # bind source is what /opt/network-report/.env chooses.
        assert env["NETWORK_REPORT_DATA_DIR"] == "/var/lib/network-report", name


def test_postgres_is_not_published_to_the_host() -> None:
    # The dev compose publishes a loopback port for host-side test runs;
    # production has no such need and must not expose the database.
    assert "ports" not in _load()["services"]["postgres"]


def test_readme_documents_layout_persistence_and_dev_difference() -> None:
    text = README.read_text(encoding="utf-8")
    for required in (
        "/opt/network-report",
        "/data/network-report",
        "/etc/network-report",
        "0600",
        "network-weekly_reports",  # the one-time copy from the dev volume
        "docker image inspect",
    ):
        assert required in text, required
