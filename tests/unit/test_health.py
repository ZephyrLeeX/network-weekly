"""Unit tests for the web health endpoint (W00-T005)."""

import asyncio

import pytest
from sqlalchemy import create_engine

from backend import __version__
from backend.main import database_reachable, health


def test_health_reports_ok_web_and_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.main.database_reachable", lambda: True)

    payload = asyncio.run(health())

    assert payload["status"] == "ok"
    assert payload["database"] == "ok"
    assert payload["version"] == __version__
    assert payload["timezone"] == "Asia/Shanghai"


def test_health_reports_unreachable_database_but_web_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Web health stays independent: the web app itself is still running."""

    monkeypatch.setattr("backend.main.database_reachable", lambda: False)

    payload = asyncio.run(health())

    assert payload["status"] == "ok"
    assert payload["database"] == "unreachable"


def test_health_response_has_stable_shape_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.main.database_reachable", lambda: True)

    payload = asyncio.run(health())

    assert set(payload) == {"status", "app", "version", "environment", "timezone", "database"}
    assert all(isinstance(value, str) for value in payload.values())


def test_database_reachable_false_on_engine_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken DATABASE_URL must not crash the health endpoint."""

    monkeypatch.setattr(
        "backend.main.get_engine",
        lambda: create_engine(
            "postgresql+psycopg://nouser@127.0.0.1:1/none",
            connect_args={"connect_timeout": 1},
        ),
    )

    assert database_reachable() is False
