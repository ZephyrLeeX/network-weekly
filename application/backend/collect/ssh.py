"""Limited read-only SSH transport for H3C Comware (SYSTEM_SPEC.md §7.2).

SSH is a *supplementary* channel only: device identity/version, IRF member
and role information, and the one lightweight reachability confirmation
after an SNMP failure (§9.1). It is never the metric collection path.

Constraints enforced here:

- The command allowlist is a module constant; `run()` refuses anything not
  in it. Every allowed command is read-only `display ...`.
- Connect and read timeouts are bounded; a silent device cannot stall the
  worker.
- Errors normalize to :class:`SshError` with a short, secret-free message.
  Netmiko exceptions can embed credentials, so only the exception class
  name is ever included.
- Session logging stays disabled (a session log would capture the auth
  exchange).
"""

import logging
from dataclasses import dataclass
from typing import Any

from netmiko import ConnectHandler

logger = logging.getLogger(__name__)

# Read-only allowlist (SYSTEM_SPEC.md §7.2). Extend only with verified
# read-only commands after real-device validation.
ALLOWED_COMMANDS = (
    "display version",
    "display irf",
    "display irf configuration",
)

REACHABILITY_COMMAND = "display version"


class SshError(RuntimeError):
    """Normalized SSH transport error (secret-free message)."""


@dataclass(frozen=True)
class SshConfig:
    host: str
    username: str
    password: str
    port: int = 22
    # Netmiko platform for H3C Comware (verified present in netmiko 4.x).
    device_type: str = "h3c_comware"
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 15.0


class H3CSshClient:
    """One bounded, allowlisted read-only SSH session per call."""

    def __init__(self, config: SshConfig) -> None:
        self._config = config

    def _connect(self) -> Any:
        try:
            return ConnectHandler(
                device_type=self._config.device_type,
                host=self._config.host,
                port=self._config.port,
                username=self._config.username,
                password=self._config.password,
                timeout=self._config.connect_timeout_seconds,
                # Never enable session_log: it would capture the password.
                session_log=None,
            )
        except Exception as exc:  # noqa: BLE001  (normalized, class name only)
            raise SshError(f"ssh connect failed: {type(exc).__name__}") from exc

    def run(self, command: str) -> str:
        """Run one allowlisted read-only command; bounded by read timeout."""

        if command not in ALLOWED_COMMANDS:
            raise SshError(f"command not in the read-only allowlist: {command!r}")
        connection = self._connect()
        try:
            output = connection.send_command(
                command,
                read_timeout=self._config.read_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001  (normalized, class name only)
            raise SshError(f"ssh command failed: {type(exc).__name__}") from exc
        finally:
            connection.disconnect()
        if not isinstance(output, str):
            raise SshError("ssh command returned no text output")
        return output

    def check_reachable(self) -> bool:
        """Lightweight management-plane confirmation (SYSTEM_SPEC.md §9.1).

        True only when an authenticated SSH session runs one allowlisted
        read-only command successfully. Never raises.
        """

        try:
            self.run(REACHABILITY_COMMAND)
        except SshError:
            return False
        return True
