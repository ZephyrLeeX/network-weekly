"""Parsers for the allowlisted H3C SSH `display` outputs (W01-T005).

Pure text -> DTO parsing, unit-tested against anonymized real-output shapes
(see tests/fixtures/h3c/README.md). Comware output varies between versions,
so parsing is deliberately tolerant: anything not recognized with confidence
stays None or is skipped rather than guessed.
"""

import re

from backend.collect.dto import IrfMemberSample

# Comware `display version` software line. Verified shapes (H3C Comware 7
# S10500/S12500 documentation):
#   "H3C Comware Software, Version 7.1.070, Release 7596P10"
#   "H3C Comware Platform Software, Software Version 7.1.070, Release 7510P21"
_VERSION_RE = re.compile(r"Version\s+(?P<version>\d[\w.]*)", re.IGNORECASE)
_RELEASE_RE = re.compile(r"Release\s+(?P<release>\d[\w.-]*)", re.IGNORECASE)
# Model line: "H3C S10510X uptime is ..." / "H3C S12508G-AF uptime is ...".
# A model may carry one or more bounded alphanumeric hyphen suffixes, but a
# space ends the model so ordinary text such as "uptime" can never be eaten.
_MODEL_RE = re.compile(
    r"^\s*H3C\s+(?P<model>S\d+[A-Z0-9]*(?:-[A-Z0-9]+)*)\b", re.MULTILINE
)

# Table rows. Leading IRF markers (`*` = master, `+` = logged-in member) may
# precede the member id in live `display irf` output.
_ROW_RE = re.compile(r"^\s*[*+]*\s*(?P<member>\d+)\s+(?P<rest>\S.*?)\s*$")

# Role tokens accepted verbatim. Kept to the documented Comware IRF role
# vocabulary (Master/Slave, plus Backup/Standby/Loading as reported by
# Comware releases); anything else leaves role as None instead of guessing.
_KNOWN_ROLES = {"master", "slave", "backup", "standby", "loading"}
_CANONICAL_ROLES = {
    role.lower(): role for role in ("Master", "Slave", "Backup", "Standby", "Loading")
}


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


def _table_lines(text: str, *, required_headers: set[str]) -> list[str]:
    """Return candidate rows following the matching IRF table header."""

    lines = text.splitlines()
    for index, line in enumerate(lines):
        headers = {token.lower() for token in line.split()}
        if required_headers <= headers:
            return lines[index + 1 :]
    return []


def _parse_display_irf_rows(text: str) -> list[IrfMemberSample]:
    """Parse and aggregate live rows shaped as MemberID Slot Role ... ."""

    roles_by_member: dict[int, set[str]] = {}
    for line in _table_lines(text, required_headers={"memberid", "slot", "role"}):
        match = _ROW_RE.match(line)
        if not match:
            continue
        tokens = match.group("rest").split()
        if len(tokens) < 2:
            continue
        member_id = int(match.group("member"))
        roles = roles_by_member.setdefault(member_id, set())
        role_token = tokens[1].lower()
        if role_token in _KNOWN_ROLES:
            roles.add(_CANONICAL_ROLES[role_token])

    members: list[IrfMemberSample] = []
    for member_id, roles in sorted(roles_by_member.items()):
        if "Master" in roles:
            role = "Master"
        elif len(roles) == 1:
            role = next(iter(roles))
        else:
            # No recognized role, or conflicting non-Master live roles: do
            # not invent a ranking or infer a chassis role from slot order.
            role = None
        members.append(IrfMemberSample(member_id=member_id, role=role))
    return members


def _parse_irf_configuration_member_ids(text: str) -> list[IrfMemberSample]:
    """Parse only member ids from configuration; its other columns are not roles."""

    member_ids: set[int] = set()
    for line in _table_lines(text, required_headers={"memberid"}):
        match = _ROW_RE.match(line)
        if match:
            member_ids.add(int(match.group("member")))
    return [IrfMemberSample(member_id=member_id, role=None) for member_id in sorted(member_ids)]


def parse_display_irf(text: str) -> list[IrfMemberSample]:
    """Parse live MemberID/Slot/Role rows and aggregate MPU slots per member."""

    return _parse_display_irf_rows(text)


def parse_display_irf_configuration(text: str) -> list[IrfMemberSample]:
    """Parse only member ids from `display irf configuration`.

    NewID, Priority and IRF-Port columns never carry a live member role.
    """

    return _parse_irf_configuration_member_ids(text)
