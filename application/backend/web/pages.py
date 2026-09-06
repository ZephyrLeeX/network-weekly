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


# --- Priority interfaces (W04-T005, §11/§12) -------------------------------------


@dataclass(frozen=True)
class DeviceEntry:
    """One device row of the device selector (§12)."""

    device_id: int
    name: str


@dataclass(frozen=True)
class InterfaceListEntry:
    """View model of one interface row of the priority-interface page."""

    interface_id: int
    display_name: str
    description: str | None
    admin_state: str | None
    oper_state: str | None
    is_aggregation: bool
    monitored: bool
    # Member display names when this interface is an aggregation.
    aggregation_members: tuple[str, ...]
    # Aggregation display names this interface belongs to.
    member_of: tuple[str, ...]


def interfaces_page(
    devices: list[DeviceEntry],
    selected_device: DeviceEntry | None,
    entries: list[InterfaceListEntry],
    csrf_token: str,
    *,
    note: str | None = None,
) -> str:
    """The priority-interface configuration page (§11/§12): device
    selection, interface name/description, current admin/oper state,
    aggregation/member relationship and the monitored toggle. Toggling
    never cascades — the W02-T005 service enforces that server-side."""

    if devices:
        device_links = " | ".join(
            _device_link(device, selected_device) for device in devices
        )
        device_html = f"<p>选择设备：{device_links}</p>"
    else:
        device_html = '<p class="muted">尚未发现设备。设备清单由 inventory 同步与采集自动发现。</p>'

    if selected_device is None:
        table_html = '<p class="muted">请先选择一台设备。</p>'
    elif entries:
        rows = "".join(_interface_row(entry, csrf_token) for entry in entries)
        table_html = f"""
<table>
<thead>
<tr><th>接口名</th><th>描述</th><th>Admin</th><th>Oper</th><th>聚合关系</th><th>重点监控</th></tr>
</thead>
<tbody>
{rows}
</tbody>
</table>"""
    else:
        table_html = '<p class="muted">该设备暂无已发现接口。</p>'

    body = f"""
{nav("interfaces")}
{device_html}
{table_html}"""
    return page("重点接口配置", body, status_note=note)


def _device_link(device: DeviceEntry, selected: DeviceEntry | None) -> str:
    if selected is not None and device.device_id == selected.device_id:
        return f"<strong>{esc(device.name)}</strong>"
    return f'<a href="/interfaces?device_id={device.device_id}">{esc(device.name)}</a>'


def _interface_row(entry: InterfaceListEntry, csrf_token: str) -> str:
    description = entry.description if entry.description else "—"
    admin_state = entry.admin_state if entry.admin_state else "数据缺失"
    oper_state = entry.oper_state if entry.oper_state else "数据缺失"

    relationship_parts = []
    if entry.is_aggregation:
        members = ", ".join(entry.aggregation_members) if entry.aggregation_members else "无成员"
        relationship_parts.append(f"聚合接口（成员：{members}）")
    if entry.member_of:
        relationship_parts.append("属于聚合：" + ", ".join(entry.member_of))
    relationship = "<br>".join(relationship_parts) if relationship_parts else "—"

    if entry.monitored:
        toggle = f"""
<form class="inline" method="post" action="/interfaces/{entry.interface_id}/monitored">
{hidden_csrf(csrf_token)}
<button name="monitored" value="false">取消监控</button>
</form>"""
        status = "是"
    else:
        toggle = f"""
<form class="inline" method="post" action="/interfaces/{entry.interface_id}/monitored">
{hidden_csrf(csrf_token)}
<button name="monitored" value="true">设为监控</button>
</form>"""
        status = "否"

    return f"""
<tr>
<td>{esc(entry.display_name)}</td>
<td>{esc(description)}</td>
<td>{esc(admin_state)}</td>
<td>{esc(oper_state)}</td>
<td>{relationship}</td>
<td>{esc(status)}{toggle}</td>
</tr>"""
