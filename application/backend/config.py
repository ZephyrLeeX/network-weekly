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

    return Settings(
        environment=environment,
        database_url=database_url,
        data_dir=data_dir,
        timezone=timezone,
        log_level=log_level,
        worker_id=worker_id,
        heartbeat_interval_seconds=heartbeat_interval,
    )
