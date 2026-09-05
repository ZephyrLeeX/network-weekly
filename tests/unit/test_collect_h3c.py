"""Unit tests for the H3C section parsers, driven by fixture files.

The fixtures are synthetic placeholders (see tests/fixtures/h3c/README.md);
they pin parser behavior until W01-T007 replaces them with anonymized
real-device captures. Secrets never appear in fixtures or DTOs.
"""

import json
from pathlib import Path

import pytest

from backend.collect.dto import normalize_interface_name
from backend.collect.h3c import oids
from backend.collect.h3c.collectors import (
    CollectionSectionError,
    collect_cpu,
    collect_interfaces,
    collect_memory,
    parse_aggregations,
    parse_entity_usage,
    parse_identity,
    parse_interfaces,
)
from backend.collect.snmp import SnmpError, SnmpVarbind

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "h3c"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _varbinds(raw: list[dict]) -> list[SnmpVarbind]:
    return [SnmpVarbind(oid=item["oid"], value=item["value"]) for item in raw]


def test_parse_identity_from_fixture() -> None:
    data = _load("identity_s10500x.json")
    identity = parse_identity(_varbinds(data["varbinds"]))
    assert identity.sys_name == "core-s10500x-01"
    assert identity.sys_description is not None and "Comware" in identity.sys_description
    assert identity.sys_object_id == "1.3.6.1.4.1.25506.1.617"
    assert identity.uptime_seconds == pytest.approx(123456.0)


def test_parse_entity_usage_cpu_and_memory() -> None:
    cpu = _load("cpu_s10500x.json")
    samples = parse_entity_usage(_varbinds(cpu["varbinds"]), cpu["column"], "cpu")
    assert [(s.entity_index, s.usage_percent) for s in samples] == [
        (67108994, 12.0),
        (67117450, 9.0),
    ]
    assert all(s.member_id is None for s in samples)  # no guessed member mapping

    mem = _load("memory_s10500x.json")
    mem_samples = parse_entity_usage(_varbinds(mem["varbinds"]), mem["column"], "memory")
    assert [s.usage_percent for s in mem_samples] == [34.0, 41.0]


def test_parse_entity_usage_ignores_non_numeric() -> None:
    samples = parse_entity_usage(
        [SnmpVarbind(oid=f"{oids.HH3C_ENTITY_EXT_CPU_USAGE}.1", value="n/a")],
        oids.HH3C_ENTITY_EXT_CPU_USAGE,
        "cpu",
    )
    assert samples == []


def test_parse_interfaces_from_fixture() -> None:
    data = _load("iftable_s10500x.json")
    varbinds = {col: _varbinds(items) for col, items in data["columns"].items()}
    samples = parse_interfaces(varbinds)
    by_name = {s.normalized_name: s for s in samples}

    ten1 = by_name[normalize_interface_name("Ten-GigabitEthernet1/0/1")]
    assert ten1.if_index == 1
    assert ten1.description == "to-server-farm"
    assert ten1.admin_state == "up"
    assert ten1.oper_state == "up"
    # ifHighSpeed (Mbps) preferred over the saturated 32-bit ifSpeed.
    assert ten1.speed_bps == 10_000_000_000
    assert ten1.in_octets == 123456789012  # 64-bit HC counter preferred
    assert ten1.out_octets == 456789012345
    assert ten1.in_errors == 0
    assert ten1.out_errors == 0
    assert ten1.fcs_errors == 7  # HC FCS counter preferred
    assert ten1.if_type == 6

    ten2 = by_name[normalize_interface_name("Ten-GigabitEthernet1/0/2")]
    assert ten2.oper_state == "down"
    assert ten2.in_octets == 0
    assert ten2.in_errors == 3
    assert ten2.out_errors == 1
    assert ten2.fcs_errors == 3

    ten49 = by_name[normalize_interface_name("Ten-GigabitEthernet1/0/49")]
    assert ten49.speed_bps == 40_000_000_000
    assert ten49.fcs_errors == 42  # 32-bit fallback when HC absent

    agg = by_name[normalize_interface_name("Bridge-Aggregation1")]
    assert agg.if_index == 65
    assert agg.if_type == 161
    assert agg.speed_bps == 20_000_000_000

    vlan100 = by_name[normalize_interface_name("Vlan-interface100")]
    assert vlan100.speed_bps == 1_000_000_000
    assert vlan100.fcs_errors is None

    assert normalize_interface_name("InLoopBack0") in by_name


def test_parse_interfaces_tolerates_missing_columns() -> None:
    data = _load("iftable_s10500x.json")
    # Only ifDescr available: rows exist, every other field degrades to None.
    varbinds = {"1.3.6.1.2.1.2.2.1.2": _varbinds(data["columns"]["1.3.6.1.2.1.2.2.1.2"])}
    samples = parse_interfaces(varbinds)
    assert len(samples) == 6
    assert all(s.oper_state is None and s.in_octets is None for s in samples)


