"""PostgreSQL regression for dynamic timestamp defaults (migration 0013).

Pinned root cause: migrations 0002-0008 declared their timestamp columns
with the plain Python string ``server_default="now()"``. Alembic renders a
plain string as the quoted literal ``DEFAULT 'now()'`` and PostgreSQL folds
that special date/time input ONCE, at DDL time, into a constant stored in
the catalog — so every later INSERT re-used the migration instant
(production: ``created_at`` frozen at 2026-09-23 07:34:24.520293+00 across
``device_poll_runs`` / ``report_jobs`` while ``cycle_started_at`` had
reached 2026-09-27/28). Alembic runs one upgrade batch in a single
transaction, which is why every affected table froze to the SAME constant.

These tests prove on real PostgreSQL:

- at 0012 the defaults really are frozen literals (the production symptom);
- 0012 -> 0013 preserves every seeded row exactly (no historical rewrite);
- after 0013 the catalog DEFAULT is the dynamic ``now()`` for all 16
  affected columns;
- new rows in the production-failure tables (``device_poll_runs``,
  ``report_jobs``) receive timestamps matching the database clock;
- a fresh 0001 -> 0013 migration ends in the same correct state;
- the 0013 -> 0012 -> 0013 chain is technically executable.
"""

import os
import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from db_guard import (
    assert_destructive_target_allowed,
    create_database_statement,
    database_name,
    drop_database_statement,
    to_psycopg_dsn,
)
from sqlalchemy import Engine, create_engine

pytestmark = pytest.mark.integration

DEFAULT_TEST_URL = (
    "postgresql+psycopg://network_report:network_report@127.0.0.1:15432/network_report_test"
)

# The 16 columns whose DEFAULT was frozen to a constant by migrations
# 0002-0008 (mirrors FROZEN_TIMESTAMP_COLUMNS in migration 0013).
AFFECTED_TIMESTAMP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("devices", "created_at"),
    ("devices", "updated_at"),
    ("device_members", "created_at"),
    ("interfaces", "created_at"),
    ("aggregation_members", "created_at"),
    ("device_poll_runs", "created_at"),
    ("device_monitoring_state", "updated_at"),
    ("device_reachability_incidents", "created_at"),
    ("interface_monitoring_state", "updated_at"),
    ("interface_state_incidents", "created_at"),
    ("system_settings", "updated_at"),
    ("irf_member_observations", "created_at"),
    ("report_jobs", "created_at"),
    ("report_jobs", "updated_at"),
    ("weekly_reports", "created_at"),
    ("weekly_reports", "updated_at"),
)

# What PostgreSQL stores in the catalog for the frozen shape, e.g.
# '2026-09-23 07:34:24.520293+00'::timestamp with time zone
_FROZEN_DEFAULT_RE = re.compile(r"^'[^']*'::timestamp with time zone$")

# Tables seeded at 0012 (every affected table, one row each).
_SEED_TABLES: tuple[str, ...] = tuple(sorted({t for t, _ in AFFECTED_TIMESTAMP_COLUMNS}))

# Inserts must land within this window of the database clock (the frozen
# constant is the earlier migration-DDL instant, far outside it).
_CLOCK_WINDOW = timedelta(seconds=5)


def _with_dbname(sqlalchemy_url: str, dbname: str) -> str:
    """Replace the database of a SQLAlchemy URL (driver suffix preserved)."""

    scheme, netloc, _path, query, fragment = urlsplit(sqlalchemy_url)
    return urlunsplit((scheme, netloc, f"/{dbname}", query, fragment))


def _upgrade_path_url(base_url: str) -> str:
    """A separate guard-passing test database name for the staged upgrades."""

    name = database_name(base_url)
    if name.endswith("_test"):
        derived = f"{name[: -len('_test')]}_upgrade_test"
    elif name.startswith("test_"):
        derived = f"test_upgrade_{name}"
    else:
        derived = f"{name}_upgrade_test"
    return _with_dbname(base_url, derived)


def _admin_dsn(sqlalchemy_url: str) -> str:
    dsn = to_psycopg_dsn(sqlalchemy_url)
    scheme, netloc, _path, query, fragment = urlsplit(dsn)
    return urlunsplit((scheme, netloc, "postgres", query, fragment))


