"""Unit tests for the bounded SNMP transport behavior (no real sockets).

`SnmpClient` is exercised through overridden `_run_get`/`_run_bulk` hooks so
error normalization, subtree-end detection and the walk deadline are proven
without network access. Real-device reachability is validated in W01-T007.
"""

import pytest
from pysnmp.proto import rfc1902, rfc1905

from backend.collect.snmp import (
    SnmpClient,
    SnmpConfig,
    SnmpError,
    SnmpVarbind,
    _is_missing,
    _normalize_varbinds,
    _to_python,
)


class FakeClient(SnmpClient):
    """SnmpClient with scripted `_run_get`/`_run_bulk` hooks (no sockets)."""

    def __init__(self, config: SnmpConfig, **kwargs: object) -> None:
        super().__init__(config, **kwargs)  # type: ignore[arg-type]
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


def test_real_pysnmp_missing_values_normalize_without_rfc1905_null() -> None:
    """PySNMP 7.1 keeps Null in rfc1902, unlike SNMP exception values."""

    assert not hasattr(rfc1905, "Null")
    values = (
        rfc1902.Null(""),
        rfc1905.NoSuchObject(""),
        rfc1905.NoSuchInstance(""),
        rfc1905.EndOfMibView(""),
    )
    for value in values:
        assert _is_missing(value)
        assert _to_python(value) is None


@pytest.mark.parametrize(
    "value_type",
    [
        rfc1902.Integer,
        rfc1902.Integer32,
        rfc1902.Gauge32,
        rfc1902.Counter32,
        rfc1902.Counter64,
        rfc1902.TimeTicks,
        rfc1902.Unsigned32,
    ],
)
def test_real_pysnmp_integer_values_normalize_to_int(value_type: type[object]) -> None:
    assert _to_python(value_type(42)) == 42  # type: ignore[call-arg]


def test_real_pysnmp_text_and_ip_normalization_is_unchanged() -> None:
    assert _to_python(rfc1902.OctetString("core-switch")) == "core-switch"
    ip_address = rfc1902.IpAddress("192.0.2.10")
    assert _to_python(ip_address) == str(ip_address)


def test_transport_boundary_normalizes_real_pysnmp_get_and_bulk_values() -> None:
    """The shared GET/GETBULK path handles real values without network I/O."""

    binds = [
        (rfc1902.ObjectIdentifier("1.3.6.1.2.1.1.5.0"), rfc1902.OctetString("core")),
        (rfc1902.ObjectIdentifier("1.3.6.1.2.1.1.3.0"), rfc1902.TimeTicks(123)),
        (rfc1902.ObjectIdentifier("1.3.6.1.2.1.2.2.1.8.9"), rfc1902.Null("")),
        (
            rfc1902.ObjectIdentifier("1.3.6.1.2.1.2.2.1.10.9"),
            rfc1905.NoSuchInstance(""),
        ),
    ]

    assert _normalize_varbinds(binds) == [
        SnmpVarbind("1.3.6.1.2.1.1.5.0", "core"),
        SnmpVarbind("1.3.6.1.2.1.1.3.0", 123),
        SnmpVarbind("1.3.6.1.2.1.2.2.1.8.9", None),
        SnmpVarbind("1.3.6.1.2.1.2.2.1.10.9", None),
    ]


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


def test_whole_poll_deadline_stops_before_another_request() -> None:
    now = [0.0]
    client = FakeClient(
        SnmpConfig(host="192.0.2.1", community="unused"),
        absolute_deadline=1.0,
        monotonic_clock=lambda: now[0],
    )
    base = "1.3.6.1.2.1.2.2.1.2"

    async def one_then_expire(
        start_oid: str, *, lexicographic_mode: bool
    ) -> tuple[object, int, list[SnmpVarbind]]:
        client.bulk_calls += 1
        now[0] = 2.0
        return None, 0, [SnmpVarbind(f"{base}.1", "eth0")]

    client._run_bulk = one_then_expire  # type: ignore[method-assign]
    with pytest.raises(SnmpError, match="device poll deadline"):
        client.bulk_walk(base)
    assert client.bulk_calls == 1
