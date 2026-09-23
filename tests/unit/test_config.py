"""Unit tests for runtime configuration (W00-T005)."""

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.config import DEFAULT_DATABASE_URL, ConfigError, load_settings


def test_defaults_are_foundation_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "DATABASE_URL",
        "NETWORK_REPORT_ENVIRONMENT",
        "NETWORK_REPORT_DATA_DIR",
        "NETWORK_REPORT_TIMEZONE",
        "NETWORK_REPORT_LOG_LEVEL",
        "NETWORK_REPORT_WORKER_ID",
        "NETWORK_REPORT_HEARTBEAT_INTERVAL_SECONDS",
        "NETWORK_REPORT_RETENTION_DAYS",
        "NETWORK_REPORT_POLL_INTERVAL_SECONDS",
        "NETWORK_REPORT_IRF_INTERVAL_SECONDS",
        "NETWORK_REPORT_POLL_MAX_WORKERS",
        "NETWORK_REPORT_POLL_STAGGER_SECONDS",
        "NETWORK_REPORT_POLL_DEADLINE_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = load_settings()

    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.environment == "development"
    assert settings.data_dir == Path("./data")
    assert settings.report_dir == Path("./data/reports")
    assert settings.timezone == ZoneInfo("Asia/Shanghai")
    assert str(settings.timezone) == "Asia/Shanghai"
    assert settings.log_level == "INFO"
    assert settings.worker_id == "worker"
    assert settings.heartbeat_interval_seconds == 30
    # §24: raw data is kept 90 days by default.
    assert settings.retention_days == 90
    assert settings.poll_interval_seconds == 1800
    assert settings.irf_interval_seconds == 1800
    assert settings.poll_max_workers == 3
    assert settings.poll_stagger_seconds == 20
    assert settings.poll_deadline_seconds == 240


def test_timezone_is_explicit_not_inherited_from_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even with TZ pointing elsewhere, business timezone stays Asia/Shanghai."""

    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.delenv("NETWORK_REPORT_TIMEZONE", raising=False)

    assert load_settings().timezone == ZoneInfo("Asia/Shanghai")


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.example:5432/other")
    monkeypatch.setenv("NETWORK_REPORT_ENVIRONMENT", "production")
    monkeypatch.setenv("NETWORK_REPORT_DATA_DIR", "/var/lib/network-report")
    monkeypatch.setenv("NETWORK_REPORT_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("NETWORK_REPORT_LOG_LEVEL", "debug")
    monkeypatch.setenv("NETWORK_REPORT_WORKER_ID", "worker-7")
    monkeypatch.setenv("NETWORK_REPORT_HEARTBEAT_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("NETWORK_REPORT_POLL_INTERVAL_SECONDS", "3600")
    monkeypatch.setenv("NETWORK_REPORT_IRF_INTERVAL_SECONDS", "1200")
    monkeypatch.setenv("NETWORK_REPORT_POLL_MAX_WORKERS", "5")
    monkeypatch.setenv("NETWORK_REPORT_POLL_STAGGER_SECONDS", "7")
    monkeypatch.setenv("NETWORK_REPORT_POLL_DEADLINE_SECONDS", "300")

    settings = load_settings()

    assert settings.database_url == "postgresql+psycopg://u:p@db.example:5432/other"
    assert settings.environment == "production"
    assert settings.data_dir == Path("/var/lib/network-report")
    assert settings.report_dir == Path("/var/lib/network-report/reports")
    assert settings.log_level == "DEBUG"
    assert settings.worker_id == "worker-7"
    assert settings.heartbeat_interval_seconds == 15
    assert settings.poll_interval_seconds == 3600
    assert settings.irf_interval_seconds == 1200
    assert settings.poll_max_workers == 5
    assert settings.poll_stagger_seconds == 7
    assert settings.poll_deadline_seconds == 300


def test_invalid_timezone_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETWORK_REPORT_TIMEZONE", "Mars/Olympus_Mons")

    with pytest.raises(ConfigError, match="NETWORK_REPORT_TIMEZONE"):
        load_settings()


def test_invalid_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETWORK_REPORT_LOG_LEVEL", "VERBOSE")

    with pytest.raises(ConfigError, match="NETWORK_REPORT_LOG_LEVEL"):
        load_settings()


@pytest.mark.parametrize("raw", ["0", "-5", "not-a-number"])
def test_invalid_heartbeat_interval_is_rejected(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("NETWORK_REPORT_HEARTBEAT_INTERVAL_SECONDS", raw)

    with pytest.raises(ConfigError, match="NETWORK_REPORT_HEARTBEAT_INTERVAL_SECONDS"):
        load_settings()


def test_blank_database_url_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "   ")

    assert load_settings().database_url == DEFAULT_DATABASE_URL


def test_retention_days_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETWORK_REPORT_RETENTION_DAYS", "180")

    assert load_settings().retention_days == 180


@pytest.mark.parametrize("raw", ["89", "0", "-30", "not-a-number"])
def test_retention_days_below_90_is_rejected(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """§24: raw data is kept at least 90 days; smaller values are refused."""

    monkeypatch.setenv("NETWORK_REPORT_RETENTION_DAYS", raw)

    with pytest.raises(ConfigError, match="NETWORK_REPORT_RETENTION_DAYS"):
        load_settings()


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("NETWORK_REPORT_POLL_INTERVAL_SECONDS", "299"),
        ("NETWORK_REPORT_POLL_INTERVAL_SECONDS", "3601"),
        ("NETWORK_REPORT_IRF_INTERVAL_SECONDS", "nope"),
        ("NETWORK_REPORT_POLL_MAX_WORKERS", "0"),
        ("NETWORK_REPORT_POLL_MAX_WORKERS", "33"),
        ("NETWORK_REPORT_POLL_STAGGER_SECONDS", "-1"),
        ("NETWORK_REPORT_POLL_DEADLINE_SECONDS", "0"),
    ],
)
def test_invalid_polling_settings_fail_fast(
    monkeypatch: pytest.MonkeyPatch, name: str, raw: str
) -> None:
    monkeypatch.setenv(name, raw)
    with pytest.raises(ConfigError, match=name):
        load_settings()


def test_poll_deadline_must_be_less_than_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETWORK_REPORT_POLL_INTERVAL_SECONDS", "600")
    monkeypatch.setenv("NETWORK_REPORT_POLL_DEADLINE_SECONDS", "600")
    with pytest.raises(ConfigError, match="must be less than"):
        load_settings()
