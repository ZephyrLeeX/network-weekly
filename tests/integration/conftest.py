"""Fixtures for integration tests: a real PostgreSQL from the dev compose.

Tests here never use SQLite: migrations and database behavior are validated
against PostgreSQL only (SYSTEM_SPEC.md runtime database).

The test database is dropped/recreated through `db_guard`, which refuses any
target that is not an obvious test database on a loopback address (or that
lacks the explicit destructive-test opt-in). Database names are always bound
via `psycopg.sql.Identifier`, never interpolated into SQL strings.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from db_guard import (
    assert_destructive_target_allowed,
    create_database_statement,
    database_name,
    drop_database_statement,
    to_psycopg_dsn,
)
from sqlalchemy import Engine, create_engine

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_TEST_URL = (
    "postgresql+psycopg://network_report:network_report@127.0.0.1:15432/network_report_test"
)


def _admin_dsn(sqlalchemy_url: str) -> str:
    dsn = to_psycopg_dsn(sqlalchemy_url)
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, "postgres", parts.query, ""))


@pytest.fixture(scope="session")
def alembic_cfg() -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


@pytest.fixture(scope="session")
def alembic_head_revision(alembic_cfg: Config) -> str:
    head = ScriptDirectory.from_config(alembic_cfg).get_current_head()
    assert head is not None, "the project must always have at least one migration"
    return head


@pytest.fixture(scope="session")
def migrated_database_url(alembic_cfg: Config) -> Iterator[str]:
    """A fresh test database migrated to head with the real Alembic migrations."""

    test_url = os.environ.get("NETWORK_REPORT_TEST_DATABASE_URL", DEFAULT_TEST_URL)
    assert_destructive_target_allowed(test_url)
    dbname = database_name(test_url)

    with psycopg.connect(_admin_dsn(test_url), autocommit=True) as admin:
        admin.execute(drop_database_statement(dbname))
        admin.execute(create_database_statement(dbname))

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_url
    try:
        command.upgrade(alembic_cfg, "head")
        yield test_url
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        assert_destructive_target_allowed(test_url)
        with psycopg.connect(_admin_dsn(test_url), autocommit=True) as admin:
            admin.execute(drop_database_statement(dbname))


@pytest.fixture
def db_engine(migrated_database_url: str) -> Iterator[Engine]:
    engine = create_engine(migrated_database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()
