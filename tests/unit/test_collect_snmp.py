"""Unit tests for the bounded SNMP transport behavior (no real sockets).

`SnmpClient` is exercised through overridden `_run_get`/`_run_bulk` hooks so
error normalization, subtree-end detection and the walk deadline are proven
without network access. Real-device reachability is validated in W01-T007.
"""

import pytest

from backend.collect.snmp import SnmpClient, SnmpConfig, SnmpError, SnmpVarbind


class FakeClient(SnmpClient):
    """SnmpClient with scripted `_run_get`/`_run_bulk` hooks (no sockets)."""

    def __init__(self, config: SnmpConfig) -> None:
        super().__init__(config)
        self.get_script: list[tuple[object, int, list[SnmpVarbind]]] = []
        self.walk_script: list[tuple[object, int, list[SnmpVarbind]]] = []
        self.get_calls = 0
        self.bulk_calls = 0

    async def _run_get(self, oids: list[str]) -> tuple[object, int, list[SnmpVarbind]]:
        result = self.get_script[min(self.get_calls, len(self.get_script) - 1)]
        self.get_calls += 1
        return result

    async def _run_bulk(
        self, start_oid: str, *, lexicographic_mode: bool
    ) -> tuple[object, int, list[SnmpVarbind]]:
        result = self.walk_script[min(self.bulk_calls, len(self.walk_script) - 1)]
        self.bulk_calls += 1
        return result


def _client() -> FakeClient:
    return FakeClient(SnmpConfig(host="192.0.2.1", community="unused-in-tests"))


def test_get_normalizes_agent_error() -> None:
    client = _client()
    client.get_script = [("agent shutdown", 0, [])]
    with pytest.raises(SnmpError, match="agent shutdown"):
        client.get(["1.3.6.1.2.1.1.1.0"])


def test_get_normalizes_error_status() -> None:
    client = _client()
    client.get_script = [(None, 2, [])]
    with pytest.raises(SnmpError, match="error-status 2"):
        client.get(["1.3.6.1.2.1.1.1.0"])


def test_get_empty_oids_short_circuits() -> None:
    client = _client()
    assert client.get([]) == []
    assert client.get_calls == 0


def test_get_transport_oserror_normalized() -> None:
    client = _client()

    async def boom(oids: list[str]) -> tuple[object, int, list[SnmpVarbind]]:
        raise OSError("network down")

    client._run_get = boom  # type: ignore[method-assign]
    with pytest.raises(SnmpError, match="OSError"):
        client.get(["1.3.6.1.2.1.1.1.0"])


def test_bulk_walk_stops_at_subtree_end() -> None:
    client = _client()
    base = "1.3.6.1.2.1.2.2.1.2"
    client.walk_script = [
        (None, 0, [SnmpVarbind(f"{base}.1", "eth0"), SnmpVarbind(f"{base}.2", "eth1")]),
        (None, 0, [SnmpVarbind(f"{base}.3", "eth2")]),
        (None, 0, [SnmpVarbind("1.3.7.0.0", "beyond")]),  # outside subtree -> stop
    ]
    result = client.bulk_walk(base)
    assert [vb.value for vb in result] == ["eth0", "eth1", "eth2"]
    assert client.bulk_calls == 3


def test_bulk_walk_stops_on_error() -> None:
    client = _client()
    base = "1.3.6.1.2.1.2.2.1.2"
    client.walk_script = [
        (None, 0, [SnmpVarbind(f"{base}.1", "eth0")]),
        ("request timed out", 0, []),
    ]
    with pytest.raises(SnmpError, match="request timed out"):
        client.bulk_walk(base)


def test_bulk_walk_deadline_is_enforced() -> None:
    client = FakeClient(
        SnmpConfig(host="192.0.2.1", community="unused", walk_deadline_seconds=0.05)
    )
    base = "1.3.6.1.2.1.2.2.1.2"
    # Endless subtree: every response continues the walk.
    client.walk_script = [
        (None, 0, [SnmpVarbind(f"{base}.{i}", "row")]) for i in range(1, 200)
    ]
    with pytest.raises(SnmpError, match="deadline"):
        client.bulk_walk(base)
