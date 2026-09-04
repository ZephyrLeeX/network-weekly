"""SSH static collectors: allowlisted `display` commands -> DTOs (W01-T005).

Fetch functions run the read-only allowlisted commands via
:class:`H3CSshClient` and normalize the output with the pure parsers in
:mod:`backend.collect.h3c.ssh_parsers`. They collect identity/version/IRF
static data only — never metrics, and never configuration changes
(SYSTEM_SPEC.md §7.2/§7.4).

Callers decide *when* to run these (the static collection is deliberately
not part of the 5-minute DEVICE_POLL; the IRF part is sized for the ~15
minute IRF observation cadence of SYSTEM_SPEC.md §7.3 / Wave 2).
"""

import logging

from backend.collect.dto import DeviceSoftwareInfo, IrfMemberSample
from backend.collect.h3c.ssh_parsers import (
    parse_display_irf,
    parse_display_irf_configuration,
    parse_display_version,
)
from backend.collect.ssh import H3CSshClient, SshError

logger = logging.getLogger(__name__)


def collect_software_info(client: H3CSshClient) -> DeviceSoftwareInfo:
    """Device software identity from `display version`."""

    parsed = parse_display_version(client.run("display version"))
    return DeviceSoftwareInfo(
        version=parsed["version"], release=parsed["release"], model=parsed["model"]
    )


def collect_irf_members(client: H3CSshClient) -> list[IrfMemberSample]:
    """IRF member id/role from `display irf` (+ `display irf configuration`).

    `display irf` carries the live member table and roles; `display irf
    configuration` contributes additional member ids when its table shape
    is recognized. Roles come only from `display irf` — never guessed.
    Both commands are optional to succeed, but at least one member row
    must be found; otherwise the collection is treated as failed.
    """

    members: dict[int, IrfMemberSample] = {}
    try:
        for member in parse_display_irf(client.run("display irf")):
            members[member.member_id] = member
    except SshError as exc:
        logger.warning("display irf unavailable: %s", exc)

    try:
        for member in parse_display_irf_configuration(client.run("display irf configuration")):
            if member.member_id not in members:
                members[member.member_id] = member
    except SshError as exc:
        logger.warning("display irf configuration unavailable: %s", exc)

    if not members:
        raise SshError("no IRF member rows recognized in display irf output")
    return [members[member_id] for member_id in sorted(members)]
