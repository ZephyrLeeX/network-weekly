"""Web application entrypoint, served by uvicorn in the web container.

Wave 0 scope: the ASGI application object only. The /health endpoint, login,
report pages and the priority-interface page are added by later tasks
(SYSTEM_SPEC.md §20/§25). The interactive API docs are disabled because the
web interface is an internal operations UI, not a public API.
"""

from fastapi import FastAPI

from backend import __version__

app = FastAPI(
    title="Network Weekly Report System",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
