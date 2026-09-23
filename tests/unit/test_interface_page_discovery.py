"""Device selection only requests discovery before any cached interface exists."""

from backend.web.pages import DeviceEntry, interfaces_page


def test_selector_separates_first_discovery_from_cached_get() -> None:
    fresh = DeviceEntry(1, "fresh")
    cached = DeviceEntry(2, "cached", has_interfaces=True)
    page = interfaces_page([fresh, cached], cached, [], "csrf")

    assert '<form method="post" action="/interfaces/1/discover" class="inline">' in page
    assert '<a class="device-tab active" href="/interfaces?device_id=2">' in page
    assert '<form method="post" action="/interfaces/2/discover" class="inline">' not in page
    assert "刷新接口列表" in page


def test_failed_first_discovery_shows_retry_and_active_job_shows_progress() -> None:
    failed = DeviceEntry(1, "fresh", discovery_status="failed", discovery_job_id=3)
    failed_page = interfaces_page([failed], failed, [], "csrf", job_id=3, job_status="failed")
    assert "接口获取失败" in failed_page
    assert "重新获取" in failed_page
    assert '<a class="device-tab active" href="/interfaces?device_id=1">' in failed_page

    pending = DeviceEntry(1, "fresh", discovery_status="pending", discovery_job_id=4)
    pending_page = interfaces_page([pending], pending, [], "csrf", job_id=4, job_status="pending")
    assert "正在获取接口" in pending_page
