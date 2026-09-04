"""Unit tests for centralized secret-safe logging (W00-T005)."""

import asyncio
import io
import logging
from collections.abc import Iterator

import pytest

from backend.config import load_settings
from backend.log import (
    SecretRedactingFormatter,
    SecretRedactingFormatterWrapper,
    clear_registered_secrets,
    redact_text,
    register_secret,
    setup_logging,
)

UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture(autouse=True)
def _clean_secret_registry() -> None:
    clear_registered_secrets()


@pytest.fixture
def _restore_uvicorn_loggers() -> Iterator[None]:
    """Snapshot and restore uvicorn's loggers so tests cannot pollute them."""

    state: dict[str, tuple[int, list[logging.Handler], list[object], bool]] = {
        name: (
            logging.getLogger(name).level,
            list(logging.getLogger(name).handlers),
            list(logging.getLogger(name).filters),
            logging.getLogger(name).propagate,
        )
        for name in UVICORN_LOGGER_NAMES
    }
    yield
    for name, (level, handlers, filters, propagate) in state.items():
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.handlers = handlers
        logger.filters = filters  # type: ignore[assignment]
        logger.propagate = propagate


def test_password_assignment_is_redacted() -> None:
    out = redact_text("connecting with password=hunter2 to db01")

    assert "hunter2" not in out
    assert "password=***" in out


def test_snmp_community_is_redacted() -> None:
    out = redact_text("SNMP community=public device=core-01")

    assert "public" not in out
    assert "community=***" in out


def test_snmp_community_json_style_is_redacted() -> None:
    out = redact_text('{"snmp_community": "s3cr3tC0mm", "device": "core-01"}')

    assert "s3cr3tC0mm" not in out


def test_ssh_password_colon_style_is_redacted() -> None:
    out = redact_text("ssh password: p@ssw0rd!")

    assert "p@ssw0rd!" not in out


def test_authorization_bearer_is_redacted() -> None:
    out = redact_text("Authorization: Bearer abc.def.ghi")

    assert "abc.def.ghi" not in out


def test_private_key_block_is_redacted() -> None:
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\nmore\n-----END PRIVATE KEY-----"
    out = redact_text(f"key material follows:\n{pem}\nend")

    assert "MIIEvQIBADANBg" not in out
    assert "***" in out


def test_non_secret_values_are_preserved() -> None:
    line = "database=ok status=ok environment=production timezone=Asia/Shanghai"

    assert redact_text(line) == line


def test_registered_secret_value_is_redacted_anywhere() -> None:
    register_secret("s3cret-community-42")

    out = redact_text('dump: {"community": "s3cret-community-42", "ok": true}')

    assert "s3cret-community-42" not in out
    assert "***" in out


def test_empty_secret_registration_is_ignored() -> None:
    register_secret("")

    assert redact_text("nothing to hide") == "nothing to hide"


def test_formatter_redacts_rendered_stream() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SecretRedactingFormatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("redaction-test.stream")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("snmp_community=%s", "real-community-value")

    rendered = stream.getvalue()
    assert "real-community-value" not in rendered
    assert "snmp_community=***" in rendered


def test_exception_repr_is_redacted() -> None:
    """Secrets inside exception messages/reprs must not survive logging."""

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SecretRedactingFormatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("redaction-test.exception")
    logger.handlers = [handler]
    logger.propagate = False

    try:
        raise RuntimeError("auth failed for password=super-hidden-pw")
    except RuntimeError:
        logger.exception("operation failed")

    rendered = stream.getvalue()
    assert "super-hidden-pw" not in rendered
    assert "Traceback" in rendered  # stack stays available for diagnosis


def test_setup_logging_registers_database_url_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://app:db-secret-pw@db:5432/appdb")
    settings = load_settings()
    stream = io.StringIO()
    clear_registered_secrets()

    setup_logging(settings, stream=stream, configure_uvicorn=False)

    logging.getLogger("redaction-test.dburl").error("cached credentials for db-secret-pw")

    assert "db-secret-pw" not in stream.getvalue()
    assert stream.getvalue().count("***") >= 1
    clear_registered_secrets()


