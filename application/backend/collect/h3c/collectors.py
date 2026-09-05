"""H3C collectors: fetch (client-bound) + parse (pure) for each section.

Parse functions are pure and unit-tested against anonymized real-device
fixtures (W01-T007). Fetch functions only wrap the transport. No function
here handles configuration changes or business statistics (§7.4).
"""

import logging
from dataclasses import dataclass

from backend.collect.dto import (
    AggregationMapping,
    DeviceIdentity,
    EntityLoadSample,
    InterfaceSample,
    normalize_interface_name,
)
from backend.collect.h3c import oids
from backend.collect.snmp import SnmpClient, SnmpVarbind

logger = logging.getLogger(__name__)


class CollectionSectionError(RuntimeError):
    """One collection section failed; other sections may still succeed."""

    def __init__(self, section: str, reason: str) -> None:
        super().__init__(f"{section} section failed: {reason}")
        self.section = section


def _values_by_suffix(varbinds: list[SnmpVarbind], column: str) -> dict[int, object]:
    """Map table column instances to values keyed by row index."""

    keyed: dict[int, object] = {}
    for vb in varbinds:
        index = oids.column_index(vb.oid, column)
        if index is not None:
            keyed[index] = vb.value
    return keyed


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


# --- identity ------------------------------------------------------------------


def parse_identity(varbinds: list[SnmpVarbind]) -> DeviceIdentity:
    by_oid = {vb.oid: vb.value for vb in varbinds}
    uptime = by_oid.get(oids.SYS_UP_TIME)
    return DeviceIdentity(
        sys_name=_clean_text(by_oid.get(oids.SYS_NAME)),
        sys_description=_clean_text(by_oid.get(oids.SYS_DESCR)),
        sys_object_id=_clean_text(by_oid.get(oids.SYS_OBJECT_ID)),
        uptime_seconds=int(uptime) / 100 if isinstance(uptime, int) else None,
    )


def collect_identity(client: SnmpClient) -> DeviceIdentity:
    return parse_identity(
        client.get([oids.SYS_DESCR, oids.SYS_OBJECT_ID, oids.SYS_UP_TIME, oids.SYS_NAME])
    )


# --- CPU / memory (H3C entity-ext) -----------------------------------------------


def parse_entity_usage(
    varbinds: list[SnmpVarbind], column: str, section: str
) -> list[EntityLoadSample]:
    samples: list[EntityLoadSample] = []
    for index, value in sorted(_values_by_suffix(varbinds, column).items()):
        if value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            logger.warning("ignoring non-numeric %s usage for entity index %d", section, index)
            continue
        samples.append(EntityLoadSample(entity_index=index, usage_percent=float(value)))
    return samples


def collect_cpu(client: SnmpClient) -> list[EntityLoadSample]:
    """Walk the CPU usage column; an empty result is a section FAILURE (§8).

    A walk that succeeds but reports no usable value means the section did
    not collect what this cycle needs — reporting SUCCESS would fake a
    healthy cycle with no CPU data at all (the H3C enterprise OIDs are
    themselves pending W01-T007 confirmation, so silence is a real risk).
    """

    samples = parse_entity_usage(
        client.bulk_walk(oids.HH3C_ENTITY_EXT_CPU_USAGE), oids.HH3C_ENTITY_EXT_CPU_USAGE, "cpu"
    )
    if not samples:
        raise CollectionSectionError("cpu", "no cpu usage samples collected")
    return samples


def collect_memory(client: SnmpClient) -> list[EntityLoadSample]:
    """Walk the memory usage column; an empty result is a section FAILURE (§8)."""

    samples = parse_entity_usage(
        client.bulk_walk(oids.HH3C_ENTITY_EXT_MEM_USAGE),
        oids.HH3C_ENTITY_EXT_MEM_USAGE,
        "memory",
    )
    if not samples:
        raise CollectionSectionError("memory", "no memory usage samples collected")
    return samples


# --- interfaces ------------------------------------------------------------------


