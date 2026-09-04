"""Fresh-database migration acceptance on real PostgreSQL (W00-T004)."""

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration


def test_fresh_database_reaches_head(
    db_engine: sa.Engine,
    alembic_head_revision: str,
) -> None:
    with db_engine.connect() as conn:
        version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()

    assert version == alembic_head_revision


def test_worker_heartbeat_columns_use_timestamptz(db_engine: sa.Engine) -> None:
    """SYSTEM_SPEC.md §3/§23: all business time columns are TIMESTAMPTZ."""

    with db_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'worker_heartbeat'"
            )
        ).all()

    data_types: dict[str, str] = {str(name): str(data_type) for name, data_type in rows}
    assert data_types["last_heartbeat"] == "timestamp with time zone"
    assert data_types["started_at"] == "timestamp with time zone"
