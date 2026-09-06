"""HTML pages of the operations web (W04-T002; report/priority pages arrive
with W04-T003/T005). Every text value is rendered through
:mod:`backend.web.html.esc` — credentials, session tokens and device
secrets never appear in any page.
"""

from backend.web.html import esc, hidden_csrf, page

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
    """The authenticated home page (§20): navigation + logout (T003/T005
    turn the nav placeholders into the real pages)."""

    body = f"""
<p class="ok">已登录：{esc(admin_username)}</p>
<nav>
<a href="/reports">报告列表</a>
<a href="/interfaces">重点接口配置</a>
</nav>
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


def login_redirect_note() -> str:
    return "请先登录"
