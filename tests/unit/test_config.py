"""Unit tests for runtime configuration loading (W00-T004 scope)."""

import pytest

from backend.config import DEFAULT_DATABASE_URL, load_settings


def test_default_database_url_used_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert load_settings().database_url == DEFAULT_DATABASE_URL


def test_database_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    override = "postgresql+psycopg://u:p@db.example:5432/other"
    monkeypatch.setenv("DATABASE_URL", override)

    assert load_settings().database_url == override


def test_blank_database_url_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "   ")

    assert load_settings().database_url == DEFAULT_DATABASE_URL
