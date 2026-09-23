"""Configuration discovery reads only metadata and topology columns."""

from backend.collect.h3c import oids
from backend.collect.h3c.collectors import collect_aggregations, collect_interface_inventory
from backend.collect.snmp import SnmpVarbind


class RecordingClient:
    def __init__(self) -> None:
        self.walked: list[str] = []

    def bulk_walk(self, column: str) -> list[SnmpVarbind]:
        self.walked.append(column)
        if column == oids.IF_DESCR:
            return [SnmpVarbind(f"{column}.7", "Ten-GigabitEthernet1/0/1")]
        if column == oids.DOT3_AGG_PORT_ATTACHED_AGG_ID:
            return [SnmpVarbind(f"{column}.7", 10)]
        return []


def test_inventory_walks_only_page_metadata_and_topology() -> None:
    client = RecordingClient()
    samples = collect_interface_inventory(client)  # type: ignore[arg-type]
    mappings = collect_aggregations(client)  # type: ignore[arg-type]
    assert samples[0].normalized_name == "ten-gigabitethernet1/0/1"
    assert mappings[0].aggregation_if_index == 10
    assert set(client.walked) == {
        oids.IF_DESCR,
        oids.IF_ALIAS,
        oids.IF_ADMIN_STATUS,
        oids.IF_OPER_STATUS,
        oids.IF_SPEED,
        oids.IF_HIGH_SPEED,
        oids.DOT3_AGG_PORT_ATTACHED_AGG_ID,
        oids.DOT3_AGG_PORT_SELECTED_AGG_ID,
    }
    assert not set(client.walked) & {
        oids.IF_IN_ERRORS,
        oids.IF_OUT_ERRORS,
        oids.IF_IN_DISCARDS,
        oids.IF_OUT_DISCARDS,
        oids.IF_HC_IN_OCTETS,
        oids.IF_HC_OUT_OCTETS,
        oids.DOT3_STATS_FCS_ERRORS,
    }
