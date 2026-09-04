"""Runtime configuration loading.

W00-T004 scope: database URL resolution for the SQLAlchemy engine and Alembic.
W00-T005 extends this module with the full foundation settings (runtime
environment, data/report paths, explicit Asia/Shanghai timezone, logging
level, worker identity and heartbeat interval).
"""

import os
from dataclasses import dataclass

# Host-side development default: the dev compose PostgreSQL published on the
# loopback interface. Compose and production deployments set DATABASE_URL
# explicitly (see docker-compose.yml and SYSTEM_SPEC.md §26.1).
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://network_report:network_report@127.0.0.1:15432/network_report"
)

DATABASE_URL_ENV = "DATABASE_URL"


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL


def load_settings() -> Settings:
    """Build settings from the environment, failing loudly on empty values."""

    database_url = os.environ.get(DATABASE_URL_ENV, "").strip() or DEFAULT_DATABASE_URL
    return Settings(database_url=database_url)
