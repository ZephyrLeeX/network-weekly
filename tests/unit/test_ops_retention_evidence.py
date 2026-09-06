"""W05-AUDIT fix 2: the A13 evidence verdict comes from the OLDEST row.

The pre-audit collector computed MAX(collected_at) and labelled it "oldest",
so a healthy recent row always masked data retained beyond 90 days. The
classification here must be driven by MIN() (the true oldest surviving row)
— the DB-level MIN query itself is pinned against PostgreSQL in
`tests/integration/test_ops_retention_evidence.py`.
"""

from datetime import UTC, datetime

from backend.ops.retention_evidence import (
    BEYOND_THRESHOLD_DAYS,
    classify_age_days,
    format_retention_line,
)

NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)


def test_classification_uses_the_91_day_slack_threshold() -> None:
    assert BEYOND_THRESHOLD_DAYS == 91.0  # 90d §24 + retention-pass slack
    assert classify_age_days(1.0) == "OK"
    assert classify_age_days(89.9) == "OK"
    assert classify_age_days(91.0) == "OK"  # threshold itself is still OK
    assert classify_age_days(91.01) == "BEYOND-90d"
    assert classify_age_days(180.0) == "BEYOND-90d"


def test_line_reports_oldest_row_age_and_verdict() -> None:
    oldest = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)  # 180 days before NOW
    line = format_retention_line("device_metrics", oldest, NOW)
    assert "device_metrics" in line
    assert "oldest row 2026-03-10T12:00:00+00:00" in line
    assert "age 180.0 days" in line
    assert "BEYOND-90d" in line
    assert "OK" not in line


def test_line_reports_ok_when_oldest_row_is_recent() -> None:
    oldest = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)  # 1 day before NOW
    line = format_retention_line("device_poll_runs", oldest, NOW)
    assert "age 1.0 days" in line
    assert line.endswith("OK")
    assert "BEYOND-90d" not in line


def test_line_reports_empty_tables_without_a_verdict() -> None:
    assert format_retention_line("interface_metrics", None, NOW) == (
        "interface_metrics: empty"
    )
