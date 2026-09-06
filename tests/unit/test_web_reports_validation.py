"""Unit tests for report-list input validation (W04-T003)."""

import pytest

from backend.web.reports import DOCX_MEDIA_TYPE, is_valid_week_code


@pytest.mark.parametrize(
    ("week_code", "expected"),
    [
        ("2026-W36", True),
        ("2026-W01", True),
        ("2026-W53", True),
        ("1999-W02", True),
        ("2026-W9", False),  # week must be zero-padded
        ("2026-W99", True),  # well-formed; impossible weeks 404 later via ISO check
        ("26-W36", False),
        ("2026W36", False),
        ("2026-w36", False),  # case-sensitive
        ("W36", False),
        ("", False),
        ("2026-W36/../../etc", False),
        ("2026-W36%00", False),
    ],
)
def test_is_valid_week_code(week_code: str, expected: bool) -> None:
    assert is_valid_week_code(week_code) is expected


def test_docx_media_type_is_the_ooxml_type() -> None:
    assert DOCX_MEDIA_TYPE == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