@pytest.fixture
def upgrade_path_url() -> Iterator[str]:
    """An empty throwaway database for staged 0012/0013 upgrade tests.

    Uses its own database name so the session-wide ``migrated_database_url``
    database is never disturbed. The caller drives the Alembic revisions.
    """

    base_url = os.environ.get("NETWORK_REPORT_TEST_DATABASE_URL", DEFAULT_TEST_URL)
    assert_destructive_target_allowed(base_url)
    url = _upgrade_path_url(base_url)
    assert_destructive_target_allowed(url)

    with psycopg.connect(_admin_dsn(url), autocommit=True) as admin:
        admin.execute(drop_database_statement(database_name(url)))
        admin.execute(create_database_statement(database_name(url)))

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        yield url
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        assert_destructive_target_allowed(url)
        with psycopg.connect(_admin_dsn(url), autocommit=True) as admin:
            admin.execute(drop_database_statement(database_name(url)))


def _column_default(conn: sa.Connection, table: str, column: str) -> str:
    default = conn.execute(
        sa.text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).scalar_one()
    return str(default)


def _assert_dynamic_defaults(conn: sa.Connection) -> None:
    for table, column in AFFECTED_TIMESTAMP_COLUMNS:
        assert _column_default(conn, table, column) == "now()", (table, column)


def _assert_frozen_defaults(conn: sa.Connection) -> None:
    for table, column in AFFECTED_TIMESTAMP_COLUMNS:
        default = _column_default(conn, table, column)
        assert default != "now()", (table, column)
        assert _FROZEN_DEFAULT_RE.match(default), (table, column, default)


def _snapshot(conn: sa.Connection) -> dict[str, list[dict[str, object]]]:
    return {
        table: [
            dict(row._mapping) for row in conn.execute(sa.text(f"SELECT * FROM {table} ORDER BY 1"))
        ]
        for table in _SEED_TABLES
    }


def _seed_rows(conn: sa.Connection) -> None:
    """Insert one row per affected table at 0012, timestamps via server default."""

    anchors = {
        "incident": datetime(2026, 9, 26, 1, 0, 0),
        "observed": datetime(2026, 9, 26, 2, 0, 0),
        "period": datetime(2026, 9, 21, 16, 0, 0),
        "cycle": datetime(2026, 9, 27, 8, 0, 0),
    }
    device_id = conn.execute(
        sa.text(
            "INSERT INTO devices (name, management_ip, model_family, credential_profile) "
            "VALUES ('seed-device', '10.10.10.10', 's10500x', 'default') RETURNING id"
        )
    ).scalar_one()
    conn.execute(
        sa.text("INSERT INTO device_members (device_id, member_id) VALUES (:d, 1)"),
        {"d": device_id},
    )
    agg_if = conn.execute(
        sa.text(
            "INSERT INTO interfaces (device_id, normalized_name, display_name) "
            "VALUES (:d, 'bridge-aggregation1', 'Bridge-Aggregation1') RETURNING id"
        ),
        {"d": device_id},
    ).scalar_one()
    member_if = conn.execute(
        sa.text(
            "INSERT INTO interfaces (device_id, normalized_name, display_name) "
            "VALUES (:d, 'ten1/0/1', 'Ten1/0/1') RETURNING id"
        ),
        {"d": device_id},
    ).scalar_one()
    conn.execute(
        sa.text(
            "INSERT INTO aggregation_members "
            "(aggregation_interface_id, member_interface_id) VALUES (:a, :m)"
        ),
        {"a": agg_if, "m": member_if},
    )
    conn.execute(
        sa.text(
            "INSERT INTO device_poll_runs (device_id, cycle_started_at, status) "
            "VALUES (:d, :c, 'SUCCESS')"
        ),
        {"d": device_id, "c": anchors["cycle"]},
    )
    conn.execute(
        sa.text("INSERT INTO device_monitoring_state (device_id) VALUES (:d)"), {"d": device_id}
    )
    conn.execute(
        sa.text(
            "INSERT INTO device_reachability_incidents (device_id, started_at) "
            "VALUES (:d, :s)"
        ),
        {"d": device_id, "s": anchors["incident"]},
    )
    conn.execute(
        sa.text("INSERT INTO interface_monitoring_state (interface_id) VALUES (:i)"),
        {"i": member_if},
    )
    conn.execute(
        sa.text(
            "INSERT INTO interface_state_incidents (interface_id, started_at) VALUES (:i, :s)"
        ),
        {"i": member_if, "s": anchors["incident"]},
    )
    conn.execute(sa.text("INSERT INTO system_settings (key, value) VALUES ('seed_marker', '1')"))
    conn.execute(
        sa.text(
            "INSERT INTO irf_member_observations "
            "(device_id, member_id, observed, observed_at) VALUES (:d, 1, true, :o)"
        ),
        {"d": device_id, "o": anchors["observed"]},
    )
    # "trigger" is a reserved PostgreSQL word: the ORM quotes it, raw SQL must.
    conn.execute(
        sa.text(
            'INSERT INTO report_jobs (week_code, period_start, period_end, status, "trigger") '
            "VALUES ('2026-W38', :s, :e, 'succeeded', 'scheduled')"
        ),
        {"s": anchors["period"], "e": anchors["cycle"]},
    )
    conn.execute(
        sa.text(
            "INSERT INTO weekly_reports (week_code, period_start, period_end, status) "
            "VALUES ('2026-W38', :s, :e, 'success')"
        ),
        {"s": anchors["period"], "e": anchors["cycle"]},
    )


