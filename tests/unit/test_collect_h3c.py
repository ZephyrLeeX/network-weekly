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
    parse_aggregations,
    parse_entity_usage,
    parse_identity,
    parse_interfaces,
)
from backend.collect.snmp import SnmpVarbind

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
    assert ten1.speed_bps == 10_000_000_000
    assert ten1.in_octets == 123456789012  # 64-bit HC counter preferred
    assert ten1.out_octets == 456789012345
    assert ten1.in_errors == 0
    assert ten1.out_errors == 0
    assert ten1.if_type == 6

    ten2 = by_name[normalize_interface_name("Ten-GigabitEthernet1/0/2")]
    assert ten2.oper_state == "down"
    assert ten2.in_octets == 0
    assert ten2.in_errors == 3
    assert ten2.out_errors == 1

    agg = by_name[normalize_interface_name("Bridge-Aggregation1")]
    assert agg.if_index == 65
    assert agg.if_type == 161

    assert normalize_interface_name("InLoopBack0") in by_name


def test_parse_interfaces_tolerates_missing_columns() -> None:
    data = _load("iftable_s10500x.json")
    # Only ifDescr available: rows exist, every other field degrades to None.
    varbinds = {"1.3.6.1.2.1.2.2.1.2": _varbinds(data["columns"]["1.3.6.1.2.1.2.2.1.2"])}
    samples = parse_interfaces(varbinds)
    assert len(samples) == 6
    assert all(s.oper_state is None and s.in_octets is None for s in samples)


def test_parse_aggregations_from_fixture() -> None:
    data = _load("lag_s10500x.json")
    mappings = parse_aggregations(_varbinds(data["varbinds"]))
    assert len(mappings) == 1
    mapping = mappings[0]
    assert mapping.aggregation_if_index == 65
    assert mapping.member_if_indexes == (49, 50)


def test_column_index() -> None:
    assert oids.column_index("1.3.6.1.2.1.2.2.1.2.10", oids.IF_DESCR) == 10
    assert oids.column_index("1.3.6.1.2.1.2.2.1.2", oids.IF_DESCR) is None
    assert oids.column_index("1.3.6.1.999", oids.IF_DESCR) is None
