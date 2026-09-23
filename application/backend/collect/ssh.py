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
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from netmiko import ConnectHandler

from backend.collect.snmp import PollDeadlineExceeded

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

    def __init__(
        self,
        config: SshConfig,
        *,
        absolute_deadline: float | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self._config = config
        self._absolute_deadline = absolute_deadline
        self._monotonic = monotonic_clock

    def _remaining_budget(self) -> float | None:
        if self._absolute_deadline is None:
            return None
        remaining = self._absolute_deadline - self._monotonic()
        if remaining <= 0:
            raise PollDeadlineExceeded("device poll deadline exceeded")
        return remaining

    def _deadline_expired(self) -> bool:
        return (
            self._absolute_deadline is not None
            and self._monotonic() >= self._absolute_deadline
        )

    def _connect(self) -> Any:
        remaining = self._remaining_budget()
        connect_timeout = self._config.connect_timeout_seconds
        if remaining is not None:
            connect_timeout = min(connect_timeout, remaining)
        try:
            connection = ConnectHandler(
                device_type=self._config.device_type,
                host=self._config.host,
                port=self._config.port,
                username=self._config.username,
                password=self._config.password,
                conn_timeout=connect_timeout,
                auth_timeout=connect_timeout,
                banner_timeout=connect_timeout,
                blocking_timeout=connect_timeout,
                timeout=connect_timeout,
                # Never enable session_log: it would capture the password.
                session_log=None,
            )
        except Exception as exc:  # noqa: BLE001  (normalized, class name only)
            if self._deadline_expired():
                raise PollDeadlineExceeded("device poll deadline exceeded") from exc
            raise SshError(f"ssh connect failed: {type(exc).__name__}") from exc
        # Connecting consumes part of the whole-poll budget. Recheck before
        # returning so run() never starts a command after the deadline.
        try:
            self._remaining_budget()
        except PollDeadlineExceeded:
            try:
                connection.disconnect()
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask deadline
                logger.warning("ssh disconnect after deadline failed: %s", type(exc).__name__)
            raise
        return connection

    def run(self, command: str) -> str:
        """Run one allowlisted read-only command; bounded by read timeout."""

        if command not in ALLOWED_COMMANDS:
            raise SshError(f"command not in the read-only allowlist: {command!r}")
        connection = self._connect()
        try:
            remaining = self._remaining_budget()
            read_timeout = self._config.read_timeout_seconds
            if remaining is not None:
                read_timeout = min(read_timeout, remaining)
            output = connection.send_command(
                command,
                read_timeout=read_timeout,
            )
            # A transport may return at the timeout boundary. Do not turn a
            # command that completed outside the shared budget into evidence.
            self._remaining_budget()
        except PollDeadlineExceeded:
            raise
        except Exception as exc:  # noqa: BLE001  (normalized, class name only)
            if self._deadline_expired():
                raise PollDeadlineExceeded("device poll deadline exceeded") from exc
            raise SshError(f"ssh command failed: {type(exc).__name__}") from exc
        finally:
            try:
                connection.disconnect()
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup, class name only
                logger.warning("ssh disconnect failed: %s", type(exc).__name__)
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
        except PollDeadlineExceeded:
            raise
        except SshError:
            return False
        return True