def _seeded_timestamps(snapshot: dict[str, list[dict[str, object]]]) -> list[datetime]:
    values: list[datetime] = []
    for table, column in AFFECTED_TIMESTAMP_COLUMNS:
        rows = snapshot[table]
        assert rows, table
        for row in rows:
            value = row[column]
            assert isinstance(value, datetime), (table, column, value)
            values.append(value)
    return values


def _assert_near_database_clock(value: datetime, db_now: datetime, label: str) -> None:
    delta = value - db_now if value >= db_now else db_now - value
    assert delta <= _CLOCK_WINDOW, (label, value, db_now)


def test_upgrade_0012_to_0013_preserves_rows_and_dynamizes_defaults(
    upgrade_path_url: str, alembic_cfg: Config
) -> None:
    command.upgrade(alembic_cfg, "0012")
    engine = create_engine(upgrade_path_url, pool_pre_ping=True)
    try:
        # Seed at 0012: every timestamp column filled by the (buggy) server
        # default inside one seeding transaction.
        with engine.begin() as conn:
            _seed_rows(conn)
            seed_now = conn.execute(sa.text("SELECT now()")).scalar_one()

        with engine.connect() as conn:
            before = _snapshot(conn)
            # Root cause pinned: at 0012 the defaults are frozen constants.
            _assert_frozen_defaults(conn)
            seeded = _seeded_timestamps(before)
            # The production symptom in miniature: rows inserted at seed_now
            # carry one shared earlier (migration-DDL) instant, exactly the
            # value the frozen catalog default evaluates to.
            assert all(value == seeded[0] for value in seeded), seeded
            assert seeded[0] < seed_now, (seeded[0], seed_now)
            frozen_expr = _column_default(conn, "device_poll_runs", "created_at")
            evaluated = conn.execute(sa.text(f"SELECT {frozen_expr}")).scalar_one()
            assert evaluated == seeded[0]

        command.upgrade(alembic_cfg, "head")

        with engine.connect() as conn:
            after = _snapshot(conn)
            # 0013 must not touch any existing row.
            assert after == before
            # Catalog defaults are now the dynamic expression.
            _assert_dynamic_defaults(conn)

        # New rows in the production-failure tables use the database clock.
        with engine.connect() as conn:
            with conn.begin():
                db_now = conn.execute(sa.text("SELECT now()")).scalar_one()
                conn.execute(
                    sa.text(
                        "INSERT INTO device_poll_runs (device_id, cycle_started_at, status) "
                        "VALUES (:d, :c, 'SUCCESS')"
                    ),
                    {"d": before["devices"][0]["id"], "c": datetime(2026, 9, 28, 8, 0, 0)},
                )
                conn.execute(
                    sa.text(
                        'INSERT INTO report_jobs (week_code, period_start, period_end, "trigger") '
                        "VALUES ('2026-W39', :s, :e, 'scheduled')"
                    ),
                    {"s": datetime(2026, 9, 28, 16, 0, 0), "e": datetime(2026, 10, 5, 16, 0, 0)},
                )
            with conn.begin():
                poll_created = conn.execute(
                    sa.text("SELECT created_at FROM device_poll_runs WHERE cycle_started_at = :c"),
                    {"c": datetime(2026, 9, 28, 8, 0, 0)},
                ).scalar_one()
                job_created, job_updated = conn.execute(
                    sa.text(
                        "SELECT created_at, updated_at FROM report_jobs WHERE week_code = :w"
                    ),
                    {"w": "2026-W39"},
                ).one()
        _assert_near_database_clock(poll_created, db_now, "device_poll_runs.created_at")
        _assert_near_database_clock(job_created, db_now, "report_jobs.created_at")
        _assert_near_database_clock(job_updated, db_now, "report_jobs.updated_at")
        # The frozen history stays frozen (no rewrite of existing rows).
        assert after["device_poll_runs"][0]["created_at"] == seeded[0]
        assert after["report_jobs"][0]["created_at"] != job_created
    finally:
        engine.dispose()


