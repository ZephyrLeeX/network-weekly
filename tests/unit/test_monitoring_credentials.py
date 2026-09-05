"""Unit tests for device poll-context building (W02-T002)."""

from types import SimpleNamespace
from typing import cast

from sqlalchemy.orm import Session

from backend.monitoring.credentials import build_contexts, build_unpollable_contexts

SECRET_VALUES = {
    "SNMP_COMMUNITY_DEFAULT": "c0mmunity",
    "SSH_USERNAME_DEFAULT": "monitor",
    "SSH_PASSWORD_DEFAULT": "s3cret",
    "SNMP_COMMUNITY_S12500": "other-community",
}


def _device(name: str, profile: str = "default", enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        name=name,
        management_ip="192.0.2.1",
        credential_profile=profile,
        enabled=enabled,
    )


class _FakeSession:
    """Duck-typed stand-in for the one query `build_contexts` issues."""

    def __init__(self, devices: list[SimpleNamespace]) -> None:
        self._devices = devices

    def execute(self, _query: object) -> _FakeResult:
        return _FakeResult(self._devices)


class _FakeResult:
    def __init__(self, devices: list[SimpleNamespace]) -> None:
        self._devices = devices

    def scalars(self) -> list[SimpleNamespace]:
        return self._devices


def _session(devices: list[SimpleNamespace]) -> Session:
    return cast(Session, _FakeSession(devices))


def test_context_builds_snmp_and_ssh() -> None:
    contexts = build_contexts(_session([_device("dev-1")]), SECRET_VALUES)
    assert len(contexts) == 1
    context = contexts[0]
    assert context.device_name == "dev-1"
    assert context.snmp is not None
    assert context.snmp.host == "192.0.2.1"
    assert context.snmp.community == "c0mmunity"
    assert context.ssh is not None
    assert context.ssh.username == "monitor"
    assert context.ssh.password == "s3cret"


def test_device_without_ssh_credentials_polls_snmp_only() -> None:
    secrets = {"SNMP_COMMUNITY_DEFAULT": "c0mmunity"}
    contexts = build_contexts(_session([_device("dev-1")]), secrets)
    assert contexts[0].snmp is not None
    assert contexts[0].snmp.community == "c0mmunity"
    assert contexts[0].ssh is None  # no §9.1 confirmation available, but pollable


def test_device_without_snmp_community_is_unattemptable_not_dropped() -> None:
    """§8: the device keeps its planned cycles — recorded FAILED, not lost."""

    contexts = build_contexts(_session([_device("dev-1", profile="missing")]), SECRET_VALUES)
    assert len(contexts) == 1
    context = contexts[0]
    assert context.snmp is None
    assert context.ssh is None
    assert context.unavailable_reason is not None
    assert "SNMP_COMMUNITY_MISSING" in context.unavailable_reason  # key name, never a value


def test_unpollable_contexts_cover_every_enabled_device() -> None:
    """Secrets unusable at all: every enabled device stays attributable (§8)."""

    devices = [_device("a"), _device("b")]
    contexts = build_unpollable_contexts(_session(devices), reason="secrets file not found: x")
    assert len(contexts) == 2
    assert all(context.snmp is None for context in contexts)
    assert all(context.unavailable_reason == "secrets file not found: x" for context in contexts)
    assert [context.device_name for context in contexts] == ["a", "b"]


def test_profiles_map_to_their_own_secrets() -> None:
    contexts = build_contexts(
        _session([_device("core", profile="s12500")]), SECRET_VALUES
    )
    assert contexts[0].snmp is not None
    assert contexts[0].snmp.community == "other-community"
