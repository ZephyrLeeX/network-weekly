"""Web application entrypoint, served by uvicorn in the web container.

Wave 0 scope: the ASGI application plus the /health endpoint. Login, report
pages and the priority-interface page are added by later waves
(SYSTEM_SPEC.md §20/§25). The interactive API docs are disabled because the
web interface is an internal operations UI, not a public API.

The health response states that the web application itself is running and
reports database connectivity as a separate field. It never exposes secrets
or environment variables. Worker liveness is deliberately NOT part of this
endpoint: worker heartbeat is an independent check stored in PostgreSQL.
"""

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend import __version__
from backend.config import load_settings
from backend.db.engine import get_engine

app = FastAPI(
    title="Network Weekly Report System",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def database_reachable() -> bool:
    """Cheap connectivity probe with a bounded connect timeout."""

    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True


@app.get("/health")
async def health() -> dict[str, str]:
    settings = load_settings()
    return {
        "status": "ok",
        "app": "network-weekly-report",
        "version": __version__,
        "environment": settings.environment,
        "timezone": str(settings.timezone),
        "database": "ok" if database_reachable() else "unreachable",
    }
