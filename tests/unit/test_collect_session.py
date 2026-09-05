"""Unit tests for collection orchestration and section isolation (W01-T006)."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from backend.collect.dto import (
    DeviceIdentity,
    DeviceSoftwareInfo,
    InterfaceSample,
    IrfMemberSample,
    normalize_interface_name,
)
from backend.collect.h3c.collectors import CollectionSectionError, InterfaceCollection
from backend.collect.session import (
    DEGRADED,
    FAILED,
    PARTIAL,
    SUCCESS,
    DeviceCollectionOutcome,
    run_collection,
    run_irf_observation,
    run_static_ssh_collection,
)
from backend.collect.snmp import SnmpConfig, SnmpError
from backend.collect.ssh import SshConfig, SshError

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
def patch_ssh_reachable(value: bool) -> Iterator[object]:
    with patch("backend.collect.session.H3CSshClient") as ssh_cls:
        ssh_cls.return_value.check_reachable.return_value = value
        yield ssh_cls


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


def test_degraded_interfaces_section_is_partial_and_keeps_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing key field degrades the section: samples kept, cycle PARTIAL.

    One required field no column delivered must not let the cycle pass as
    SUCCESS — but the interface data other columns already delivered still
    persists (§8 degraded semantics).
    """

    sample = _interface_sample()

    def degraded_interfaces(client: object) -> object:
        return InterfaceCollection(samples=[sample], missing_fields=("out_discards",))

    _stub_collect(monkeypatch, failures={})
    monkeypatch.setattr("backend.collect.session.collect_interfaces", degraded_interfaces)
    outcome = run_collection(SNMP, None, "core-s10500x-01")

    interfaces_section = next(s for s in outcome.sections if s.name == "interfaces")
    assert interfaces_section.status == DEGRADED
    assert "out_discards" in (interfaces_section.error or "")
    assert outcome.interfaces == [sample]  # collected data survives
    assert outcome.overall_status == PARTIAL  # never dressed up as SUCCESS
    assert outcome.failed_sections == ("interfaces",)
    # No SNMP channel failure: valid interface data proves the channel worked.
    assert outcome.ssh_reachable is None


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


def test_ssh_probe_skipped_on_single_section_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CPU/memory/LAG failure alone must NOT trigger SSH (§9.1)."""

    for failing in ("cpu", "memory", "aggregations", "interfaces", "identity"):
        _stub_collect(monkeypatch, failures={failing: SnmpError("timeout")})
        with patch_ssh_reachable(True) as ssh_cls:
            outcome = run_collection(SNMP, SSH, "core-s10500x-01")
        assert outcome.ssh_reachable is None, f"probe ran for single {failing} failure"
        assert ssh_cls.call_count == 0  # type: ignore[attr-defined]


def test_ssh_probe_decision_follows_valid_data_not_failure_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe is keyed on 'no valid data', not on the number of failures."""

    # identity + interfaces failed (the two core sections), but CPU and
    # memory succeeded — the SNMP channel demonstrably works: no probe.
    _stub_collect(
        monkeypatch,
        failures={"identity": SnmpError("timeout"), "interfaces": SnmpError("timeout")},
    )
    with patch_ssh_reachable(True) as ssh_cls:
        outcome = run_collection(SNMP, SSH, "core-s10500x-01")
    assert outcome.overall_status == PARTIAL
    assert outcome.ssh_reachable is None
    assert ssh_cls.call_count == 0  # type: ignore[attr-defined]


def test_section_collection_error_message_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CollectionSectionError (e.g. interfaces) keeps its secret-free reason."""

    _stub_collect(monkeypatch, failures={})
    monkeypatch.setattr(
        "backend.collect.session.collect_interfaces",
        lambda client: (_ for _ in ()).throw(
            CollectionSectionError("interfaces", "core ifDescr walk failed: SnmpError")
        ),
    )
    outcome = run_collection(SNMP, None, "core-s10500x-01")
    interfaces_section = next(s for s in outcome.sections if s.name == "interfaces")
    assert interfaces_section.status == FAILED
    assert "ifDescr walk failed" in (interfaces_section.error or "")
    assert outcome.overall_status == PARTIAL


def test_outcome_defaults_and_status_rules() -> None:
    empty = DeviceCollectionOutcome(device_name="x")
    assert empty.overall_status == FAILED
    assert empty.has_valid_data() is False


def test_static_ssh_collection_success() -> None:
    software = DeviceSoftwareInfo(version="7.1.070", release="7596P10", model="S10508X")
    members = [
        IrfMemberSample(member_id=1, role="Master"),
        IrfMemberSample(member_id=2, role="Slave"),
    ]
    with patch("backend.collect.session.collect_software_info_ssh", return_value=software):
        with patch("backend.collect.session.collect_irf_members_ssh", return_value=members):
            outcome = run_static_ssh_collection(SSH, "core-s10500x-irf")
    assert outcome.software == software
    assert outcome.irf_members == members
    assert outcome.overall_status == SUCCESS
    assert outcome.has_valid_data() is True


def test_static_ssh_collection_section_isolation() -> None:
    members = [IrfMemberSample(member_id=1, role="Master")]
    with patch(
        "backend.collect.session.collect_software_info_ssh",
        side_effect=SshError("ssh command failed: OSError"),
    ):
        with patch("backend.collect.session.collect_irf_members_ssh", return_value=members):
            outcome = run_static_ssh_collection(SSH, "core-s10500x-irf")
    software_section = next(s for s in outcome.sections if s.name == "software")
    assert software_section.status == FAILED
    assert outcome.software is None
    assert outcome.irf_members == members  # IRF section still succeeded
    assert outcome.overall_status == PARTIAL


def test_run_irf_observation_returns_members() -> None:
    members = [IrfMemberSample(member_id=1, role="Master")]
    with patch("backend.collect.session.collect_irf_members_ssh", return_value=members):
        assert run_irf_observation(SSH, "core-s10500x-irf") == members


def test_run_irf_observation_failure_returns_none() -> None:
    with patch(
        "backend.collect.session.collect_irf_members_ssh",
        side_effect=SshError("ssh connect failed"),
    ):
        assert run_irf_observation(SSH, "core-s10500x-irf") is None
