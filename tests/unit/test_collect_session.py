"""Unit tests for collection orchestration and section isolation (W01-T006)."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from backend.collect.dto import (
    DeviceIdentity,
    InterfaceSample,
    normalize_interface_name,
)
from backend.collect.session import (
    FAILED,
    PARTIAL,
    SUCCESS,
    DeviceCollectionOutcome,
    run_collection,
)
from backend.collect.snmp import SnmpConfig, SnmpError
from backend.collect.ssh import SshConfig

SNMP = SnmpConfig(host="192.0.2.1", community="unused")
SSH = SshConfig(host="192.0.2.1", username="user", password="unused")

ALL_SECTIONS = ("identity", "cpu", "memory", "interfaces", "aggregations")


def _interface_sample() -> InterfaceSample:
    name = "Ten-GigabitEthernet1/0/1"
    return InterfaceSample(
        if_index=1,
        name=name,
        normalized_name=normalize_interface_name(name),
        description=None,
        admin_state="up",
        oper_state="up",
        speed_bps=10_000_000_000,
        in_octets=None,
        out_octets=None,
        in_errors=None,
        out_errors=None,
        in_discards=None,
        out_discards=None,
    )


def _stub_collect(monkeypatch: pytest.MonkeyPatch, failures: dict[str, Exception]) -> None:
    """Script each section collector: raise, or return minimal valid data."""

    def scripted(name: str, ok_result: object) -> Callable[[object], object]:
        def inner(client: object) -> object:
            if name in failures:
                raise failures[name]
            return ok_result

        return inner

    monkeypatch.setattr(
        "backend.collect.session.collect_identity",
        scripted("identity", DeviceIdentity(None, None, None, None)),
    )
    monkeypatch.setattr("backend.collect.session.collect_cpu", scripted("cpu", []))
    monkeypatch.setattr("backend.collect.session.collect_memory", scripted("memory", []))
    monkeypatch.setattr(
        "backend.collect.session.collect_interfaces",
        scripted("interfaces", [_interface_sample()]),
    )
    monkeypatch.setattr(
        "backend.collect.session.collect_aggregations", scripted("aggregations", [])
    )


@contextmanager
def patch_ssh_reachable(value: bool) -> Iterator[None]:
    with patch("backend.collect.session.H3CSshClient") as ssh_cls:
        ssh_cls.return_value.check_reachable.return_value = value
        yield


def test_all_sections_succeed_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_collect(monkeypatch, failures={})
    outcome = run_collection(SNMP, None, "core-s10500x-01")
    assert outcome.overall_status == SUCCESS
    assert outcome.failed_sections == ()
    assert outcome.interfaces is not None and len(outcome.interfaces) == 1
    assert outcome.identity is not None


def test_single_section_failure_is_partial_and_keeps_valid_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_collect(monkeypatch, failures={"memory": SnmpError("timeout")})
    outcome = run_collection(SNMP, None, "core-s10500x-01")
    assert outcome.overall_status == PARTIAL
    assert outcome.failed_sections == ("memory",)
    assert outcome.memory is None
    assert outcome.interfaces is not None  # valid data preserved
    memory_section = next(s for s in outcome.sections if s.name == "memory")
    assert memory_section.status == FAILED
    assert "timeout" in (memory_section.error or "")


def test_all_sections_failed_is_failed_and_probes_ssh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_collect(
        monkeypatch, failures={name: SnmpError("timeout") for name in ALL_SECTIONS}
    )
    with patch_ssh_reachable(True):
        outcome = run_collection(SNMP, SSH, "core-s10500x-01")
    assert outcome.overall_status == FAILED
    assert outcome.ssh_reachable is True
    assert outcome.interfaces is None


def test_unexpected_section_error_is_reduced_to_class_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_collect(monkeypatch, failures={"identity": ValueError("sysName leak attempt")})
    outcome = run_collection(SNMP, None, "core-s10500x-01")
    identity_section = next(s for s in outcome.sections if s.name == "identity")
    assert identity_section.status == FAILED
    assert identity_section.error == "ValueError"
    assert "leak" not in (identity_section.error or "")


def test_ssh_probe_skipped_when_snmp_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_collect(monkeypatch, failures={})
    outcome = run_collection(SNMP, SSH, "core-s10500x-01")
    assert outcome.ssh_reachable is None


def test_outcome_defaults_and_status_rules() -> None:
    empty = DeviceCollectionOutcome(device_name="x")
    assert empty.overall_status == FAILED
    assert empty.has_valid_data() is False
