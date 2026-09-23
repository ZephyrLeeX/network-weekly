"""Safe, non-persistent manual collection CLI tests."""

from contextlib import nullcontext
from dataclasses import replace

import pytest

from backend.collect.dto import DeviceIdentity, EntityLoadSample
from backend.collect.session import (
    FAILED,
    PARTIAL,
    SUCCESS,
    DeviceCollectionOutcome,
    SectionResult,
)
from backend.collect.snmp import SnmpConfig
from backend.config import Settings
from backend.manual_poll import _select_contexts, main, run_diagnostics
from backend.monitoring.poll import DevicePollContext


def _context(name: str, device_id: int, *, pollable: bool = True) -> DevicePollContext:
    return DevicePollContext(
        device_id=device_id,
        device_name=name,
        snmp=SnmpConfig(host=f"192.0.2.{device_id}", community="do-not-print")
        if pollable
        else None,
        unavailable_reason="missing secret do-not-print" if not pollable else None,
    )


def _outcome(name: str, status: str = SUCCESS) -> DeviceCollectionOutcome:
    outcome = DeviceCollectionOutcome(device_name=name)
    outcome.sections.append(
        SectionResult(
            name="identity",
            status=SUCCESS if status == SUCCESS else FAILED,
            error=None if status == SUCCESS else "safe failure",
        )
    )
    if status == PARTIAL:
        outcome.sections.append(SectionResult(name="cpu", status=SUCCESS))
    outcome.identity = DeviceIdentity("core", None, "1.3.6.1.4.1.25506", 123.0)
    outcome.cpu = [EntityLoadSample(1, 12.0)] if status == PARTIAL else []
    return outcome


def test_select_single_device_and_all_use_stable_enabled_order() -> None:
    contexts = [_context("z-device", 2), _context("a-device", 1)]
    assert [item.device_name for item in _select_contexts(contexts, ["z-device"])] == [
        "z-device"
    ]
    assert [item.device_name for item in _select_contexts(contexts, None)] == [
        "a-device",
        "z-device",
    ]


def test_unknown_device_is_rejected_before_collection() -> None:
    with pytest.raises(ValueError, match="not enabled or does not exist"):
        _select_contexts([_context("enabled", 1)], ["disabled-or-missing"])


def test_parser_requires_exclusive_selection() -> None:
    with pytest.raises(SystemExit) as missing:
        main([])
    assert missing.value.code == 2
    with pytest.raises(SystemExit) as both:
        main(["--all", "--device", "core"])
    assert both.value.code == 2


def test_main_loads_database_contexts_and_selects_one_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.manual_poll as module

    contexts = [_context("enabled-b", 2), _context("enabled-a", 1)]
    collected: list[str] = []
    monkeypatch.setattr(module, "load_settings", lambda: Settings())
    monkeypatch.setattr(module, "setup_logging", lambda _settings: None)
    monkeypatch.setattr(module, "load_secrets", lambda _path: {"loaded": "secret"})
    monkeypatch.setattr(module, "get_session_factory", lambda: lambda: nullcontext(object()))
    monkeypatch.setattr(module, "build_contexts", lambda _session, _secrets: contexts)

    def collect(
        _snmp: SnmpConfig,
        _ssh: object,
        name: str,
        *,
        deadline_seconds: float,
    ) -> DeviceCollectionOutcome:
        collected.append(name)
        assert deadline_seconds == 240
        return _outcome(name)

    assert main(["--device", "enabled-b"], collect=collect) == 0
    assert collected == ["enabled-b"]


def test_diagnostics_pass_deadline_and_stagger_and_render_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    contexts = [_context("b", 2), _context("a", 1)]
    calls: list[tuple[str, float]] = []
    sleeps: list[float] = []

    def collect(
        _snmp: SnmpConfig,
        _ssh: object,
        name: str,
        *,
        deadline_seconds: float,
    ) -> DeviceCollectionOutcome:
        calls.append((name, deadline_seconds))
        return _outcome(name)

    settings = replace(Settings(), poll_deadline_seconds=240, poll_stagger_seconds=20)
    result = run_diagnostics(
        _select_contexts(contexts, None), settings, collect=collect, sleeper=sleeps.append
    )

    assert result == 0
    assert calls == [("a", 240), ("b", 240)]
    assert sleeps == [20]
    output = capsys.readouterr().out
    assert "DEVICE: a" in output
    assert "STATUS: SUCCESS" in output
    assert "FAILED_SECTIONS: none" in output
    assert "sys_name: core" in output
    assert "CPU_SAMPLES: 0" in output


@pytest.mark.parametrize("status", [PARTIAL, FAILED])
def test_partial_or_failed_diagnostic_returns_one_and_safe_summary(
    status: str, capsys: pytest.CaptureFixture[str]
) -> None:
    result = run_diagnostics(
        [_context("core", 1)],
        Settings(),
        collect=lambda *_args, **_kwargs: _outcome("core", status),
    )
    assert result == 1
    output = capsys.readouterr().out
    assert f"STATUS: {status}" in output
    assert "FAILED_SECTIONS: identity" in output


def test_missing_snmp_credentials_never_calls_collection_or_prints_secret(
    capsys: pytest.CaptureFixture[str],
) -> None:
    called = False

    def collect(*_args: object, **_kwargs: object) -> DeviceCollectionOutcome:
        nonlocal called
        called = True
        raise AssertionError("must not collect")

    result = run_diagnostics([_context("core", 1, pollable=False)], Settings(), collect=collect)
    captured = capsys.readouterr()
    assert result == 1
    assert not called
    assert "SNMP credentials unavailable" in captured.err
    assert "do-not-print" not in captured.out + captured.err


def test_runtime_exception_is_reduced_to_class_name_and_no_secret(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def collect(*_args: object, **_kwargs: object) -> DeviceCollectionOutcome:
        raise RuntimeError("community=do-not-print password=also-secret")

    result = run_diagnostics([_context("core", 1)], Settings(), collect=collect)
    captured = capsys.readouterr()
    assert result == 1
    assert "RuntimeError" in captured.err
    assert "do-not-print" not in captured.out + captured.err
    assert "also-secret" not in captured.out + captured.err


def test_manual_module_has_no_persistence_or_state_machine_dependencies() -> None:
    import backend.manual_poll as module

    forbidden = (
        "poll_device",
        "persist_collection",
        "persist_poll_result",
        "apply_device_reachability",
        "record_interface_states",
        "apply_irf_observation",
    )
    assert all(not hasattr(module, name) for name in forbidden)
