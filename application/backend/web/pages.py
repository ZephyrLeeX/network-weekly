"""HTML pages of the operations web (W04-T002/T003/T005). Every text value
is rendered through :mod:`backend.web.html.esc` — credentials, session
tokens and device secrets never appear in any page.
"""

from dataclasses import dataclass

from backend.web.html import esc, hidden_csrf, nav, page

LOGIN_TITLE = "管理员登录"
HOME_TITLE = "网络运维周报系统"


def login_page(csrf_token: str, *, error: str | None = None, username: str = "") -> str:
    """The login form (§20.1). `error` is a fixed text, never exception data."""

    error_html = f'<p class="error">{esc(error)}</p>' if error else ""
    body = f"""
{error_html}
<form method="post" action="/login">
{hidden_csrf(csrf_token)}
<p><label>用户名 <input type="text" name="username" value="{esc(username)}" autofocus></label></p>
<p><label>密码 <input type="password" name="password"></label></p>
<p><button type="submit">登录</button></p>
</form>"""
    return page(LOGIN_TITLE, body)


def home_page(admin_username: str, csrf_token: str) -> str:
    """The authenticated home page (§20)."""

    body = f"""
{nav("home")}
<p class="ok">已登录：{esc(admin_username)}</p>
<form class="inline" method="post" action="/logout">
{hidden_csrf(csrf_token)}
<button type="submit">退出登录</button>
</form>"""
    return page(HOME_TITLE, body)


def csrf_error_page() -> str:
    """403 page for a missing/invalid CSRF token — fixed text, no details."""

    return page(
        "请求被拒绝",
        '<p class="error">安全校验失败（CSRF），请返回后重试。</p>'
        '<p><a href="/">返回首页</a></p>',
    )


def not_found_page(message: str = "请求的资源不存在") -> str:
    """404 page — the message is fixed text chosen by the server."""

    return page("未找到", f'<p class="error">{esc(message)}</p><p><a href="/">返回首页</a></p>')


# --- Report list (W04-T003/T004, §20.2) ------------------------------------------


@dataclass(frozen=True)
class ReportListEntry:
    """View model of one row of the report list."""

    week_code: str
    period_text: str
    generated_text: str
    status_text: str
    last_error: str | None
    downloadable: bool
    # Present when an ACTIVE report job (W04-T004 manual regenerate) owns
    # the week — rendered next to the report status so the administrator
    # sees that a regeneration is queued/running/retrying.
    job_status_text: str | None = None


def reports_page(
    entries: list[ReportListEntry], csrf_token: str, *, note: str | None = None
) -> str:
    """The report list (§20.2): week/period/generated_at/status/last_error/
    download/regenerate — newest week first. The regenerate form POSTs with
    the CSRF field (§20.4); `note` carries a fixed server status text."""

    if entries:
        rows = "".join(_report_row(entry, csrf_token) for entry in entries)
    else:
        rows = '<tr><td colspan="7" class="muted">暂无报告</td></tr>'
    body = f"""
{nav("reports")}
<table>
<thead>
<tr><th>周编号</th><th>统计周期（周一至周日）</th><th>生成时间</th><th>状态</th><th>错误信息</th><th>下载</th><th>重新生成</th></tr>
</thead>
<tbody>
{rows}
</tbody>
</table>"""
    return page("报告列表", body, status_note=note)


def _report_row(entry: ReportListEntry, csrf_token: str) -> str:
    download_cell = (
        f'<a href="/reports/{esc(entry.week_code)}/download">下载 DOCX</a>'
        if entry.downloadable
        else '<span class="muted">无</span>'
    )
    error_cell = esc(entry.last_error) if entry.last_error else '<span class="muted">—</span>'
    status_cell = esc(entry.status_text)
    if entry.job_status_text:
        note_html = f'<br><span class="muted">{esc(entry.job_status_text)}</span>'
        status_cell += note_html
    action = f"/reports/{esc(entry.week_code)}/regenerate"
    regenerate_cell = f"""<form class="inline" method="post" action="{action}">
{hidden_csrf(csrf_token)}
<button type="submit">重新生成</button>
</form>"""
    return f"""
<tr>
<td>{esc(entry.week_code)}</td>
<td>{esc(entry.period_text)}</td>
<td>{esc(entry.generated_text)}</td>
<td>{status_cell}</td>
<td>{error_cell}</td>
<td>{download_cell}</td>
<td>{regenerate_cell}</td>
</tr>"""
