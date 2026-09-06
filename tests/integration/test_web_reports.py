"""Web-level report list + download tests (W04-T003, §20.2/§21/§27.18).

Drives the real FastAPI app against migrated PostgreSQL with report rows
seeded in the `weekly_reports` registry and DOCX files in a temporary
report directory.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend.auth.admin import initialize_admin
from backend.db.models import User, UserSession, WeeklyReport
from backend.main import app
from backend.reporting.period import period_for_iso_week
from backend.web.reports import DOCX_MEDIA_TYPE

pytestmark = pytest.mark.integration

USERNAME = "admin"
PASSWORD = "pw-web-reports-tests"
W36 = "2026-W36"
W35 = "2026-W35"
FAILED_ERROR = "docx render failed: fixture-injected error"


@pytest.fixture(autouse=True)
def _admin(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(UserSession).delete()
        session.query(User).delete()
        session.query(WeeklyReport).delete()
        session.commit()
        initialize_admin(session, USERNAME, PASSWORD)
    yield
    with Session(db_engine) as session:
        session.query(UserSession).delete()
        session.query(User).delete()
        session.query(WeeklyReport).delete()
        session.commit()


@pytest.fixture
def report_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary reports directory the web app reads via settings env."""

    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setenv("NETWORK_REPORT_DATA_DIR", str(tmp_path))
    return reports


def _split_week(week_code: str) -> tuple[int, int]:
    year, week = week_code.split("-W")
    return int(year), int(week)


def _seed_report(
    db_engine: Engine,
    week_code: str,
    *,
    status: str,
    file_path: Path | None,
    last_error: str | None = None,
) -> WeeklyReport:
    period = period_for_iso_week(*_split_week(week_code))
    with Session(db_engine) as session:
        row = WeeklyReport(
            week_code=week_code,
            period_start=period.start,
            period_end=period.end,
            status=status,
            file_path=str(file_path) if file_path else None,
            generated_at=datetime.now(UTC) if status == "success" else None,
            last_error=last_error,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def _login(client: TestClient, password: str = PASSWORD) -> httpx.Response:
    client.get("/login")
    response = client.get("/login")
    marker = 'name="csrf_token" value="'
    start = response.text.index(marker) + len(marker)
    csrf = response.text[start : response.text.index('"', start)]
    return client.post(
        "/login", data={"username": USERNAME, "password": password, "csrf_token": csrf}
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, follow_redirects=False) as test_client:
        assert _login(test_client).status_code == 303
        yield test_client


def _make_docx(path: Path) -> None:
    Document().save(str(path))


def test_unauthenticated_report_access_is_denied(db_engine: Engine) -> None:
    with TestClient(app, follow_redirects=False) as bare:
        assert bare.get("/reports").status_code == 303
        assert bare.get("/reports/2026-W36/download").status_code == 303


def test_report_list_is_empty_without_rows(client: TestClient, db_engine: Engine) -> None:
    page = client.get("/reports")

    assert page.status_code == 200
    assert "暂无报告" in page.text


def test_report_list_shows_required_columns_newest_first(
    client: TestClient, db_engine: Engine, report_dir: Path
) -> None:
    docx = report_dir / "network-weekly-report-2026-W36.docx"
    _make_docx(docx)
    _seed_report(db_engine, W36, status="success", file_path=docx)
    _seed_report(db_engine, W35, status="failed", file_path=None, last_error=FAILED_ERROR)

    page = client.get("/reports")
    text = page.text

    assert page.status_code == 200
    # Newest week first.
    assert text.index(W36) < text.index(W35)
    # Period shown as the natural week (§3).
    assert "2026-08-31 ~ 2026-09-06" in text
    assert "2026-08-24 ~ 2026-08-30" in text
    # Generated time rendered in the business timezone.
    assert "+0800" in text
    # Statuses + the failure reason (§20.2 last_error).
    assert "成功" in text and "失败" in text
    assert FAILED_ERROR in text
    # Download only for the success row.
    assert f'href="/reports/{W36}/download"' in text
    assert f'href="/reports/{W35}/download"' not in text


def test_download_streams_the_current_docx(
    client: TestClient, db_engine: Engine, report_dir: Path
) -> None:
    docx = report_dir / "network-weekly-report-2026-W36.docx"
    _make_docx(docx)
    _seed_report(db_engine, W36, status="success", file_path=docx)
    expected = docx.read_bytes()

    response = client.get(f"/reports/{W36}/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(DOCX_MEDIA_TYPE)
    assert "attachment" in response.headers["content-disposition"]
    assert "network-weekly-report-2026-W36.docx" in response.headers["content-disposition"]
    assert response.content == expected


def test_download_failed_report_is_denied(
    client: TestClient, db_engine: Engine
) -> None:
    _seed_report(db_engine, W35, status="failed", file_path=None, last_error=FAILED_ERROR)

    assert client.get(f"/reports/{W35}/download").status_code == 404


def test_download_unknown_week_is_denied(client: TestClient) -> None:
    assert client.get("/reports/2026-W20/download").status_code == 404


@pytest.mark.parametrize("bad", ["abc", "2026-W9", "2026-W99", "../..%2Fetc%2Fpasswd"])
def test_download_malformed_week_is_denied(client: TestClient, bad: str) -> None:
    assert client.get(f"/reports/{bad}/download").status_code == 404


def test_download_path_outside_report_dir_is_denied(
    client: TestClient, db_engine: Engine, report_dir: Path, tmp_path: Path
) -> None:
    """A tampered registry path can never serve arbitrary files (§27.18)."""

    outside = tmp_path / "outside.docx"
    _make_docx(outside)
    _seed_report(db_engine, W36, status="success", file_path=outside)
    assert client.get(f"/reports/{W36}/download").status_code == 404

    traversal = report_dir.parent / "secret.docx"
    _make_docx(traversal)
    _seed_report(
        db_engine, "2026-W34", status="success", file_path=report_dir / ".." / "secret.docx"
    )
    assert client.get("/reports/2026-W34/download").status_code == 404


def test_download_symlink_escape_is_denied(
    client: TestClient, db_engine: Engine, report_dir: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.docx"
    _make_docx(secret)
    link = report_dir / "network-weekly-report-2026-W33.docx"
    link.symlink_to(secret)
    _seed_report(db_engine, "2026-W33", status="success", file_path=link)

    assert client.get("/reports/2026-W33/download").status_code == 404


def test_download_missing_file_is_denied(
    client: TestClient, db_engine: Engine, report_dir: Path
) -> None:
    docx = report_dir / "network-weekly-report-2026-W36.docx"
    _make_docx(docx)
    _seed_report(db_engine, W36, status="success", file_path=docx)
    docx.unlink()

    assert client.get(f"/reports/{W36}/download").status_code == 404


def test_report_rows_never_leak_file_paths(
    client: TestClient, db_engine: Engine, report_dir: Path
) -> None:
    docx = report_dir / "network-weekly-report-2026-W36.docx"
    _make_docx(docx)
    _seed_report(db_engine, W36, status="success", file_path=docx)

    page = client.get("/reports")

    assert str(report_dir) not in page.text