def parse_interfaces(varbinds: dict[str, list[SnmpVarbind]]) -> list[InterfaceSample]:
    """Assemble interface rows from per-column walk results.

    `varbinds` maps a column OID to that column's walk result. Columns a
    device does not implement may be missing entirely or contain None
    values — each field degrades independently so one absent column never
    discards the other collected interface data (§8 PARTIAL semantics).

    Speed prefers ifHighSpeed (Mbps, RFC 2863) — authoritative on links
    faster than 4.29 Gb/s where ifSpeed saturates — and falls back to
    ifSpeed (bps) only when ifHighSpeed is absent.
    """

    def keyed(column: str) -> dict[int, object]:
        return _values_by_suffix(varbinds.get(column, []), column)

    def admin_status(admins: dict[int, object], if_index: int) -> str | None:
        value = admins.get(if_index)
        return oids.IF_ADMIN_STATUS_MAP.get(value) if isinstance(value, int) else None

    def oper_status(opers: dict[int, object], if_index: int) -> str | None:
        value = opers.get(if_index)
        return oids.IF_OPER_STATUS_MAP.get(value) if isinstance(value, int) else None

    def count(values: dict[int, object], if_index: int) -> int | None:
        value = values.get(if_index)
        return value if isinstance(value, int) else None

    def speed_bps(if_index: int) -> int | None:
        high = count(high_speed, if_index)
        if high is not None:
            return high * 1_000_000  # ifHighSpeed is Mbps (RFC 2863)
        return count(speed, if_index)

    descr = keyed(oids.IF_DESCR)
    if_type = keyed(oids.IF_TYPE)
    speed = keyed(oids.IF_SPEED)
    high_speed = keyed(oids.IF_HIGH_SPEED)
    admin = keyed(oids.IF_ADMIN_STATUS)
    oper = keyed(oids.IF_OPER_STATUS)
    hc_in = keyed(oids.IF_HC_IN_OCTETS)
    hc_out = keyed(oids.IF_HC_OUT_OCTETS)
    in_octets = keyed(oids.IF_IN_OCTETS)
    out_octets = keyed(oids.IF_OUT_OCTETS)
    in_errors = keyed(oids.IF_IN_ERRORS)
    out_errors = keyed(oids.IF_OUT_ERRORS)
    in_discards = keyed(oids.IF_IN_DISCARDS)
    out_discards = keyed(oids.IF_OUT_DISCARDS)
    hc_fcs = keyed(oids.DOT3_HC_STATS_FCS_ERRORS)
    fcs = keyed(oids.DOT3_STATS_FCS_ERRORS)
    alias = keyed(oids.IF_ALIAS)

    samples: list[InterfaceSample] = []
    for if_index in sorted(descr):
        name = descr[if_index]
        if not isinstance(name, str) or not name.strip():
            logger.warning("interface %d has no usable ifDescr; skipping", if_index)
            continue
        # 64-bit counters when available, 32-bit fallback, None when absent.
        in_oct = count(hc_in, if_index) if if_index in hc_in else count(in_octets, if_index)
        out_oct = count(hc_out, if_index) if if_index in hc_out else count(out_octets, if_index)
        fcs_errors = (
            count(hc_fcs, if_index) if if_index in hc_fcs else count(fcs, if_index)
        )
        samples.append(
            InterfaceSample(
                if_index=if_index,
                name=name,
                normalized_name=normalize_interface_name(name),
                description=_clean_text(alias.get(if_index)),
                admin_state=admin_status(admin, if_index),
                oper_state=oper_status(oper, if_index),
                speed_bps=speed_bps(if_index),
                in_octets=in_oct,
                out_octets=out_oct,
                in_errors=count(in_errors, if_index),
                out_errors=count(out_errors, if_index),
                in_discards=count(in_discards, if_index),
                out_discards=count(out_discards, if_index),
                fcs_errors=fcs_errors,
                if_type=count(if_type, if_index),
            )
        )
    return samples


# Key interface fields (§7.1, §13–§16): one entry per field the weekly
# reports need, listing the columns that can deliver it (the HC variant OR
# the 32-bit fallback of the SAME field). A field counts as collected only
# when at least one of its own columns delivered rows; if ANY required
# field's columns are all unavailable, the section is DEGRADED — the
# collected samples are kept, but the cycle must not count as a full
# SUCCESS. Fields are judged independently: "the errors columns delivered
# something" no longer stands in for a missing octets or state column.
# (ifAdminStatus is deliberately absent — §13 Down/Recovery is decided from
# ifOperStatus alone, and admin state is informational.)
_INTERFACE_KEY_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # §13 Down/Recovery is decided from oper state.
    ("oper_state", (oids.IF_OPER_STATUS,)),
    # §15 utilization needs speed and both octet directions.
    ("speed", (oids.IF_HIGH_SPEED, oids.IF_SPEED)),
    ("in_octets", (oids.IF_HC_IN_OCTETS, oids.IF_IN_OCTETS)),
    ("out_octets", (oids.IF_HC_OUT_OCTETS, oids.IF_OUT_OCTETS)),
    # §16 error/discard observation, judged per direction.
    ("in_errors", (oids.IF_IN_ERRORS,)),
    ("out_errors", (oids.IF_OUT_ERRORS,)),
    ("in_discards", (oids.IF_IN_DISCARDS,)),
    ("out_discards", (oids.IF_OUT_DISCARDS,)),
)


@dataclass(frozen=True)
class InterfaceCollection:
    """Interfaces-section result: the assembled samples plus any key field
    that could not be collected (§8 degraded semantics).

    `samples` is kept whatever the degradation — one missing column never
    discards the interface data other columns already delivered. The caller
    turns non-empty `missing_fields` into a DEGRADED section (poll run
    PARTIAL) so Coverage does not dress the cycle up as a full SUCCESS.
    """

    samples: list[InterfaceSample]
    missing_fields: tuple[str, ...] = ()


