# syntax=docker/dockerfile:1
# Application image shared by the `web` and `worker` compose services
# (SYSTEM_SPEC.md §25). Reproducible: pinned uv builder + pinned base image +
# frozen uv.lock; no dependency downloads happen at container runtime.

FROM ghcr.io/astral-sh/uv:0.12.9 AS uv

FROM python:3.14-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

# Dependency layer: rebuilt only when the lockfile changes.
# README.md is part of the package metadata declared in pyproject.toml.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev --no-editable

# Project layer: non-editable install ships code inside the venv.
COPY application ./application
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim-trixie
RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home-dir /app app
# Report storage (SYSTEM_SPEC.md §5). The reports volume is seeded from this
# app-owned directory, so the non-root app user can create/write/read/delete
# generated DOCX files even on a clean host.
RUN mkdir -p /var/lib/network-report/reports \
    && chown -R app:app /var/lib/network-report
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Migration-driven schema management (SYSTEM_SPEC.md §23/§26): the image can
# run `alembic upgrade head` during install/update.
COPY migrations ./migrations
COPY alembic.ini ./
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
# Default command serves the web app; the worker service overrides `command`.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
