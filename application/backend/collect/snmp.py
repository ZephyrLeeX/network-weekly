"""Bounded SNMPv2c transport built on PySNMP 7.x.

All network access is bounded: per-request `timeout`/`retries` and a
wall-clock `walk_deadline_seconds` for every walk, so a silent device can
never stall collection (SYSTEM_SPEC.md §7.1). Errors are normalized to
:class:`SnmpError` — callers see one exception type with a short, secret-free
message (the community string is never included).

PySNMP 7.x exposes only the asyncio API, so this module drives an event loop
internally and offers a synchronous facade to the rest of the (sync) worker.

Overridable hooks `_run_get`/`_run_bulk` exist purely for tests: unit tests
inject canned responses/errors instead of opening UDP sockets.
"""

import asyncio
import logging
from dataclasses import dataclass
from time import monotonic
from typing import Any

from pysnmp.hlapi.v3arch import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    bulk_cmd,
    get_cmd,
)
from pysnmp.proto import rfc1902, rfc1905

logger = logging.getLogger(__name__)


class SnmpError(RuntimeError):
    """Normalized SNMP transport error (secret-free message)."""


@dataclass(frozen=True)
class SnmpVarbind:
    """One normalized (oid, value) pair; value is a plain Python scalar."""

    oid: str
    value: Any  # int | str | None | ...


@dataclass(frozen=True)
class SnmpConfig:
    host: str
    community: str
    port: int = 161
    timeout_seconds: float = 2.0
    retries: int = 1
    max_repetitions: int = 25
    # Wall-clock cap for a whole walk regardless of table size.
    walk_deadline_seconds: float = 60.0


_VALUE_TYPES = (rfc1902.Integer, rfc1902.Integer32, rfc1902.Gauge32, rfc1902.Counter32,
                rfc1902.Counter64, rfc1902.TimeTicks, rfc1902.Unsigned32)


def _to_python(value: Any) -> Any:
    """Convert a pysnmp value object to a plain Python scalar."""

    if isinstance(value, _VALUE_TYPES):
        return int(value)
    if isinstance(value, rfc1902.OctetString):
        return str(value)
    if isinstance(value, rfc1902.IpAddress):
        return str(value)
    if isinstance(value, (rfc1905.NoSuchObject, rfc1905.NoSuchInstance, rfc1905.EndOfMibView)):
        return None
    if isinstance(value, rfc1905.Null):
        return None
    return value.prettyPrint()


def _is_missing(value: Any) -> bool:
    return isinstance(
        value, (rfc1905.NoSuchObject, rfc1905.NoSuchInstance, rfc1905.EndOfMibView, rfc1905.Null)
    )


class SnmpClient:
    """Synchronous facade over the PySNMP asyncio API for one device."""

    def __init__(self, config: SnmpConfig) -> None:
        self._config = config

    # -- hooks overridable in tests -------------------------------------------------

    async def _run_get(self, oids: list[str]) -> tuple[Any, int, list[SnmpVarbind]]:
        engine = SnmpEngine()
        try:
            target = await UdpTransportTarget.create(
                (self._config.host, self._config.port),
                timeout=self._config.timeout_seconds,
                retries=self._config.retries,
            )
            varbinds = [ObjectType(ObjectIdentity(oid)) for oid in oids]
            error_indication, error_status, error_index, binds = await get_cmd(
                engine,
                CommunityData(self._config.community, mpModel=1),
                target,
                ContextData(),
                *varbinds,
            )
            normalized = [
                SnmpVarbind(oid=str(vb[0]), value=None if _is_missing(vb[1]) else _to_python(vb[1]))
                for vb in binds
            ]
            return error_indication, error_status, normalized
        finally:
            engine.close_dispatcher()

    async def _run_bulk(
        self, start_oid: str, *, lexicographic_mode: bool
    ) -> tuple[Any, int, list[SnmpVarbind]]:
        engine = SnmpEngine()
        try:
            target = await UdpTransportTarget.create(
                (self._config.host, self._config.port),
                timeout=self._config.timeout_seconds,
                retries=self._config.retries,
            )
            error_indication, error_status, error_index, binds = await bulk_cmd(
                engine,
                CommunityData(self._config.community, mpModel=1),
                target,
                ContextData(),
                0,
                self._config.max_repetitions,
                ObjectType(ObjectIdentity(start_oid)),
                lexicographicMode=lexicographic_mode,
            )
            normalized = [
                SnmpVarbind(oid=str(vb[0]), value=None if _is_missing(vb[1]) else _to_python(vb[1]))
                for vb in binds
            ]
            return error_indication, error_status, normalized
        finally:
            engine.close_dispatcher()

    # -- public synchronous facade --------------------------------------------------

    def get(self, oids: list[str]) -> list[SnmpVarbind]:
        """GET a fixed list of OIDs; missing instances come back as value None."""

        if not oids:
            return []
        try:
            error_indication, error_status, varbinds = asyncio.run(
                self._run_get(oids)
            )
        except (TimeoutError, OSError) as exc:
            raise SnmpError(f"snmp transport failure: {type(exc).__name__}") from exc
        if error_indication is not None:
            raise SnmpError(f"snmp get failed: {error_indication}")
        if error_status != 0:
            raise SnmpError(f"snmp get failed: agent error-status {error_status}")
        return varbinds

    def bulk_walk(self, oid: str) -> list[SnmpVarbind]:
        """Bounded GETBULK walk of one subtree.

        Stops when the subtree ends, on any transport/agent error, or when
        the walk deadline expires. Never walks unboundedly.
        """

        results: list[SnmpVarbind] = []
        lever = oid
        deadline = monotonic() + self._config.walk_deadline_seconds
        prefix = f"{oid}."
        try:
            while True:
                if monotonic() > deadline:
                    raise SnmpError(
                        f"snmp walk of {oid} exceeded "
                        f"{self._config.walk_deadline_seconds}s deadline"
                    )
                error_indication, error_status, varbinds = asyncio.run(
                    self._run_bulk(lever, lexicographic_mode=False)
                )
                if error_indication is not None:
                    raise SnmpError(f"snmp walk failed: {error_indication}")
                if error_status != 0:
                    raise SnmpError(f"snmp walk failed: agent error-status {error_status}")
                if not varbinds:
                    break
                advanced = False
                for vb in varbinds:
                    if vb.value is None or (
                        isinstance(vb.oid, str) and not (vb.oid == oid or vb.oid.startswith(prefix))
                    ):
                        # EndOfMibView / walked past the subtree: subtree finished.
                        return results
                    results.append(vb)
                    lever = vb.oid
                    advanced = True
                if not advanced:
                    break
        except SnmpError:
            raise
        except (TimeoutError, OSError) as exc:
            raise SnmpError(f"snmp transport failure: {type(exc).__name__}") from exc
        return results

    def close(self) -> None:  # noqa: B027  (hook for symmetry/context-manager use)
        """No persistent resources: each call builds and closes its own engine."""

    def __enter__(self) -> SnmpClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
