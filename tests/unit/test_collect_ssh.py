"""Unit tests for the bounded SSH transport and IRF parsers (W01-T005).

No real SSH connections: transport behavior is proven with a stubbed
ConnectHandler; parsers run against fixture files that are synthetic
placeholders pending anonymized real captures (see README).
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.collect.h3c.ssh_parsers import (
    parse_display_irf,
    parse_display_irf_configuration,
    parse_display_version,
)
from backend.collect.ssh import ALLOWED_COMMANDS, H3CSshClient, SshConfig, SshError

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "h3c"


def _fixture_text(name: str) -> str:
    return json.loads((FIXTURES / name).read_text())["text"]


def _client() -> H3CSshClient:
    return H3CSshClient(
        SshConfig(host="192.0.2.1", username="user", password="unused-in-tests")
    )


class _FakeConnection:
    def __init__(self, output: str = "ok") -> None:
        self.output = output
        self.disconnected = False

    def send_command(self, command: str, read_timeout: float) -> str:
        assert read_timeout > 0
        return self.output

    def disconnect(self) -> None:
        self.disconnected = True


def test_run_rejects_non_allowlisted_command() -> None:
    with pytest.raises(SshError, match="allowlist"):
        _client().run("system-view")


def test_run_rejects_allowlist_bypass_attempts() -> None:
    assert all(cmd.startswith("display ") for cmd in ALLOWED_COMMANDS)


def test_run_disconnects_even_on_success() -> None:
    conn = _FakeConnection("version output")
    with patch("backend.collect.ssh.ConnectHandler", return_value=conn):
        assert _client().run("display version") == "version output"
    assert conn.disconnected


def test_run_error_is_normalized_and_disconnects() -> None:
    class _Broken:
        def send_command(self, command: str, read_timeout: float) -> str:
            raise OSError("connection dropped (would embed credentials)")

        def disconnect(self) -> None:
            self.disconnected = True

    conn = _Broken()
    with patch("backend.collect.ssh.ConnectHandler", return_value=conn):
        with pytest.raises(SshError, match="OSError") as exc_info:
            _client().run("display version")
    # The exception message must not carry the underlying error text.
    assert "credentials" not in str(exc_info.value)
    assert conn.disconnected


def test_connect_failure_is_normalized() -> None:
    with patch("backend.collect.ssh.ConnectHandler", side_effect=ValueError("bad")):
        with pytest.raises(SshError, match="connect failed"):
            _client().run("display version")


def test_check_reachable_true_and_false() -> None:
    with patch("backend.collect.ssh.ConnectHandler", return_value=_FakeConnection()):
        assert _client().check_reachable() is True
    with patch("backend.collect.ssh.ConnectHandler", side_effect=ValueError("bad")):
        assert _client().check_reachable() is False


def test_parse_display_version() -> None:
    parsed = parse_display_version(_fixture_text("display_version_s10500x.json"))
    # Official Comware 7 shape: "H3C Comware Software, Version 7.1.070,
    # Release 7596P10".
    assert parsed["version"] == "7.1.070"
    assert parsed["release"] == "7596P10"
    assert parsed["model"] == "S10508X"


def test_parse_display_version_platform_variant() -> None:
    # Older "Comware Platform Software, Software Version ..." wording.
    parsed = parse_display_version(
        "H3C Comware Platform Software, Software Version 7.1.070, Release 7510P21\n"
        "Copyright (c) 2004-2026 New H3C Technologies Co., Ltd.\n"
        "H3C S12516X-G uptime is 1 week, 2 days, 3 hours, 4 minutes"
    )
    assert parsed["version"] == "7.1.070"
    assert parsed["release"] == "7510P21"
    assert parsed["model"] == "S12516X-G"


def test_parse_display_version_unknown_stays_none() -> None:
    parsed = parse_display_version("unrecognizable output")
    assert parsed == {"version": None, "release": None, "model": None}


def test_parse_display_irf_members_and_roles() -> None:
    members = parse_display_irf(_fixture_text("display_irf_s10500x.json"))
    assert [(m.member_id, m.role) for m in members] == [(1, "Master"), (2, "Slave")]
    assert all(m.model is None and m.software_version is None for m in members)


def test_parse_display_irf_ignores_non_member_lines() -> None:
    members = parse_display_irf("garbage\n\nno members here")
    assert members == []


def test_parse_display_irf_configuration_member_ids() -> None:
    members = parse_display_irf_configuration(
        _fixture_text("display_irf_configuration_s10500x.json")
    )
    assert [m.member_id for m in members] == [1, 2]


class _StubSshClient:
    """H3CSshClient stub: command -> output text or SshError."""

    def __init__(self, outputs: dict[str, str | Exception]) -> None:
        self._outputs = outputs
        self.ran: list[str] = []

    def run(self, command: str) -> str:
        self.ran.append(command)
        result = self._outputs[command]
        if isinstance(result, Exception):
            raise result
        return result


def test_collect_software_info_from_display_version() -> None:
    from backend.collect.h3c.ssh_collectors import collect_software_info

    client = _StubSshClient({"display version": _fixture_text("display_version_s10500x.json")})
    software = collect_software_info(client)  # type: ignore[arg-type]
    assert software.version == "7.1.070"
    assert software.release == "7596P10"
    assert software.model == "S10508X"
    assert software.software_version == "7.1.070 Release 7596P10"


def test_collect_irf_members_merges_both_commands() -> None:
    from backend.collect.h3c.ssh_collectors import collect_irf_members

    client = _StubSshClient(
        {
            "display irf": _fixture_text("display_irf_s10500x.json"),
            "display irf configuration": _fixture_text(
                "display_irf_configuration_s10500x.json"
            ),
        }
    )
    members = collect_irf_members(client)  # type: ignore[arg-type]
    assert [(m.member_id, m.role) for m in members] == [(1, "Master"), (2, "Slave")]


def test_collect_irf_members_falls_back_to_configuration_only() -> None:
    from backend.collect.h3c.ssh_collectors import collect_irf_members

    client = _StubSshClient(
        {
            "display irf": SshError("ssh command failed: OSError"),
            "display irf configuration": _fixture_text(
                "display_irf_configuration_s10500x.json"
            ),
        }
    )
    members = collect_irf_members(client)  # type: ignore[arg-type]
    # Member ids survive; roles stay None (never guessed from configuration).
    assert [m.member_id for m in members] == [1, 2]
    assert all(m.role is None for m in members)


def test_collect_irf_members_no_rows_is_error() -> None:
    from backend.collect.h3c.ssh_collectors import collect_irf_members

    client = _StubSshClient(
        {
            "display irf": "garbage output",
            "display irf configuration": "garbage output",
        }
    )
    with pytest.raises(SshError, match="no IRF member rows"):
        collect_irf_members(client)  # type: ignore[arg-type]
