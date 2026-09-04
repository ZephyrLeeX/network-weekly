"""Parsers for the allowlisted H3C SSH `display` outputs (W01-T005).

Pure text -> DTO parsing, unit-tested against fixture files. The fixtures
are synthetic placeholders pending anonymized real S10500X/S12500 captures
(see tests/fixtures/h3c/README.md); Comware output varies between versions,
so parsing is deliberately tolerant: anything not recognized with
confidence stays None or is skipped rather than guessed.
"""

import re

from backend.collect.dto import IrfMemberSample

# Comware `display version` software line. Verified shapes (H3C Comware 7
# S10500/S12500 documentation):
#   "H3C Comware Software, Version 7.1.070, Release 7596P10"
#   "H3C Comware Platform Software, Software Version 7.1.070, Release 7510P21"
_VERSION_RE = re.compile(r"Version\s+(?P<version>\d[\w.]*)", re.IGNORECASE)
_RELEASE_RE = re.compile(r"Release\s+(?P<release>\d[\w.-]*)", re.IGNORECASE)
# Model line: "H3C S10508X uptime is ..." / "H3C S12516X-G uptime is ...".
_MODEL_RE = re.compile(r"^\s*H3C\s+(?P<model>S\d+[A-Z0-9X]*(?:-G)?)\b", re.MULTILINE)

# Table rows of `display irf` / `display irf configuration`. Leading IRF
# markers (`*` = master, `+` = logged-in member) may precede the member id.
_ROW_RE = re.compile(r"^\s*[*+]*\s*(?P<member>\d+)\s+(?P<rest>\S.*?)\s*$")

# Role tokens accepted verbatim. Kept to the documented Comware IRF role
# vocabulary (Master/Slave, plus Standby as reported by some Comware 7
# releases); anything else leaves role as None instead of guessing.
_KNOWN_ROLES = {"master", "slave", "backup", "standby"}


def parse_display_version(text: str) -> dict[str, str | None]:
    """Extract (version, release, model) from `display version`.

    Anything the output does not identify with confidence stays None.
    """

    version = _VERSION_RE.search(text)
    release = _RELEASE_RE.search(text)
    model = _MODEL_RE.search(text)
    return {
        "version": version.group("version") if version else None,
        "release": release.group("release") if release else None,
        "model": model.group("model") if model else None,
    }


def _member_rows(text: str) -> list[IrfMemberSample]:
    """Common member-row extraction: numeric first column, optional role."""

    members: list[IrfMemberSample] = []
    seen: set[int] = set()
    for line in text.splitlines():
        match = _ROW_RE.match(line)
        if not match:
            continue
        member_id = int(match.group("member"))
        if member_id in seen:
            continue
        seen.add(member_id)
        first_token = match.group("rest").split()[0]
        role = first_token if first_token.lower() in _KNOWN_ROLES else None
        members.append(IrfMemberSample(member_id=member_id, role=role))
    return members


def parse_display_irf(text: str) -> list[IrfMemberSample]:
    """Parse `display irf` member table rows; unparseable lines are skipped."""

    return _member_rows(text)


def parse_display_irf_configuration(text: str) -> list[IrfMemberSample]:
    """Parse `display irf configuration` (member id + role/priority columns).

    Member *count* is the critical value for §17; roles stay None when the
    output shape differs from expectation until real evidence fixes it.
    """

    return _member_rows(text)
