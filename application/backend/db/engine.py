"""SQLAlchemy engine and session factory backed by psycopg 3."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import load_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Process-wide engine built from the configured database URL.

    `pool_pre_ping` lets web/worker recover transparently after a short
    PostgreSQL outage (SYSTEM_SPEC.md §27.9). The connect timeout keeps the
    health endpoint responsive when the database is unreachable.
    """

    settings = load_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def dispose_engine() -> None:
    """Dispose the cached engine, then drop it from the cache.

    Used by tests and graceful shutdown. The next :func:`get_engine` call
    builds a fresh engine from the current configuration.
    """

    engine = get_engine()
    engine.dispose()
    get_engine.cache_clear()