def test_downgrade_chain_0013_0012_0013_is_executable(
    upgrade_path_url: str, alembic_cfg: Config, alembic_head_revision: str
) -> None:
    command.upgrade(alembic_cfg, "head")
    engine = create_engine(upgrade_path_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
            assert version == alembic_head_revision
            _assert_dynamic_defaults(conn)

        # Schema-only downgrade: back to the 0012 shape (a literal that
        # freezes at THIS downgrade's DDL moment), no row rewrite involved.
        command.downgrade(alembic_cfg, "0012")
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
            assert version == "0012"
            _assert_frozen_defaults(conn)

        command.upgrade(alembic_cfg, "head")
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
            assert version == alembic_head_revision
            _assert_dynamic_defaults(conn)
    finally:
        engine.dispose()


def test_fresh_head_timestamp_defaults_are_dynamic(db_engine: Engine) -> None:
    """Fresh 0001 -> head: even though 0002-0008 briefly create frozen
    defaults, the final schema state must be fully corrected by 0013."""

    with db_engine.connect() as conn:
        _assert_dynamic_defaults(conn)

    with db_engine.begin() as conn:
        device_id = conn.execute(
            sa.text(
                "INSERT INTO devices (name, management_ip, model_family, credential_profile) "
                "VALUES ('clock-device', '10.10.10.11', 's10500x', 'default') RETURNING id"
            )
        ).scalar_one()
        db_now = conn.execute(sa.text("SELECT now()")).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO device_poll_runs (device_id, cycle_started_at, status) "
                "VALUES (:d, :c, 'SUCCESS')"
            ),
            {"d": device_id, "c": datetime(2026, 9, 28, 9, 0, 0)},
        )
        conn.execute(
            sa.text(
                "INSERT INTO report_jobs (week_code, period_start, period_end, status) "
                "VALUES ('2026-W39', :s, :e, 'succeeded')"
            ),
            {"s": datetime(2026, 9, 28, 16, 0, 0), "e": datetime(2026, 10, 5, 16, 0, 0)},
        )

    with db_engine.connect() as conn:
        poll_created = conn.execute(
            sa.text("SELECT created_at FROM device_poll_runs WHERE device_id = :d"),
            {"d": device_id},
        ).scalar_one()
        job_created, job_updated = conn.execute(
            sa.text("SELECT created_at, updated_at FROM report_jobs WHERE week_code = '2026-W39'")
        ).one()

    _assert_near_database_clock(poll_created, db_now, "device_poll_runs.created_at")
    _assert_near_database_clock(job_created, db_now, "report_jobs.created_at")
    _assert_near_database_clock(job_updated, db_now, "report_jobs.updated_at")
