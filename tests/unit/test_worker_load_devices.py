"""Unit tests for the worker's per-cycle device loading (W02 audit).

§8: when the secrets file cannot be loaded at all, the cycle is not lost —
every enabled device keeps an unattemptable context whose planned cycle
`poll_device` records as a FAILED poll run.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from sqlalchemy.orm import sessionmaker

from backend.collect.snmp import SnmpConfig
from backend.monitoring.poll import DevicePollContext
from backend.secrets import SecretsError
from backend.worker import _load_devices


class _FakeSession:
    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _FakeFactory:
    def __call__(self) -> _FakeSession:
        return _FakeSession()


def _factory() -> sessionmaker:
    return cast(sessionmaker, _FakeFactory())


def test_secrets_failure_yields_unattemptable_contexts(
    monkeypatch: SimpleNamespace,
) -> None:
    captured: dict[str, str] = {}

    def fake_unpollable(session: object, *, reason: str) -> list[DevicePollContext]:
        captured["reason"] = reason
        return [
            DevicePollContext(
                device_id=7,
                device_name="dev-7",
                snmp=None,
                unavailable_reason=reason,
            )
        ]

    def broken_loader(_path: Path) -> object:
        raise SecretsError("secrets file not found: /etc/network-report/secrets.env")

    monkeypatch.setattr("backend.worker.load_secrets", broken_loader)
    monkeypatch.setattr("backend.worker.build_unpollable_contexts", fake_unpollable)

    contexts = _load_devices(_factory(), Path("/etc/network-report/secrets.env"))

    assert len(contexts) == 1
    assert contexts[0].device_id == 7
    assert contexts[0].snmp is None
    assert contexts[0].unavailable_reason == (
        "secrets file not found: /etc/network-report/secrets.env"
    )
    assert captured["reason"].startswith("secrets file not found")


def test_healthy_secrets_build_normal_contexts(monkeypatch: SimpleNamespace) -> None:
    expected = [
        DevicePollContext(
            device_id=1,
            device_name="dev-1",
            snmp=SnmpConfig(host="192.0.2.1", community="public"),
        )
    ]
    monkeypatch.setattr("backend.worker.load_secrets", lambda _path: {"K": "V"})

    def fake_build(session: object, secrets: object) -> list[DevicePollContext]:
        assert secrets == {"K": "V"}
        return expected

    monkeypatch.setattr("backend.worker.build_contexts", fake_build)

    assert _load_devices(_factory(), Path("/x")) == expected
