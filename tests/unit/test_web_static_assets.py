"""Offline UI assets stay local and contain no device credential handling."""

from pathlib import Path


def test_interface_script_uses_only_same_origin_status_and_local_filter() -> None:
    script = (Path(__file__).parents[2] / "application/backend/web/static/app.js").read_text()
    assert "dataset.statusUrl" in script
    assert "#interface-search" in script
    assert "fetch(" in script
    assert "https://" not in script and "http://" not in script
    assert "community" not in script.lower()
    assert "password" not in script.lower()
    assert "192.0.2." not in script
