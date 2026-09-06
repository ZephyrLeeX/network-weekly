"""Web-level manual regenerate tests (W04-T004, §4.4/§20.2/§20.4).

The web action is a thin adapter onto the Wave 3 `request_regenerate`
service: CSRF-protected, one active job per week even under duplicate
submissions, and the job state visible on the report list.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from backend.auth.admin import initialize_admin
from backend.db.models import ReportJob, User, UserSession, WeeklyReport
from backend.main import app
from backend.reporting.period import period_for_iso_week

pytestmark = pytest.mark.integration

USERNAME = "admin"
PASSWORD = "pw-web-regenerate-tests"


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(UserSession).delete()
        session.query(User).delete()
        session.query(WeeklyReport).delete()
        session.query(ReportJob).delete()
        session.commit()
        initialize_admin(session, USERNAME, PASSWORD)
    yield
    with Session(db_engine) as session:
        session.query(UserSession).delete()
        session.query(User).delete()
        session.query(WeeklyReport).delete()
        session.query(ReportJob).delete()
        session.commit()


@pytest.fixture
def report_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary reports directory the web app reads via settings env."""

    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setenv("NETWORK_REPORT_DATA_DIR", str(tmp_path))
    return reports


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, follow_redirects=False) as test_client:
        assert _login(test_client).status_code == 303
        yield test_client


def _login(client: TestClient) -> httpx.Response:
    client.get("/login")
    response = client.get("/login")
    marker = 'name="csrf_token" value="'
    start = response.text.index(marker) + len(marker)
    csrf = response.text[start : response.text.index('"', start)]
    return client.post(
        "/login", data={"username": USERNAME, "password": PASSWORD, "csrf_token": csrf}
    )


def _csrf(client: TestClient) -> str:
    """The CSRF token bound to this client's cookie (the authenticated home
    page always renders the logout form with the current token; the same
    cookie value is accepted by the regenerate form)."""

    page = client.get("/")
    marker = 'name="csrf_token" value="'
    start = page.text.index(marker) + len(marker)
    return page.text[start : page.text.index('"', start)]


def _seed_success_report(db_engine: Engine, week_code: str, report_dir: Path) -> None:
    year, week = week_code.split("-W")
    period = period_for_iso_week(int(year), int(week))
    docx = report_dir / f"network-weekly-report-{week_code}.docx"
    Document().save(str(docx))
    with Session(db_engine) as session:
        session.add(
            WeeklyReport(
                week_code=week_code,
                period_start=period.start,
                period_end=period.end,
                status="success",
                file_path=str(docx),
                generated_at=datetime.now(UTC),
            )
        )
        session.commit()


def _job_rows(db_engine: Engine, week_code: str) -> list[ReportJob]:
    with Session(db_engine) as session:
        return list(
            session.execute(
                select(ReportJob).where(ReportJob.week_code == week_code)
            ).scalars()
        )


def test_regenerate_creates_one_manual_job(
    client: TestClient, db_engine: Engine, report_dir: Path
) -> None:
    _seed_success_report(db_engine, "2026-W35", report_dir)

    response = client.post(
        "/reports/2026-W35/regenerate", data={"csrf_token": _csrf(client)}
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/reports"
    jobs = _job_rows(db_engine, "2026-W35")
    assert len(jobs) == 1
    assert jobs[0].trigger == "manual"
    assert jobs[0].status == "pending"


def test_duplicate_regenerate_submissions_yield_one_job(
    client: TestClient, db_engine: Engine, report_dir: Path
) -> None:
    """Repeated Web submissions can never create concurrent duplicate jobs."""

    _seed_success_report(db_engine, "2026-W35", report_dir)
    csrf = _csrf(client)

    for _ in range(3):
        response = client.post("/reports/2026-W35/regenerate", data={"csrf_token": csrf})
        assert response.status_code == 303

    assert len(_job_rows(db_engine, "2026-W35")) == 1
    assert (
        Session(db_engine)
        .execute(select(func.count()).select_from(ReportJob))
        .scalar_one()
        == 1
    )


def test_regenerate_requires_valid_csrf(client: TestClient, db_engine: Engine) -> None:
    missing = client.post("/reports/2026-W35/regenerate", data={})
    forged = client.post("/reports/2026-W35/regenerate", data={"csrf_token": "forged"})

    assert missing.status_code == 403
    assert forged.status_code == 403
    assert _job_rows(db_engine, "2026-W35") == []


def test_unauthenticated_regenerate_is_denied(db_engine: Engine) -> None:
    with TestClient(app, follow_redirects=False) as bare:
        response = bare.post("/reports/2026-W35/regenerate", data={"csrf_token": "x"})

    assert response.status_code == 303
    assert _job_rows(db_engine, "2026-W35") == []


def test_regenerate_incomplete_week_is_refused_with_note(
    client: TestClient, db_engine: Engine
) -> None:
    """A week that has not ended cannot be regenerated; the refusal shows."""

    response = client.post(
        "/reports/2026-W40/regenerate", data={"csrf_token": _csrf(client)}
    )

    assert response.status_code == 200
    assert "该统计周尚未结束" in response.text
    assert _job_rows(db_engine, "2026-W40") == []


@pytest.mark.parametrize("bad", ["abc", "2026-W9", "2026-W99"])
def test_regenerate_malformed_week_is_denied(client: TestClient, bad: str) -> None:
    assert client.post(f"/reports/{bad}/regenerate", data={"csrf_token": "x"}).status_code in (
        403,
        404,
    )


def test_active_job_status_is_visible_on_the_report_list(
    client: TestClient, db_engine: Engine, report_dir: Path
) -> None:
    _seed_success_report(db_engine, "2026-W35", report_dir)
    client.post("/reports/2026-W35/regenerate", data={"csrf_token": _csrf(client)})

    page = client.get("/reports")

    assert page.status_code == 200
    assert "重新生成排队中" in page.text
    # The previous success stays downloadable while the job is active (§4.4).
    assert 'href="/reports/2026-W35/download"' in page.text


def test_report_list_renders_regenerate_form_with_csrf(
    client: TestClient, db_engine: Engine, report_dir: Path
) -> None:
    _seed_success_report(db_engine, "2026-W35", report_dir)

    page = client.get("/reports")

    assert page.status_code == 200
    assert 'action="/reports/2026-W35/regenerate"' in page.text
    assert 'name="csrf_token"' in page.text
    assert "重新生成" in page.text
