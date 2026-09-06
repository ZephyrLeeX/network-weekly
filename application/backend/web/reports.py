"""Report list, DOCX download and manual regenerate routes
(W04-T003/T004, §4.4/§20.2/§21).

All routes sit behind :func:`backend.web.deps.require_admin`.

Download safety (§20/§27.18 — "download 只能下载 registry 中合法 current
DOCX，禁止任意路径读取"): the request NEVER carries a path. The week code
selects the `weekly_reports` registry row; the file is served only when

- the week code is a valid ISO week code (`YYYY-Www`),
- the row is the week's `success` entry with a `file_path`,
- the stored path's name is the canonical current-report name inside the
  resolved report directory and is not a symlink (`network-weekly-report-
  <week>.docx`, §5) — any stored path pointing elsewhere (tampered row,
  traversal, symlink escape, missing file) is refused with 404.

Manual regenerate (§4.4): the form POSTs `POST /reports/{week}/regenerate`
with the CSRF field; the route is a thin adapter onto the Wave 3
`request_regenerate` service — NO job logic is duplicated here. Repeated
submissions return the same active job (service idempotency + the
`uq_report_jobs_active_week` DB guarantee), so duplicates can never create
concurrent duplicate jobs; the active job's state is rendered in the list
so the action's effect is visible.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from starlette.responses import Response

from backend.config import Settings, load_settings
from backend.db.engine import get_session_factory
from backend.db.models import ReportJob, WeeklyReport
from backend.reporting.jobs import (
    ACTIVE_STATUSES,
    REPORT_STATUS_SUCCESS,
    ReportJobError,
    load_weekly_reports,
    report_period_for,
    request_regenerate,
)
from backend.reporting.period import BUSINESS_TIMEZONE, report_file_name
from backend.web.deps import AdminDep
from backend.web.pages import ReportListEntry, not_found_page, reports_page
from backend.web.security import csrf_for_render, csrf_guard, set_csrf_cookie

router = APIRouter()

# ISO week code, e.g. 2026-W36 (§3).
_WEEK_CODE_RE = re.compile(r"^\d{4}-W\d{2}$")

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Fixed note when the regenerate service refuses (incomplete week) — the
# exception text itself is never rendered (§22.2 discipline).
_REGENERATE_REFUSED_NOTE = "重新生成被拒绝：该统计周尚未结束。"

_JOB_STATUS_TEXT = {
    "pending": "重新生成排队中",
    "running": "重新生成进行中",
    "failed": "重新生成失败，等待自动重试",
}


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


def _active_job_status_text(db: DbSession) -> dict[str, str]:
    """week_code -> readable note for weeks with an ACTIVE report job."""

    jobs = (
        db.execute(
            select(ReportJob).where(ReportJob.status.in_(ACTIVE_STATUSES))
        )
        .scalars()
        .all()
    )
    notes: dict[str, str] = {}
    for job in jobs:
        note = _JOB_STATUS_TEXT.get(job.status, job.status)
        existing = notes.get(job.week_code)
        notes[job.week_code] = f"{existing}；{note}" if existing else note
    return notes


def _list_entries() -> list[ReportListEntry]:
    with get_session_factory()() as db:
        rows = load_weekly_reports(db)
        job_notes = _active_job_status_text(db)
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
                job_status_text=job_notes.get(row.week_code),
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
    """The report list page (§20.2), newest week first, with the CSRF-fed
    regenerate forms (W04-T004)."""

    csrf_token, fresh = csrf_for_render(request)
    response = HTMLResponse(reports_page(_list_entries(), csrf_token))
    if fresh:
        set_csrf_cookie(response, csrf_token)
    return response


@router.post("/reports/{week_code}/regenerate")
async def regenerate_report(request: Request, week_code: str, admin: AdminDep) -> Response:
    """§4.4 manual regenerate — a thin, CSRF-protected adapter onto the
    Wave 3 `request_regenerate` service (never two active jobs per week)."""

    del admin  # the dependency enforces authentication; the account is unused
    csrf_response = await csrf_guard(request)
    if csrf_response is not None:
        return csrf_response
    if not is_valid_week_code(week_code):
        return HTMLResponse(not_found_page("报告不存在"), status_code=404)
    try:
        period = report_period_for(week_code)
    except (ValueError, KeyError):
        return HTMLResponse(not_found_page("报告不存在"), status_code=404)

    refused = False
    now = datetime.now(UTC)
    with get_session_factory()() as db:
        try:
            request_regenerate(db, period, now=now)
        except ReportJobError:
            # Incomplete week (the service refuses; no job was created).
            refused = True
    if refused:
        csrf_token, fresh = csrf_for_render(request)
        response = HTMLResponse(
            reports_page(_list_entries(), csrf_token, note=_REGENERATE_REFUSED_NOTE)
        )
        if fresh:
            set_csrf_cookie(response, csrf_token)
        return response
    return RedirectResponse("/reports", status_code=303)


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
