"""Device secret loading from /etc/network-report/secrets.env (SYSTEM_SPEC.md §22.2).

Rules enforced here:

- The file must have permission mode `0600`; anything wider is rejected
  before a single byte is parsed.
- Parsed values are registered with `backend.log.register_secret` the moment
  they are loaded, so they can never appear in redacted log output.
- Values are wrapped in a mapping whose repr/str never expose content, so
  accidental `print`/logging of the container is safe too.

Format (KEY=VALUE, one per line; `#` comments; optional quotes; optional
`export ` prefix). Keys are per credential profile, upper-cased with `-`
mapped to `_`:

    SNMP_COMMUNITY_DEFAULT=...
    SSH_USERNAME_DEFAULT=...
    SSH_PASSWORD_DEFAULT=...
"""

import os
import stat
from collections.abc import Iterator, Mapping
from pathlib import Path

from backend.log import register_secret

REQUIRED_PERMISSION = 0o600


class SecretsError(ValueError):
    """Raised when secrets.env is missing, too permissive or malformed."""


def _validate_permissions(path: Path) -> None:
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError as exc:
        raise SecretsError(f"secrets file not found: {path}") from exc
    if mode != REQUIRED_PERMISSION:
        raise SecretsError(
            f"secrets file {path} must have permission 0600 (got {mode:04o}); "
            "refusing to load secrets from a too-permissive file"
        )


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_env_file(text: str) -> Iterator[tuple[str, str]]:
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            raise SecretsError(f"secrets line {line_number}: expected KEY=VALUE")
        key = key.strip()
        value = _unquote(value.strip())
        if not key or not value:
            raise SecretsError(f"secrets line {line_number}: KEY and VALUE must be non-empty")
        yield key, value


class SecretValues(Mapping[str, str]):
    """Read-only secret mapping whose repr/str never reveals any value."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = dict(values)

    def __getitem__(self, key: str) -> str:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"SecretValues(<{len(self._values)} entries hidden>)"

    __str__ = __repr__


def load_secrets(path: Path | str) -> SecretValues:
    """Load secrets.env after enforcing 0600; register every value for log redaction."""

    secrets_path = Path(path)
    _validate_permissions(secrets_path)
    try:
        text = secrets_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SecretsError(f"cannot read secrets file {path}: {type(exc).__name__}") from exc

    values = dict(_parse_env_file(text))
    for value in values.values():
        register_secret(value)
    return SecretValues(values)


def profile_key(kind: str, profile: str) -> str:
    """Secrets.env key for one credential-profile field (SYSTEM_SPEC.md §22.1/§22.2)."""

    return f"{kind}_{profile.upper().replace('-', '_')}"


def lookup_profile_secret(secrets: Mapping[str, str], kind: str, profile: str) -> str:
    """Fetch one profile secret; errors name the missing key, never the value."""

    key = profile_key(kind, profile)
    try:
        return secrets[key]
    except KeyError as exc:
        raise SecretsError(
            f"secrets file is missing required key {key} for credential profile {profile!r}"
        ) from exc
