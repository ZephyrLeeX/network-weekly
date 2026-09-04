"""Per-device collection orchestration with section isolation (SYSTEM_SPEC.md §8).

Each section runs independently: one failing section records its failure
and never discards data the other sections already collected. The overall
status follows §8:

- SUCCESS: every attempted section succeeded.
- PARTIAL: at least one section failed but at least one produced valid data.
- FAILED: no section produced usable data.

Error strings stored in the report are secret-free: transport errors are
already normalized, and any unexpected exception is reduced to its class
name.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.collect.dto import (
    AggregationMapping,
    DeviceIdentity,
    EntityLoadSample,
    InterfaceSample,
    IrfMemberSample,
)
from backend.collect.h3c.collectors import (
    collect_aggregations,
    collect_cpu,
    collect_identity,
    collect_interfaces,
    collect_memory,
)
from backend.collect.snmp import SnmpClient, SnmpConfig, SnmpError
from backend.collect.ssh import H3CSshClient, SshConfig

logger = logging.getLogger(__name__)

SUCCESS = "SUCCESS"
PARTIAL = "PARTIAL"
FAILED = "FAILED"


@dataclass(frozen=True)
class SectionResult:
    """Outcome of one collection section (§8)."""

    name: str
    status: str  # SUCCESS or FAILED
    error: str | None = None


@dataclass
class DeviceCollectionOutcome:
    """Everything one device poll produced, plus per-section status."""

    device_name: str
    sections: list[SectionResult] = field(default_factory=list)
    identity: DeviceIdentity | None = None
    cpu: list[EntityLoadSample] | None = None
    memory: list[EntityLoadSample] | None = None
    interfaces: list[InterfaceSample] | None = None
    aggregations: list[AggregationMapping] | None = None
    irf_members: list[IrfMemberSample] | None = None
    # Reachability probe result after SNMP trouble (§9.1); None = not probed.
    ssh_reachable: bool | None = None

    @property
    def failed_sections(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.sections if s.status != SUCCESS)

    @property
    def overall_status(self) -> str:
        if not self.sections:
            return FAILED
        failed = self.failed_sections
        if not failed:
            return SUCCESS
        if len(failed) == len(self.sections):
            return FAILED
        return PARTIAL

    def has_valid_data(self) -> bool:
        """True when at least one section produced non-None data."""

        return any(
            data is not None
            for data in (self.identity, self.cpu, self.memory, self.interfaces, self.aggregations)
        )


def _run_section(
    outcome: DeviceCollectionOutcome, name: str, func: Callable[[], Any]
) -> Any:
    try:
        result = func()
    except SnmpError as exc:
        outcome.sections.append(SectionResult(name=name, status=FAILED, error=str(exc)))
        return None
    except Exception as exc:  # noqa: BLE001  (isolated; class name only — no secrets)
        outcome.sections.append(
            SectionResult(name=name, status=FAILED, error=type(exc).__name__)
        )
        return None
    outcome.sections.append(SectionResult(name=name, status=SUCCESS))
    return result


def run_collection(
    snmp: SnmpConfig, ssh: SshConfig | None, device_name: str
) -> DeviceCollectionOutcome:
    """Run all SNMP sections for one device; never raises (§7/§8).

    When any SNMP section failed and an SSH config is available, one
    lightweight SSH reachability probe runs (§9.1). The probe result is
    recorded on the outcome; Down/Recovery decisions are Wave 2 semantics.
    """

    outcome = DeviceCollectionOutcome(device_name=device_name)

    with SnmpClient(snmp) as client:
        outcome.identity = _run_section(outcome, "identity", lambda: collect_identity(client))
        outcome.cpu = _run_section(outcome, "cpu", lambda: collect_cpu(client))
        outcome.memory = _run_section(outcome, "memory", lambda: collect_memory(client))
        outcome.interfaces = _run_section(
            outcome, "interfaces", lambda: collect_interfaces(client)
        )
        outcome.aggregations = _run_section(
            outcome, "aggregations", lambda: collect_aggregations(client)
        )

    if outcome.failed_sections and ssh is not None:
        logger.info(
            "snmp sections failed for %s (%s); probing ssh reachability",
            device_name,
            ", ".join(outcome.failed_sections),
        )
        outcome.ssh_reachable = H3CSshClient(ssh).check_reachable()

    logger.info(
        "collection for %s: %s (failed sections: %s)",
        device_name,
        outcome.overall_status,
        ", ".join(outcome.failed_sections) or "none",
    )
    return outcome
