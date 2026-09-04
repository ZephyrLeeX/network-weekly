"""Unit tests for the cached engine lifecycle (W00 audit hotfix)."""

from collections.abc import Iterator

import pytest
from sqlalchemy.engine import Engine

from backend.db.engine import dispose_engine, get_engine


@pytest.fixture
def _clean_engine_cache() -> Iterator[None]:
    """Run with an isolated engine cache; leave it empty afterwards."""

    get_engine.cache_clear()
    yield
    get_engine.cache_clear()


@pytest.mark.usefixtures("_clean_engine_cache")
def test_dispose_engine_disposes_cached_engine_and_clears_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dispose_engine must really dispose the engine, not only clear the cache."""

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    engine = get_engine()

    disposed: list[Engine] = []
    monkeypatch.setattr(Engine, "dispose", lambda self, close=True: disposed.append(self))

    dispose_engine()

    assert disposed == [engine]
    assert get_engine() is not engine


@pytest.mark.usefixtures("_clean_engine_cache")
def test_dispose_engine_is_safe_when_no_engine_was_built() -> None:
    """Calling dispose_engine first builds (then disposes) the cached engine."""

    dispose_engine()

    assert get_engine.cache_info().currsize == 0
