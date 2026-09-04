"""Normalized collection DTOs (Wave 1).

These dataclasses are the stable boundary between transports/collectors and
everything downstream (persistence in W01-T006, metrics in Wave 2). They
contain only parsed, normalized values — never secrets and never raw device
text that could carry credential material.
"""

import re
from dataclasses import dataclass

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_interface_name(name: str) -> str:
    """Stable business interface identity (SYSTEM_SPEC.md §10).

    Lower-cases and collapses whitespace so a device reporting the same
    interface with different abbreviation/spacing still maps to one row.
    ifIndex is deliberately NOT part of the identity.
    """

    return _WHITESPACE_RE.sub(" ", name.strip()).lower()


@dataclass(frozen=True)
class DeviceIdentity:
    """Identity section (SNMPv2-MIB system group)."""

    sys_name: str | None
    sys_description: str | None
    sys_object_id: str | None
    # sysUpTime in hundredths of a second, normalized to seconds.
    uptime_seconds: float | None


@dataclass(frozen=True)
class EntityLoadSample:
    """One CPU or memory usage reading for one physical entity.

    `entity_index` is the source-table index (e.g. entPhysical-style index
    from the H3C entity-ext table). `member_id` is filled only when the
    mapping to an IRF member number is known from reliable source data;
    unknown mapping stays None instead of being guessed.
    """

    entity_index: int
    usage_percent: float
    member_id: int | None = None


@dataclass(frozen=True)
class DeviceSoftwareInfo:
    """Software identity from `display version` (SYSTEM_SPEC.md §7.2).

    `version` is the Comware software version (e.g. "7.1.070"); `release`
    is the release tag (e.g. "7596P10"); `model` is the chassis model token
    (e.g. "S10508X"). Anything the output does not identify stays None.
    """

    version: str | None = None
    release: str | None = None
    model: str | None = None

    @property
    def software_version(self) -> str | None:
        """Persisted form: "version Release release" or just version."""

        if self.version is None:
            return None
        if self.release is None:
            return self.version
        return f"{self.version} Release {self.release}"


@dataclass(frozen=True)
class InterfaceSample:
    """One interface from the IF-MIB tables (SYSTEM_SPEC.md §10/§15/§16).

    Counters are cumulative 64-bit values when the device provides the HC
    variant, falling back to 32-bit otherwise; None when the device reports
    no-such-instance for that column.
    """

    if_index: int
    name: str
    normalized_name: str
    description: str | None
    admin_state: str | None  # RFC 2863 ifAdminStatus vocabulary or None
    oper_state: str | None  # RFC 2863 ifOperStatus vocabulary or None
    speed_bps: int | None
    in_octets: int | None
    out_octets: int | None
    in_errors: int | None
    out_errors: int | None
    in_discards: int | None
    out_discards: int | None
    # Cumulative CRC/FCS receive-error counter (EtherLike-MIB
    # dot3HCStatsFCSErrors, fallback dot3StatsFCSErrors; SYSTEM_SPEC.md §16).
    fcs_errors: int | None = None
    if_type: int | None = None


@dataclass(frozen=True)
class IrfMemberSample:
    """One IRF member (chassis) as observed over SSH (SYSTEM_SPEC.md §17).

    `role` is the role string exactly as the device reports it (e.g.
    "Master"/"Slave"/"Backup"); it stays None when the source output does
    not identify roles reliably — never guessed.
    """

    member_id: int
    role: str | None = None
    model: str | None = None
    software_version: str | None = None


@dataclass(frozen=True)
class AggregationMapping:
    """aggregation interface -> physical member interfaces (SYSTEM_SPEC.md §11)."""

    aggregation_if_index: int
    member_if_indexes: tuple[int, ...]
