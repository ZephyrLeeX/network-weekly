"""Unit tests for devices.toml and secrets.env loading (W01-T001)."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.inventory import InventoryError, load_inventory
from backend.log import SecretRedactingFilter, clear_registered_secrets, redact_text
from backend.secrets import (
    SecretsError,
    SecretValues,
    load_secrets,
    lookup_profile_secret,
    profile_key,
)

TEN_DEVICE_TOML = """
[[device]]
name = "core-s10500x-01"
management_ip = "192.0.2.11"
model_family = "s10500x"

[[device]]
name = "core-s12500-irf"
management_ip = "192.0.2.20"
model_family = "s12500"
irf_member_count = 2
credential_profile = "core"
"""


@pytest.fixture(autouse=True)
def _no_registered_secrets() -> Iterator[None]:
    clear_registered_secrets()
    yield
    clear_registered_secrets()


class TestInventory:
    def test_loads_and_normalizes(self, tmp_path: Path) -> None:
        path = tmp_path / "devices.toml"
        path.write_text(TEN_DEVICE_TOML)
        entries = load_inventory(path)
        assert [e.name for e in entries] == ["core-s10500x-01", "core-s12500-irf"]
        standalone, irf = entries
        assert standalone.is_irf is False
        assert standalone.irf_member_count is None
        assert standalone.credential_profile == "default"
        assert irf.is_irf is True
        assert irf.irf_member_count == 2
        assert irf.credential_profile == "core"

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(InventoryError, match="not found"):
            load_inventory(tmp_path / "devices.toml")

    def test_invalid_toml(self, tmp_path: Path) -> None:
        path = tmp_path / "devices.toml"
        path.write_text("not [valid toml")
        with pytest.raises(InventoryError, match="TOML"):
            load_inventory(path)

    def test_unsupported_model_family(self, tmp_path: Path) -> None:
        path = tmp_path / "devices.toml"
        path.write_text(
            '[[device]]\nname="x"\nmanagement_ip="192.0.2.1"\nmodel_family="cisco"\n'
        )
        with pytest.raises(InventoryError, match="not supported"):
            load_inventory(path)

    def test_invalid_management_ip(self, tmp_path: Path) -> None:
        path = tmp_path / "devices.toml"
        path.write_text('[[device]]\nname="x"\nmanagement_ip="10.0.0"\nmodel_family="s10500x"\n')
        with pytest.raises(InventoryError, match="not a valid IP"):
            load_inventory(path)

    def test_duplicate_name_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "devices.toml"
        path.write_text(
            '[[device]]\nname="x"\nmanagement_ip="192.0.2.1"\nmodel_family="s10500x"\n'
            '[[device]]\nname="x"\nmanagement_ip="192.0.2.2"\nmodel_family="s10500x"\n'
        )
        with pytest.raises(InventoryError, match="duplicate device name"):
            load_inventory(path)

    def test_duplicate_management_ip_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "devices.toml"
        path.write_text(
            '[[device]]\nname="x"\nmanagement_ip="192.0.2.1"\nmodel_family="s10500x"\n'
            '[[device]]\nname="y"\nmanagement_ip="192.0.2.1"\nmodel_family="s10500x"\n'
        )
        with pytest.raises(InventoryError, match="management IP"):
            load_inventory(path)

    def test_irf_member_count_below_two_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "devices.toml"
        path.write_text(
            '[[device]]\nname="x"\nmanagement_ip="192.0.2.1"\nmodel_family="s10500x"'
            '\nirf_member_count=1\n'
        )
        with pytest.raises(InventoryError, match=">= 2"):
            load_inventory(path)


class TestSecrets:
    def _write(
        self,
        tmp_path: Path,
        mode: int,
        content: str = "A_SECRET=value1\nSSH_USERNAME_D=poe\n",
    ) -> Path:
        path = tmp_path / "secrets.env"
        path.write_text(content)
        path.chmod(mode)
        return path

    def test_loads_and_hides_repr(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, 0o600)
        secrets = load_secrets(path)
        assert secrets["A_SECRET"] == "value1"
        assert "value1" not in repr(secrets)
        assert "value1" not in str(secrets)
        assert isinstance(secrets, SecretValues)

    def test_rejects_wide_permissions(self, tmp_path: Path) -> None:
        for mode in (0o644, 0o664, 0o770, 0o640):
            path = self._write(tmp_path, mode)
            with pytest.raises(SecretsError, match="0600"):
                load_secrets(path)
            path.unlink()

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(SecretsError, match="not found"):
            load_secrets(tmp_path / "secrets.env")

    def test_malformed_line(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, 0o600, "NO_EQUALS_LINE\n")
        with pytest.raises(SecretsError, match="KEY=VALUE"):
            load_secrets(path)

    def test_quotes_comments_and_export(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            0o600,
            '# comment\nexport SNMP_COMMUNITY_DEFAULT="quoted value"\n'
            "SSH_PASSWORD_D='single'\n",
        )
        secrets = load_secrets(path)
        assert secrets["SNMP_COMMUNITY_DEFAULT"] == "quoted value"
        assert secrets["SSH_PASSWORD_D"] == "single"

    def test_values_are_registered_for_log_redaction(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, 0o600, "SNMP_COMMUNITY_DEFAULT=topsecret\n")
        load_secrets(path)
        assert redact_text("community=topsecret in logs") == "community=*** in logs"

    def test_logging_secret_value_is_masked(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, 0o600, "SSH_PASSWORD_D=hunter2\n")
        load_secrets(path)
        # The SecretRedactingFilter is what mutates records before any
        # handler/formatter sees them; a loaded secret value must vanish.
        record = logging.LogRecord(
            "secret-test", logging.INFO, __file__, 1, "connecting with hunter2 today", (), None
        )
        assert SecretRedactingFilter().filter(record) is True
        assert "hunter2" not in record.getMessage()
        assert "hunter2" not in record.msg

    def test_profile_key_and_lookup(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, 0o600, "SNMP_COMMUNITY_CORE=corecom\n")
        secrets = load_secrets(path)
        assert profile_key("SNMP_COMMUNITY", "core-x") == "SNMP_COMMUNITY_CORE_X"
        assert lookup_profile_secret(secrets, "SNMP_COMMUNITY", "CORE") == "corecom"
        with pytest.raises(SecretsError, match="SSH_USERNAME_CORE"):
            lookup_profile_secret(secrets, "SSH_USERNAME", "CORE")
