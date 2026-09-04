"""Centralized, secret-safe logging configuration.

Redaction must not rely on every call site remembering to sanitize. Two
mechanisms are applied at the logging layer:

1. Key-pattern redaction: every rendered line (including exception reprs and
   tracebacks) passes through :func:`redact_text`, which masks values that
   follow common secret key names (password, community, private key,
   authorization, ...).
2. Registered-value redaction: processes call :func:`register_secret` for
   secret values they load (currently the database URL password; device
   SNMP/SSH secrets arrive in Wave 1). Registered values are replaced
   wherever they appear.

`setup_logging` is the single entrypoint used by the web app and the worker;
it also wraps the loggers uvicorn configures on its own so access/error logs
get the same treatment.
"""

import logging
import re
import sys
import threading
from typing import IO

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from backend.config import Settings

_REDACTION = "***"

# Matches `key = value` / `key: value` for common secret key names, including
# JSON-ish `"key": "value"` and `Authorization: Bearer <token>` forms.
_SECRET_ASSIGNMENT_RE = re.compile(
    r"""
    (?P<key>\b(?:
        password|passwd|pwd|
        secret|token|api[_-]?key|access[_-]?key|
        auth|authorization|credential|credentials|
        community|snmp[_-]?community|
        private[_-]?key
    )\b)
    (?P<sep>\s*["']?\s*[:=]\s*["']?)
    (?P<val>"[^"]*"|'[^']*'|(?i:bearer)\s+\S+|[^\s,;)\]}]+)
    """,
    re.VERBOSE | re.IGNORECASE,
)

_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)

_registry_lock = threading.Lock()
_registered_secrets: list[str] = []


def register_secret(value: str) -> None:
    """Register a secret value so it never appears in redacted log output."""

    if not value:
        return
    with _registry_lock:
        if value not in _registered_secrets:
            _registered_secrets.append(value)
            _registered_secrets.sort(key=len, reverse=True)


def clear_registered_secrets() -> None:
    """Drop all registered secrets (used by tests)."""

    with _registry_lock:
        _registered_secrets.clear()


def redact_text(text: str) -> str:
    """Return `text` with known secret patterns and registered values masked."""

    text = _PEM_PRIVATE_KEY_RE.sub(_REDACTION, text)
    text = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('key')}{match.group('sep')}{_REDACTION}",
        text,
    )
    with _registry_lock:
        values = tuple(_registered_secrets)
    for value in values:
        if value in text:
            text = text.replace(value, _REDACTION)
    return text


class SecretRedactingFilter(logging.Filter):
    """Mutates a record's message so every downstream formatter is safe."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return True


class SecretRedactingFormatter(logging.Formatter):
    """Formatter that masks secrets in the fully rendered line.

    Redacting the final rendered string also covers exception reprs and
    tracebacks attached through `exc_info`.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def _register_configured_secrets(settings: Settings) -> None:
    """Register secret material derived from configuration (DB URL password)."""

    try:
        url = make_url(settings.database_url)
    except ArgumentError:
        return
    if url.password:
        register_secret(url.password)


def setup_logging(
    settings: Settings,
    stream: IO[str] | None = None,
    *,
    configure_uvicorn: bool = True,
) -> None:
    """Configure root logging with secret redaction for the whole process."""

    _register_configured_secrets(settings)

    level = logging.getLevelName(settings.log_level)
    if not isinstance(level, int):  # defensive; load_settings validates the name
        level = logging.INFO

    root = logging.getLogger()
    root.setLevel(level)

    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(SecretRedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(SecretRedactingFilter())
    root.handlers.clear()
    root.addHandler(handler)

    if configure_uvicorn:
        # uvicorn installs its own handlers/log levels when it starts; attach the
        # redacting filter to those loggers so their records are safe as well.
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            uvicorn_logger = logging.getLogger(name)
            uvicorn_logger.setLevel(level)
            if not any(isinstance(f, SecretRedactingFilter) for f in uvicorn_logger.filters):
                uvicorn_logger.addFilter(SecretRedactingFilter())
