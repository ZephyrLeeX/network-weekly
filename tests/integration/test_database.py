"""Database connectivity checks against real PostgreSQL (W00-T004)."""

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration


def test_database_connection(db_engine: sa.Engine) -> None:
    with db_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT 1")).scalar_one() == 1