def collect_interfaces(client: SnmpClient) -> InterfaceCollection:
    """Walk the IF-MIB/EtherLike columns for all interfaces.

    The ifDescr walk is the section's core: when it fails, or when no
    usable interface row can be assembled from a successful walk, the
    section FAILED — never a SUCCESS with empty data (§8). A usable row set
    with missing key fields is returned as DEGRADED data instead:
    partial column failure keeps the samples and degrades the section.
    """

    columns = (
        oids.IF_DESCR,
        oids.IF_TYPE,
        oids.IF_SPEED,
        oids.IF_HIGH_SPEED,
        oids.IF_ADMIN_STATUS,
        oids.IF_OPER_STATUS,
        oids.IF_IN_OCTETS,
        oids.IF_OUT_OCTETS,
        oids.IF_IN_ERRORS,
        oids.IF_OUT_ERRORS,
        oids.IF_IN_DISCARDS,
        oids.IF_OUT_DISCARDS,
        oids.IF_HC_IN_OCTETS,
        oids.IF_HC_OUT_OCTETS,
        oids.DOT3_HC_STATS_FCS_ERRORS,
        oids.DOT3_STATS_FCS_ERRORS,
        oids.IF_ALIAS,
    )
    varbinds: dict[str, list[SnmpVarbind]] = {}
    descr_error: Exception | None = None
    for column in columns:
        try:
            varbinds[column] = client.bulk_walk(column)
        except Exception as exc:  # noqa: BLE001  (one column failing must not kill the rest)
            if column == oids.IF_DESCR:
                descr_error = exc
            logger.warning("interface column %s unavailable (%s)", column, type(exc).__name__)
            varbinds[column] = []

    if descr_error is not None:
        raise CollectionSectionError(
            "interfaces", f"core ifDescr walk failed: {type(descr_error).__name__}"
        )
    samples = parse_interfaces(varbinds)
    if not samples:
        raise CollectionSectionError("interfaces", "no usable interface data collected")
    missing = tuple(
        name
        for name, columns in _INTERFACE_KEY_FIELDS
        if not any(varbinds.get(column) for column in columns)
    )
    return InterfaceCollection(samples=samples, missing_fields=missing)


# --- aggregation membership ------------------------------------------------------


def parse_aggregations(varbinds: list[SnmpVarbind]) -> list[AggregationMapping]:
    """Port -> aggregation membership from IEEE8023-LAG-MIB (IEEE 802.1AX).

    Membership follows the H3C/IEEE semantics of the two columns:

    - dot3adAggPortAttachedAggID (.13): the aggregation the port is
      actually attached to / bundling into — the authoritative membership
      relation and therefore preferred.
    - dot3adAggPortSelectedAggID (.12): the aggregation the selection logic
      selected the port into; used as fallback for ports that are selected
      but not (yet) attached (e.g. during LACP negotiation), so intended
      membership is not lost.

    0 means "not selected/attached". The aggregation id equals the
    aggregation interface's ifIndex on Comware (pending real-device
    confirmation in W01-T004/T007).
    """

    attached = _values_by_suffix(varbinds, oids.DOT3_AGG_PORT_ATTACHED_AGG_ID)
    selected = _values_by_suffix(varbinds, oids.DOT3_AGG_PORT_SELECTED_AGG_ID)

    grouped: dict[int, list[int]] = {}
    for port_index in sorted(set(attached) | set(selected)):
        # Attached is authoritative; selected only fills the gap.
        agg_id = attached.get(port_index)
        if not isinstance(agg_id, int) or agg_id == 0:
            agg_id = selected.get(port_index)
        if not isinstance(agg_id, int) or agg_id == 0:
            continue
        if agg_id == port_index:
            # The aggregation interface itself; not a membership record.
            continue
        grouped.setdefault(agg_id, []).append(port_index)
    return [
        AggregationMapping(aggregation_if_index=agg_id, member_if_indexes=tuple(sorted(members)))
        for agg_id, members in sorted(grouped.items())
    ]


def collect_aggregations(client: SnmpClient) -> list[AggregationMapping]:
    """Walk both LAG membership columns.

    Only a successful walk returning an empty (or absent) table yields an
    empty mapping — that is a legitimate "device reports no aggregation"
    and drives stale-membership cleanup. A failing walk raises so the
    section is FAILED and no stale data is ever cleared on failure.
    """

    attached = client.bulk_walk(oids.DOT3_AGG_PORT_ATTACHED_AGG_ID)
    try:
        selected = client.bulk_walk(oids.DOT3_AGG_PORT_SELECTED_AGG_ID)
    except Exception:  # noqa: BLE001  (attached succeeded; membership stays authoritative)
        logger.warning("dot3adAggPortSelectedAggID walk unavailable; using attached only")
        selected = []
    return parse_aggregations(attached + selected)
