"""H3C collectors: fetch (client-bound) + parse (pure) for each section.

Parse functions are pure and unit-tested against anonymized real-device
fixtures (W01-T007). Fetch functions only wrap the transport. No function
here handles configuration changes or business statistics (§7.4).
"""

import logging

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
    return parse_entity_usage(
        client.bulk_walk(oids.HH3C_ENTITY_EXT_CPU_USAGE), oids.HH3C_ENTITY_EXT_CPU_USAGE, "cpu"
    )


def collect_memory(client: SnmpClient) -> list[EntityLoadSample]:
    return parse_entity_usage(
        client.bulk_walk(oids.HH3C_ENTITY_EXT_MEM_USAGE), oids.HH3C_ENTITY_EXT_MEM_USAGE, "memory"
    )


# --- interfaces ------------------------------------------------------------------


def parse_interfaces(varbinds: dict[str, list[SnmpVarbind]]) -> list[InterfaceSample]:
    """Assemble interface rows from per-column walk results.

    `varbinds` maps a column OID to that column's walk result. Columns a
    device does not implement may be missing entirely or contain None
    values — each field degrades independently so one absent column never
    discards the other collected interface data (§8 PARTIAL semantics).
    """

    def keyed(column: str) -> dict[int, object]:
        return _values_by_suffix(varbinds.get(column, []), column)

    def status(statuses: dict[int, object], if_index: int) -> str | None:
        value = statuses.get(if_index)
        return oids.IF_STATUS.get(value) if isinstance(value, int) else None

    def count(values: dict[int, object], if_index: int) -> int | None:
        value = values.get(if_index)
        return value if isinstance(value, int) else None

    descr = keyed(oids.IF_DESCR)
    if_type = keyed(oids.IF_TYPE)
    speed = keyed(oids.IF_SPEED)
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
        samples.append(
            InterfaceSample(
                if_index=if_index,
                name=name,
                normalized_name=normalize_interface_name(name),
                description=_clean_text(alias.get(if_index)),
                admin_state=status(admin, if_index),
                oper_state=status(oper, if_index),
                speed_bps=count(speed, if_index),
                in_octets=in_oct,
                out_octets=out_oct,
                in_errors=count(in_errors, if_index),
                out_errors=count(out_errors, if_index),
                in_discards=count(in_discards, if_index),
                out_discards=count(out_discards, if_index),
                if_type=count(if_type, if_index),
            )
        )
    return samples


def collect_interfaces(client: SnmpClient) -> list[InterfaceSample]:
    columns = (
        oids.IF_DESCR,
        oids.IF_TYPE,
        oids.IF_SPEED,
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
        oids.IF_ALIAS,
    )
    varbinds: dict[str, list[SnmpVarbind]] = {}
    for column in columns:
        try:
            varbinds[column] = client.bulk_walk(column)
        except Exception as exc:  # noqa: BLE001  (one column failing must not kill the rest)
            logger.warning("interface column %s unavailable (%s)", column, type(exc).__name__)
            varbinds[column] = []
    return parse_interfaces(varbinds)


# --- aggregation membership ------------------------------------------------------


def parse_aggregations(varbinds: list[SnmpVarbind]) -> list[AggregationMapping]:
    """Port -> selected aggregation mapping from dot3AggPortSelectedAggID.

    Port ifIndex -> aggregation id (aggregation id equals the aggregation
    interface's ifIndex on Comware — pending real-device verification in
    W01-T004). Ports with id 0 are not attached to any aggregation.
    """

    selected = _values_by_suffix(varbinds, oids.DOT3_AGG_PORT_SELECTED_AGG_ID)
    grouped: dict[int, list[int]] = {}
    for port_index, agg_id in sorted(selected.items()):
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
    return parse_aggregations(client.bulk_walk(oids.DOT3_AGG_PORT_SELECTED_AGG_ID))
