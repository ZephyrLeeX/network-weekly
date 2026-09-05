"""Runtime configuration loading for the application backend.

All foundation settings are explicit and environment-overridable. The
business timezone defaults to Asia/Shanghai (SYSTEM_SPEC.md §3) and must
never be inherited implicitly from the host.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Host-side development default: the dev compose PostgreSQL published on the
# loopback interface. Compose and production deployments set DATABASE_URL
# explicitly (see docker-compose.yml and SYSTEM_SPEC.md §26.1).
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://network_report:network_report@127.0.0.1:15432/network_report"
)

DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_DATA_DIR = "./data"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_WORKER_ID = "worker"
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30
# §24: raw metrics/poll runs are kept at least 90 days.
DEFAULT_RETENTION_DAYS = 90
MIN_RETENTION_DAYS = 90
# Production locations (SYSTEM_SPEC.md §22/§26.1); overridable for tests/dev.
DEFAULT_DEVICES_FILE = "/etc/network-report/devices.toml"
DEFAULT_SECRETS_FILE = "/etc/network-report/secrets.env"

_VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(ValueError):
    """Raised when an environment-provided configuration value is invalid."""


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    database_url: str = DEFAULT_DATABASE_URL
    data_dir: Path = Path(DEFAULT_DATA_DIR)
    timezone: ZoneInfo = ZoneInfo(DEFAULT_TIMEZONE)
    log_level: str = DEFAULT_LOG_LEVEL
    worker_id: str = DEFAULT_WORKER_ID
    heartbeat_interval_seconds: int = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    # Raw metrics/poll-run retention in days (§24: at least 90).
    retention_days: int = DEFAULT_RETENTION_DAYS
    devices_file: Path = Path(DEFAULT_DEVICES_FILE)
    secrets_file: Path = Path(DEFAULT_SECRETS_FILE)

    @property
    def report_dir(self) -> Path:
        """Persistent location for generated DOCX reports (SYSTEM_SPEC.md §5)."""

        return self.data_dir / "reports"


def load_settings() -> Settings:
    """Build settings from the environment, failing loudly on invalid values."""

    database_url = os.environ.get("DATABASE_URL", "").strip() or DEFAULT_DATABASE_URL

    environment = os.environ.get("NETWORK_REPORT_ENVIRONMENT", "").strip() or "development"

    data_dir = Path(os.environ.get("NETWORK_REPORT_DATA_DIR", "").strip() or DEFAULT_DATA_DIR)

    timezone_name = os.environ.get("NETWORK_REPORT_TIMEZONE", "").strip() or DEFAULT_TIMEZONE
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ConfigError(
            f"NETWORK_REPORT_TIMEZONE is not a valid IANA timezone: {timezone_name!r}"
        ) from exc

    raw_log_level = os.environ.get("NETWORK_REPORT_LOG_LEVEL", "").strip()
    log_level = (raw_log_level or DEFAULT_LOG_LEVEL).upper()
    if log_level not in _VALID_LOG_LEVELS:
        raise ConfigError(
            f"NETWORK_REPORT_LOG_LEVEL must be one of {', '.join(_VALID_LOG_LEVELS)}; "
            f"got {log_level!r}"
        )

    worker_id = os.environ.get("NETWORK_REPORT_WORKER_ID", "").strip() or DEFAULT_WORKER_ID

    devices_file = Path(
        os.environ.get("NETWORK_REPORT_DEVICES_FILE", "").strip() or DEFAULT_DEVICES_FILE
    )
    secrets_file = Path(
        os.environ.get("NETWORK_REPORT_SECRETS_FILE", "").strip() or DEFAULT_SECRETS_FILE
    )

    raw_interval = os.environ.get("NETWORK_REPORT_HEARTBEAT_INTERVAL_SECONDS", "").strip()
    if not raw_interval:
        heartbeat_interval = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    else:
        try:
            heartbeat_interval = int(raw_interval)
        except ValueError as exc:
            raise ConfigError(
                "NETWORK_REPORT_HEARTBEAT_INTERVAL_SECONDS must be an integer number of "
                f"seconds; got {raw_interval!r}"
            ) from exc
    if heartbeat_interval <= 0:
        raise ConfigError(
            "NETWORK_REPORT_HEARTBEAT_INTERVAL_SECONDS must be positive; "
            f"got {heartbeat_interval}"
        )

    raw_retention = os.environ.get("NETWORK_REPORT_RETENTION_DAYS", "").strip()
    if not raw_retention:
        retention_days = DEFAULT_RETENTION_DAYS
    else:
        try:
            retention_days = int(raw_retention)
        except ValueError as exc:
            raise ConfigError(
                "NETWORK_REPORT_RETENTION_DAYS must be an integer number of days; "
                f"got {raw_retention!r}"
            ) from exc
    if retention_days < MIN_RETENTION_DAYS:
        raise ConfigError(
            f"NETWORK_REPORT_RETENTION_DAYS must be >= {MIN_RETENTION_DAYS} "
            f"(SYSTEM_SPEC.md §24 keeps raw data at least 90 days); got {retention_days}"
        )

    return Settings(
        environment=environment,
        database_url=database_url,
        data_dir=data_dir,
        timezone=timezone,
        log_level=log_level,
        worker_id=worker_id,
        heartbeat_interval_seconds=heartbeat_interval,
        retention_days=retention_days,
        devices_file=devices_file,
        secrets_file=secrets_file,
    )
