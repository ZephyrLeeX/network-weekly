"""Unit tests for centralized secret-safe logging (W00-T005)."""

import io
import logging

import pytest

from backend.config import load_settings
from backend.log import (
    SecretRedactingFormatter,
    clear_registered_secrets,
    redact_text,
    register_secret,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _clean_secret_registry() -> None:
    clear_registered_secrets()


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