def test_admin_and_oper_status_maps_are_separate() -> None:
    # ifAdminStatus (RFC 2863): up(1)/down(2)/testing(3) only.
    assert oids.IF_ADMIN_STATUS_MAP == {1: "up", 2: "down", 3: "testing"}
    # ifOperStatus (RFC 2863): 1..7 with the extended states.
    assert oids.IF_OPER_STATUS_MAP == {
        1: "up",
        2: "down",
        3: "testing",
        4: "unknown",
        5: "dormant",
        6: "notpresent",
        7: "lowerlayerdown",
    }


def test_parse_interfaces_maps_oper_status_1_to_7() -> None:
    descr = [SnmpVarbind(oid=f"{oids.IF_DESCR}.{i}", value=f"XGE{i}") for i in range(1, 8)]
    oper = [
        SnmpVarbind(oid=f"{oids.IF_OPER_STATUS}.{i}", value=i) for i in range(1, 8)
    ]
    admin = [SnmpVarbind(oid=f"{oids.IF_ADMIN_STATUS}.{i}", value=2) for i in range(1, 8)]
    samples = parse_interfaces(
        {oids.IF_DESCR: descr, oids.IF_OPER_STATUS: oper, oids.IF_ADMIN_STATUS: admin}
    )
    assert [s.oper_state for s in samples] == [
        "up",
        "down",
        "testing",
        "unknown",
        "dormant",
        "notpresent",
        "lowerlayerdown",
    ]
    assert all(s.admin_state == "down" for s in samples)


def test_parse_interfaces_ignores_out_of_range_status() -> None:
    samples = parse_interfaces(
        {
            oids.IF_DESCR: [SnmpVarbind(oid=f"{oids.IF_DESCR}.1", value="XGE1")],
            oids.IF_OPER_STATUS: [SnmpVarbind(oid=f"{oids.IF_OPER_STATUS}.1", value=9)],
            oids.IF_ADMIN_STATUS: [SnmpVarbind(oid=f"{oids.IF_ADMIN_STATUS}.1", value=9)],
        }
    )
    assert samples[0].oper_state is None
    assert samples[0].admin_state is None


class _FakeSnmpClient:
    """bulk_walk stub: map column OID -> varbinds or exception."""

    def __init__(self, columns: dict[str, list[SnmpVarbind] | Exception]) -> None:
        self._columns = columns
        self.walked: list[str] = []

    def bulk_walk(self, column: str) -> list[SnmpVarbind]:
        self.walked.append(column)
        result = self._columns[column]
        if isinstance(result, Exception):
            raise result
        return result


def test_collect_interfaces_ifdescr_walk_failure_is_section_error() -> None:
    data = _load("iftable_s10500x.json")
    columns: dict[str, list[SnmpVarbind] | Exception] = {
        col: _varbinds(items) for col, items in data["columns"].items()
    }
    columns[oids.IF_DESCR] = SnmpError("walk timeout")
    client = _FakeSnmpClient(columns)
    with pytest.raises(CollectionSectionError, match="ifDescr walk failed"):
        collect_interfaces(client)  # type: ignore[arg-type]
    # The remaining columns were still attempted before failing.
    assert oids.IF_OPER_STATUS in client.walked


def test_collect_interfaces_empty_result_is_section_error() -> None:
    data = _load("iftable_s10500x.json")
    columns: dict[str, list[SnmpVarbind] | Exception] = {
        col: _varbinds(items) for col, items in data["columns"].items()
    }
    columns[oids.IF_DESCR] = []  # successful walk, but no interface rows
    with pytest.raises(CollectionSectionError, match="no usable interface data"):
        collect_interfaces(_FakeSnmpClient(columns))  # type: ignore[arg-type]


def test_collect_interfaces_success_with_full_data() -> None:
    data = _load("iftable_s10500x.json")
    client = _FakeSnmpClient({col: _varbinds(items) for col, items in data["columns"].items()})
    collection = collect_interfaces(client)  # type: ignore[arg-type]
    assert len(collection.samples) == 6
    assert collection.missing_groups == ()  # every key group delivered
    assert all(col in client.walked for col in (oids.IF_HIGH_SPEED, oids.DOT3_HC_STATS_FCS_ERRORS))


def test_collect_interfaces_degraded_when_state_columns_missing() -> None:
    """Both ifAdminStatus and ifOperStatus unavailable -> DEGRADED, data kept."""

    data = _load("iftable_s10500x.json")
    columns: dict[str, list[SnmpVarbind] | Exception] = {
        col: _varbinds(items) for col, items in data["columns"].items()
    }
    columns[oids.IF_ADMIN_STATUS] = SnmpError("walk timeout")
    columns[oids.IF_OPER_STATUS] = SnmpError("walk timeout")
    collection = collect_interfaces(_FakeSnmpClient(columns))  # type: ignore[arg-type]
    assert len(collection.samples) == 6  # the ifDescr-driven rows survive
    assert collection.missing_groups == ("state",)


