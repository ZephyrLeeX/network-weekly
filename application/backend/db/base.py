"""Declarative base for all ORM models.

Production schema creation is migration-driven (`alembic upgrade head`).
`Base.metadata.create_all()` is not used outside test helpers.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model in this project."""
