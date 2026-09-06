"""Report list and DOCX download routes (W04-T003, §20.2/§21).

Both routes sit behind :func:`backend.web.deps.require_admin`.

Download safety (§20/§27.18 — "download 只能下载 registry 中合法 current
DOCX，禁止任意路径读取"): the request NEVER carries a path. The week code
selects the `weekly_reports` registry row; the file is served only when

- the week code is a valid ISO week code (`YYYY-Www`),
- the row is the week's `success` entry with a `file_path`,
- the stored path resolves, after symlink resolution, EXACTLY to the
  canonical current-report name inside the configured report directory
  (`network-weekly-report-<week>.docx`, §5) — any stored path pointing
  elsewhere (tampered row, traversal, symlink escape, missing file) is
  refused with 404.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select
from starlette.responses import Response

from backend.config import Settings, load_settings
from backend.db.engine import get_session_factory
from backend.db.models import WeeklyReport
from backend.reporting.jobs import (
    REPORT_STATUS_SUCCESS,
    load_weekly_reports,
    report_period_for,
)
from backend.reporting.period import BUSINESS_TIMEZONE, report_file_name
from backend.web.deps import AdminDep
from backend.web.pages import ReportListEntry, not_found_page, reports_page

router = APIRouter()

# ISO week code, e.g. 2026-W36 (§3).
_WEEK_CODE_RE = re.compile(r"^\d{4}-W\d{2}$")

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def is_valid_week_code(week_code: str) -> bool:
    """True for well-formed ISO week codes (`2026-W36`)."""

    return bool(_WEEK_CODE_RE.match(week_code))


def _fmt_local(moment: datetime | None) -> str:
    """Render an instant in the business timezone (§3) or 数据缺失 (§6.2)."""

    if moment is None:
        return "数据缺失"
    return moment.astimezone(BUSINESS_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S %z")


def _period_text(week_code: str) -> str:
    """`周一至周日` presentation of the half-open period (§3)."""

    period = report_period_for(week_code)
    start_day = period.start.astimezone(BUSINESS_TIMEZONE).date()
    end_day = (period.end - timedelta(days=1)).astimezone(BUSINESS_TIMEZONE).date()
    return f"{start_day.isoformat()} ~ {end_day.isoformat()}"


def _status_text(status: str) -> str:
    return {"success": "成功", "failed": "失败"}.get(status, status)


def _list_entries() -> list[ReportListEntry]:
    with get_session_factory()() as db:
        rows = load_weekly_reports(db)
    entries: list[ReportListEntry] = []
    for row in rows:
        try:
            period_text = _period_text(row.week_code)
        except (ValueError, KeyError):
            period_text = "数据缺失"
        entries.append(
            ReportListEntry(
                week_code=row.week_code,
                period_text=period_text,
                generated_text=_fmt_local(row.generated_at),
                status_text=_status_text(row.status),
                last_error=row.last_error,
                downloadable=(
                    row.status == REPORT_STATUS_SUCCESS and row.file_path is not None
                ),
            )
        )
    return entries


def current_report_path(settings: Settings, week_code: str) -> Path | None:
    """The one downloadable current DOCX of a week, or None (§27.18).

    Enforces, in order: registry success row -> the stored path is the
    canonical current name DIRECTLY inside the resolved report directory
    (no `..`, no symlink at any position — parent resolution + explicit
    symlink rejection close both traversal and link escape) -> the target
    exists as a regular file. A tampered or dangling `file_path` can
    therefore never serve arbitrary file content.
    """

    with get_session_factory()() as db:
        row = db.execute(
            select(WeeklyReport).where(WeeklyReport.week_code == week_code)
        ).scalar_one_or_none()
        stored_path = (
            row.file_path
            if row is not None
            and row.status == REPORT_STATUS_SUCCESS
            and row.file_path is not None
            else None
        )
    if stored_path is None:
        return None
    stored = Path(stored_path)
    canonical_name = report_file_name(report_period_for(week_code))
    if stored.is_symlink() or stored.name != canonical_name:
        return None
    report_dir = settings.report_dir.resolve()
    try:
        if stored.parent.resolve(strict=True) != report_dir:
            return None
        if not (stored.resolve(strict=True).is_file()):
            return None
    except OSError:
        return None
    return stored.resolve(strict=True)


@router.get("/reports")
async def reports_home(request: Request, admin: AdminDep) -> Response:
    """The report list page (§20.2), newest week first."""

    del request
    return HTMLResponse(reports_page(_list_entries()))


@router.get("/reports/{week_code}/download")
async def download_report(week_code: str, admin: AdminDep) -> Response:
    """Download the week's current DOCX (§20.2) — registry-validated only."""

    if not is_valid_week_code(week_code):
        return HTMLResponse(not_found_page("报告不存在"), status_code=404)
    try:
        target = current_report_path(load_settings(), week_code)
    except (ValueError, KeyError):
        target = None  # a well-formed but impossible ISO week (e.g. 2026-W99)
    if target is None:
        return HTMLResponse(not_found_page("报告不存在或尚未生成完成"), status_code=404)
    return FileResponse(target, media_type=DOCX_MEDIA_TYPE, filename=target.name)
