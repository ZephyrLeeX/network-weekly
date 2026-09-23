"""Minimal server-side HTML rendering helpers (§20.4).

No template engine, no frontend build: pages are small Python functions.
The ONE rule this module enforces structurally is that every piece of
user- or device-controlled text goes through :func:`esc` (HTML-escaped,
attribute-safe) before it may appear in a page — values are never
interpolated into HTML outside an :func:`esc` call. There is deliberately
no raw-markup escape hatch.
"""

from html import escape


def esc(value: object) -> str:
    """Escape text for safe interpolation into HTML elements/attributes.

    `quote=True` also escapes double quotes, so the result is safe inside
    double-quoted attribute values (e.g. `value="..."`).
    """

    return escape(str(value), quote=True)


def page(title: str, body: str, *, status_note: str | None = None) -> str:
    """Render the shared offline page layout."""

    note = f"<p class='note'>{esc(status_note)}</p>" if status_note else ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} - 网络运维周报系统</title>
<link rel="stylesheet" href="/static/app.css">
<script src="/static/app.js" defer></script>
</head>
<body>
<div class="shell">
<header class="site-header"><a class="brand" href="/">网络运维周报</a></header>
<main>
<h1>{esc(title)}</h1>
{note}
{body}
</main>
</div>
</body>
</html>"""


def hidden_csrf(csrf_token: str) -> str:
    """The hidden CSRF field every state-changing form must carry (§20.4)."""

    return f'<input type="hidden" name="csrf_token" value="{esc(csrf_token)}">'


_NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("/", "首页", "home"),
    ("/reports", "报告列表", "reports"),
    ("/interfaces", "重点接口配置", "interfaces"),
)


def nav(active: str) -> str:
    """The shared navigation bar; `active` is rendered bold, not linked."""

    links = []
    for href, label, key in _NAV_ITEMS:
        if key == active:
            links.append(f"<strong>{esc(label)}</strong>")
        else:
            links.append(f'<a href="{esc(href)}">{esc(label)}</a>')
    return '<nav class="nav" aria-label="主导航">' + "".join(links) + "</nav>"
