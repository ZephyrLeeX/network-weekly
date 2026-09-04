"""Safety tests for the integration-test database destruction guard.

These are pure unit-level checks (no PostgreSQL required) and run in the
default test suite: the guard must hold even when integration tests are not
being run interactively.
"""

import psycopg.sql
import pytest
from db_guard import (
    ALLOW_DESTRUCTIVE_TESTS_ENV,
    UnsafeDatabaseTarget,
    assert_destructive_target_allowed,
    create_database_statement,
    database_name,
    drop_database_statement,
)

LOOPBACK_URL = "postgresql+psycopg://u:p@127.0.0.1:15432/network_report_test"


def test_obvious_test_database_on_loopback_is_allowed() -> None:
    assert_destructive_target_allowed(LOOPBACK_URL)


@pytest.mark.parametrize("hostname", ["localhost", "127.0.0.1", "[::1]"])
def test_all_loopback_hosts_are_allowed(hostname: str) -> None:
    url = f"postgresql+psycopg://u:p@{hostname}:15432/network_report_test"

    assert_destructive_target_allowed(url)


def test_non_test_database_name_is_rejected() -> None:
    url = "postgresql+psycopg://u:p@127.0.0.1:15432/network_report"

    with pytest.raises(UnsafeDatabaseTarget, match="network_report"):
        assert_destructive_target_allowed(url)


@pytest.mark.parametrize(
    "dbname",
    ["network_report", "test", "_test", "test_", "network-report-test", "network_test_prod"],
)
def test_lookalike_database_names_are_rejected(dbname: str) -> None:
    url = f"postgresql+psycopg://u:p@127.0.0.1:15432/{dbname}"

    with pytest.raises(UnsafeDatabaseTarget):
        assert_destructive_target_allowed(url)


def test_prefix_form_test_database_name_is_allowed() -> None:
    url = "postgresql+psycopg://u:p@127.0.0.1:15432/test_network_report"

    assert_destructive_target_allowed(url)


def test_non_loopback_host_is_rejected_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ALLOW_DESTRUCTIVE_TESTS_ENV, raising=False)
    url = "postgresql+psycopg://u:p@db.internal:5432/network_report_test"

    with pytest.raises(UnsafeDatabaseTarget, match="db.internal"):
        assert_destructive_target_allowed(url)


def test_non_loopback_host_is_allowed_with_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ALLOW_DESTRUCTIVE_TESTS_ENV, "YES")
    url = "postgresql+psycopg://u:p@db.internal:5432/network_report_test"

    assert_destructive_target_allowed(url)


def test_opt_in_does_not_bypass_the_test_name_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The name guard is unconditional; no flag may drop a production-looking DB."""

    monkeypatch.setenv(ALLOW_DESTRUCTIVE_TESTS_ENV, "YES")
    url = "postgresql+psycopg://u:p@db.internal:5432/production"

    with pytest.raises(UnsafeDatabaseTarget):
        assert_destructive_target_allowed(url)


def test_opt_in_requires_exact_yes_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOW_DESTRUCTIVE_TESTS_ENV, "yes")
    url = "postgresql+psycopg://u:p@db.internal:5432/network_report_test"

    with pytest.raises(UnsafeDatabaseTarget):
        assert_destructive_target_allowed(url)


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[object] = []

    def execute(self, statement: object) -> None:
        self.statements.append(statement)


def test_drop_statement_uses_sql_identifier() -> None:
    statement = drop_database_statement("network_report_test")

    assert isinstance(statement, psycopg.sql.Composed)
    assert any(isinstance(part, psycopg.sql.Identifier) for part in statement)
    conn = _RecordingConnection()
    conn.execute(statement)
    assert conn.statements == [statement]


def test_create_statement_uses_sql_identifier() -> None:
    statement = create_database_statement("network_report_test")

    assert isinstance(statement, psycopg.sql.Composed)
    assert any(isinstance(part, psycopg.sql.Identifier) for part in statement)


def test_database_name_extraction() -> None:
    assert database_name(LOOPBACK_URL) == "network_report_test"
