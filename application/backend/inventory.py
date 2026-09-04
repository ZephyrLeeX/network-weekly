"""Device inventory loading from /etc/network-report/devices.toml (SYSTEM_SPEC.md §22.1).

The inventory contains non-sensitive device metadata only: logical name,
management IP, model family, expected IRF member count and a *reference* to
a credential profile in secrets.env. Secrets never live here.

Format:

    [[device]]
    name = "core-s10500x-01"
    management_ip = "192.0.2.11"
    model_family = "s10500x"          # supported: "s10500x", "s12500"
    credential_profile = "default"    # key prefix in secrets.env
    # irf_member_count = 2            # omit for standalone devices

`python -m backend.inventory_sync` performs the explicit inventory sync into
PostgreSQL (see inventory_sync.py).
"""

import ipaddress
import tomllib
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_MODEL_FAMILIES = ("s10500x", "s12500")
DEFAULT_CREDENTIAL_PROFILE = "default"


class InventoryError(ValueError):
    """Raised when devices.toml is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class DeviceEntry:
    """One logical device from devices.toml (SYSTEM_SPEC.md §22.1)."""

    name: str
    management_ip: str
    model_family: str
    credential_profile: str
    # None (or 1) means a standalone device; >= 2 an IRF fabric (§2.2/§17).
    irf_member_count: int | None = None

    @property
    def is_irf(self) -> bool:
        return self.irf_member_count is not None and self.irf_member_count > 1


def _parse_device(raw: object, index: int) -> DeviceEntry:
    if not isinstance(raw, dict):
        raise InventoryError(f"device #{index}: entry must be a table")

    def _text(key: str) -> str:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise InventoryError(f"device #{index}: {key!r} must be a non-empty string")
        return value.strip()

    name = _text("name")
    management_ip = _text("management_ip")
    try:
        ipaddress.ip_address(management_ip)
    except ValueError as exc:
        raise InventoryError(
            f"device {name!r}: management_ip {management_ip!r} is not a valid IP address"
        ) from exc

    model_family = _text("model_family").lower()
    if model_family not in SUPPORTED_MODEL_FAMILIES:
        raise InventoryError(
            f"device {name!r}: model_family {model_family!r} is not supported "
            f"(only {', '.join(SUPPORTED_MODEL_FAMILIES)})"
        )

    profile = raw.get("credential_profile", DEFAULT_CREDENTIAL_PROFILE)
    if not isinstance(profile, str) or not profile.strip():
        raise InventoryError(
            f"device {name!r}: credential_profile must be a non-empty string"
        )
    profile = profile.strip()

    irf_member_count: int | None = None
    raw_count = raw.get("irf_member_count")
    if raw_count is not None:
        if not isinstance(raw_count, int) or isinstance(raw_count, bool):
            raise InventoryError(f"device {name!r}: irf_member_count must be an integer")
        if raw_count < 2:
            raise InventoryError(
                f"device {name!r}: irf_member_count must be >= 2 for an IRF fabric; "
                "omit it for standalone devices"
            )
        irf_member_count = raw_count

    return DeviceEntry(
        name=name,
        management_ip=management_ip,
        model_family=model_family,
        credential_profile=profile,
        irf_member_count=irf_member_count,
    )


def load_inventory(path: Path | str) -> list[DeviceEntry]:
    """Load and validate devices.toml; every error is explicit and named."""

    inventory_path = Path(path)
    try:
        with inventory_path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise InventoryError(f"inventory file not found: {inventory_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise InventoryError(f"inventory file is not valid TOML: {exc}") from exc

    raw_devices = data.get("device")
    if not isinstance(raw_devices, list) or not raw_devices:
        raise InventoryError(
            f"inventory file {inventory_path} must contain at least one [[device]] entry"
        )

    entries = [_parse_device(raw, index) for index, raw in enumerate(raw_devices, start=1)]

    seen: set[str] = set()
    for entry in entries:
        if entry.name in seen:
            raise InventoryError(f"duplicate device name in inventory: {entry.name!r}")
        seen.add(entry.name)

    ips: dict[str, str] = {}
    for entry in entries:
        if entry.management_ip in ips:
            raise InventoryError(
                f"management IP {entry.management_ip} used by both {ips[entry.management_ip]!r} "
                f"and {entry.name!r}"
            )
        ips[entry.management_ip] = entry.name

    return entries
