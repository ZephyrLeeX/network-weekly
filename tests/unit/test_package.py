"""Toolchain smoke test: the backend package is importable and versioned."""

import backend


def test_backend_package_imports_and_exposes_version() -> None:
    assert isinstance(backend.__version__, str)
    assert backend.__version__