def _uvicorn_logger_with_plain_handler(stream: io.StringIO) -> logging.Logger:
    """Attach a plain (uvicorn-style, non-redacting) handler to a uvicorn logger."""

    logger = logging.getLogger("uvicorn.access")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.handlers = [handler]
    logger.filters = []
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger


@pytest.mark.usefixtures("_restore_uvicorn_loggers")
def test_setup_logging_redacts_uvicorn_logger_output() -> None:
    """Access/error logs routed through uvicorn's own handlers must be redacted."""

    stream = io.StringIO()
    uvicorn_logger = _uvicorn_logger_with_plain_handler(stream)

    setup_logging(load_settings(), stream=io.StringIO())

    uvicorn_logger.info('login failed for password="leaked-pw-1"')

    assert "leaked-pw-1" not in stream.getvalue()
    assert "password=" in stream.getvalue()
    assert "***" in stream.getvalue()


@pytest.mark.usefixtures("_restore_uvicorn_loggers")
def test_setup_logging_redacts_uvicorn_exception_traceback() -> None:
    """Tracebacks rendered by uvicorn's own formatter must be redacted too."""

    stream = io.StringIO()
    uvicorn_logger = _uvicorn_logger_with_plain_handler(stream)

    setup_logging(load_settings(), stream=io.StringIO())

    try:
        raise RuntimeError("snmp community=leaked-community-7")
    except RuntimeError:
        uvicorn_logger.exception("connection to device failed")

    rendered = stream.getvalue()
    assert "leaked-community-7" not in rendered
    assert "Traceback" in rendered  # diagnosis stays available


@pytest.mark.usefixtures("_restore_uvicorn_loggers")
def test_setup_logging_wraps_uvicorn_formatter_exactly_once() -> None:
    stream = io.StringIO()
    uvicorn_logger = _uvicorn_logger_with_plain_handler(stream)
    original_formatter = uvicorn_logger.handlers[0].formatter
    assert original_formatter is not None

    setup_logging(load_settings(), stream=io.StringIO())
    setup_logging(load_settings(), stream=io.StringIO())

    formatter = uvicorn_logger.handlers[0].formatter
    assert isinstance(formatter, SecretRedactingFormatterWrapper)
    assert formatter.wrapped is original_formatter


@pytest.mark.usefixtures("_restore_uvicorn_loggers")
def test_uvicorn_access_log_with_args_stays_formattable_and_redacted() -> None:
    """Access records rely on record.args (uvicorn unpacks a 5-tuple).

    The redaction wrapper must keep args intact so uvicorn's formatter keeps
    working, while the rendered line still loses secret material.
    """

    stream = io.StringIO()
    uvicorn_logger = _uvicorn_logger_with_plain_handler(stream)

    setup_logging(load_settings(), stream=io.StringIO())

    uvicorn_logger.info(
        '%s - "%s %s HTTP/%s" %d',
        "10.0.0.9:1234",
        "GET",
        "/health?password=hunter2",
        "1.1",
        200,
    )

    rendered = stream.getvalue()
    assert "hunter2" not in rendered
    assert "password=***" in rendered
    assert 'GET /health?password=*** HTTP/1.1" 200' in rendered


@pytest.mark.usefixtures("_restore_uvicorn_loggers")
def test_web_lifespan_wires_secret_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """The web app must call setup_logging during startup (not only the worker)."""

    from backend import main as backend_main
    from backend.config import Settings

    calls: list[Settings] = []
    monkeypatch.setattr(backend_main, "setup_logging", calls.append)

    async def _run_lifespan() -> None:
        async with backend_main.app.router.lifespan_context(backend_main.app):
            pass

    asyncio.run(_run_lifespan())

    assert len(calls) == 1
    assert isinstance(calls[0], Settings)