def test_collect_interfaces_degraded_when_speed_and_counters_missing() -> None:
    data = _load("iftable_s10500x.json")
    columns: dict[str, list[SnmpVarbind] | Exception] = {
        col: _varbinds(items) for col, items in data["columns"].items()
    }
    for column in (
        oids.IF_SPEED,
        oids.IF_HIGH_SPEED,
        oids.IF_IN_OCTETS,
        oids.IF_OUT_OCTETS,
        oids.IF_HC_IN_OCTETS,
        oids.IF_HC_OUT_OCTETS,
    ):
        columns[column] = []
    collection = collect_interfaces(_FakeSnmpClient(columns))  # type: ignore[arg-type]
    assert collection.missing_groups == ("speed", "octets")


def test_collect_interfaces_not_degraded_on_32bit_fallback() -> None:
    """HC columns absent but 32-bit fallbacks present: no degradation."""

    data = _load("iftable_s10500x.json")
    columns: dict[str, list[SnmpVarbind] | Exception] = {
        col: _varbinds(items) for col, items in data["columns"].items()
    }
    columns[oids.IF_HC_IN_OCTETS] = []
    columns[oids.IF_HC_OUT_OCTETS] = []
    columns[oids.DOT3_HC_STATS_FCS_ERRORS] = []
    collection = collect_interfaces(_FakeSnmpClient(columns))  # type: ignore[arg-type]
    assert collection.missing_groups == ()


def test_collect_cpu_empty_result_is_section_error() -> None:
    with pytest.raises(CollectionSectionError, match="no cpu usage samples"):
        collect_cpu(_FakeSnmpClient({oids.HH3C_ENTITY_EXT_CPU_USAGE: []}))  # type: ignore[arg-type]


def test_collect_memory_non_numeric_only_is_section_error() -> None:
    varbinds = [SnmpVarbind(oid=f"{oids.HH3C_ENTITY_EXT_MEM_USAGE}.1", value="n/a")]
    with pytest.raises(CollectionSectionError, match="no memory usage samples"):
        collect_memory(_FakeSnmpClient({oids.HH3C_ENTITY_EXT_MEM_USAGE: varbinds}))  # type: ignore[arg-type]


def test_parse_aggregations_from_fixture() -> None:
    data = _load("lag_s10500x.json")
    mappings = parse_aggregations(_varbinds(data["varbinds"]))
    # Ports 49/50 attached to agg 65; port 51 selected (not yet attached)
    # into agg 66 via the selected fallback; ports 1/2 are 0 = no membership.
    assert [(m.aggregation_if_index, m.member_if_indexes) for m in mappings] == [
        (65, (49, 50)),
        (66, (51,)),
    ]


def test_lag_oids_match_ieee8023_lag_mib() -> None:
    # IEEE 802.1AX IEEE8023-LAG-MIB dot3AggPortTable columns (verified
    # against the published MIB module; H3C MIB Companion implements it).
    assert oids.DOT3_AGG_PORT_SELECTED_AGG_ID == "1.2.840.10006.300.43.1.2.1.1.12"
    assert oids.DOT3_AGG_PORT_ATTACHED_AGG_ID == "1.2.840.10006.300.43.1.2.1.1.13"


def test_parse_aggregations_attached_takes_precedence() -> None:
    varbinds = [
        # selected into 65 but attached to 66: attached wins.
        SnmpVarbind(oid=f"{oids.DOT3_AGG_PORT_SELECTED_AGG_ID}.10", value=65),
        SnmpVarbind(oid=f"{oids.DOT3_AGG_PORT_ATTACHED_AGG_ID}.10", value=66),
    ]
    mappings = parse_aggregations(varbinds)
    assert [(m.aggregation_if_index, m.member_if_indexes) for m in mappings] == [(66, (10,))]


def test_parse_aggregations_skips_agg_itself() -> None:
    varbinds = [
        SnmpVarbind(oid=f"{oids.DOT3_AGG_PORT_ATTACHED_AGG_ID}.65", value=65),
    ]
    assert parse_aggregations(varbinds) == []


def test_column_index() -> None:
    assert oids.column_index("1.3.6.1.2.1.2.2.1.2.10", oids.IF_DESCR) == 10
    assert oids.column_index("1.3.6.1.2.1.2.2.1.2", oids.IF_DESCR) is None
    assert oids.column_index("1.3.6.1.999", oids.IF_DESCR) is None
