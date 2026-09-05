"""Per-device collection orchestration with section isolation (SYSTEM_SPEC.md §8).

Each section runs independently: one failing section records its failure
and never discards data the other sections already collected. The overall
status follows §8:

- SUCCESS: every attempted section fully succeeded.
- PARTIAL: at least one section failed or delivered only degraded data,
  but at least one produced valid data.
- FAILED: no section produced usable data.

A section can also be DEGRADED: it returned data, but a key part of the
section (e.g. the interface state/speed/counter columns) could not be
collected. Degraded data is kept — a partial column failure never discards
the samples other columns delivered — but it counts as a non-success
section, so the cycle lands on PARTIAL instead of faking SUCCESS.

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
    DeviceSoftwareInfo,
    EntityLoadSample,
    InterfaceSample,
    IrfMemberSample,
)
from backend.collect.h3c.collectors import (
    CollectionSectionError,
    InterfaceCollection,
    collect_aggregations,
    collect_cpu,
    collect_identity,
    collect_interfaces,
    collect_memory,
)
from backend.collect.h3c.ssh_collectors import (
    collect_irf_members as collect_irf_members_ssh,
)
from backend.collect.h3c.ssh_collectors import (
    collect_software_info as collect_software_info_ssh,
)
from backend.collect.snmp import SnmpClient, SnmpConfig, SnmpError
from backend.collect.ssh import H3CSshClient, SshConfig

logger = logging.getLogger(__name__)

SUCCESS = "SUCCESS"
PARTIAL = "PARTIAL"
FAILED = "FAILED"
# Data returned, but a key part of the section is missing (§8 degraded).
DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class SectionResult:
    """Outcome of one collection section (§8)."""

    name: str
    # SUCCESS, DEGRADED (data kept, cycle is at best PARTIAL) or FAILED.
    status: str
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
    software: DeviceSoftwareInfo | None = None
    # Reachability probe result after SNMP trouble (§9.1); None = not probed.
    ssh_reachable: bool | None = None

    @property
    def failed_sections(self) -> tuple[str, ...]:
        """Sections that did not fully succeed (FAILED or DEGRADED)."""
        return tuple(s.name for s in self.sections if s.status != SUCCESS)

    @property
    def overall_status(self) -> str:
        if not self.sections:
            return FAILED
        failed = self.failed_sections
        if not failed:
            return SUCCESS
        if len(failed) == len(self.sections):
            # Only when NO section produced usable data can the cycle be
            # FAILED; a degraded section still delivered samples (§8).
            if all(s.status == FAILED for s in self.sections):
                return FAILED
        return PARTIAL

    def has_valid_data(self) -> bool:
        """True when at least one section produced non-None data."""

        return any(
            data is not None
            for data in (
                self.identity,
                self.cpu,
                self.memory,
                self.interfaces,
                self.aggregations,
                self.irf_members,
                self.software,
            )
        )


def _run_section(
    outcome: DeviceCollectionOutcome, name: str, func: Callable[[], Any]
) -> Any:
    try:
        result = func()
    except CollectionSectionError as exc:
        # Our own section-failure signal; message is secret-free by construction.
        outcome.sections.append(SectionResult(name=name, status=FAILED, error=str(exc)))
        return None
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


def _snmp_channel_failed(outcome: DeviceCollectionOutcome) -> bool:
    """True when the SNMP management channel itself yielded nothing.

    Only then does the §9.1 SSH reachability probe run. A single failing
    CPU/memory/LAG section with valid data from other sections proves the
    SNMP channel works and must NOT trigger SSH.
    """

    return not outcome.has_valid_data()


def _run_interfaces_section(outcome: DeviceCollectionOutcome, client: SnmpClient) -> None:
    """Run the interfaces section with its degraded-data path (§8).

    FAILED (no usable rows) drops nothing that exists — there are no rows.
    DEGRADED keeps every assembled sample and records which key fields were
    missing (each judged on its own columns: a field no column delivered),
    so the poll run lands on PARTIAL instead of a fake SUCCESS while the
    collected interface data still persists.
    """

    try:
        result = collect_interfaces(client)
    except CollectionSectionError as exc:
        # Our own section-failure signal; message is secret-free by construction.
        outcome.sections.append(SectionResult(name="interfaces", status=FAILED, error=str(exc)))
        return
    except SnmpError as exc:
        outcome.sections.append(SectionResult(name="interfaces", status=FAILED, error=str(exc)))
        return
    except Exception as exc:  # noqa: BLE001  (isolated; class name only — no secrets)
        outcome.sections.append(
            SectionResult(name="interfaces", status=FAILED, error=type(exc).__name__)
        )
        return

    if isinstance(result, InterfaceCollection):
        outcome.interfaces = result.samples
        missing_fields = result.missing_fields
    else:
        outcome.interfaces = result
        missing_fields = ()
    if missing_fields:
        outcome.sections.append(
            SectionResult(
                name="interfaces",
                status=DEGRADED,
                error="missing key fields: " + ", ".join(missing_fields),
            )
        )
        logger.warning(
            "interfaces section degraded for %s (missing: %s)",
            outcome.device_name,
            ", ".join(missing_fields),
        )
        return
    outcome.sections.append(SectionResult(name="interfaces", status=SUCCESS))


def run_collection(
    snmp: SnmpConfig, ssh: SshConfig | None, device_name: str
) -> DeviceCollectionOutcome:
    """Run all SNMP sections for one device; never raises (§7/§8).

    The lightweight SSH reachability probe (§9.1) runs only when the SNMP
    management channel truly failed — no section produced any valid data.
    The probe result is recorded on the outcome; Down/Recovery decisions
    are Wave 2 semantics. SSH static collection (version/IRF) is a
    separate, caller-scheduled flow (:func:`run_static_ssh_collection` /
    :func:`run_irf_observation`) and is never forced into this 5-minute
    poll.
    """

    outcome = DeviceCollectionOutcome(device_name=device_name)

    with SnmpClient(snmp) as client:
        outcome.identity = _run_section(outcome, "identity", lambda: collect_identity(client))
        outcome.cpu = _run_section(outcome, "cpu", lambda: collect_cpu(client))
        outcome.memory = _run_section(outcome, "memory", lambda: collect_memory(client))
        _run_interfaces_section(outcome, client)
        outcome.aggregations = _run_section(
            outcome, "aggregations", lambda: collect_aggregations(client)
        )

    if _snmp_channel_failed(outcome) and ssh is not None:
        logger.info(
            "snmp management channel failed for %s (%s); probing ssh reachability",
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


def run_static_ssh_collection(
    ssh: SshConfig, device_name: str
) -> DeviceCollectionOutcome:
    """Collect SSH static data: software version + IRF members (§7.2).

    A standalone, caller-scheduled flow (not part of the 5-minute
    DEVICE_POLL): sections run with the same isolation rules, and the
    returned outcome persists through :func:`persist_collection` — device
    software_version and IRF member id/role (§17).
    """

    outcome = DeviceCollectionOutcome(device_name=device_name)
    client = H3CSshClient(ssh)
    outcome.software = _run_section(
        outcome, "software", lambda: collect_software_info_ssh(client)
    )
    outcome.irf_members = _run_section(
        outcome, "irf", lambda: collect_irf_members_ssh(client)
    )
    logger.info(
        "static ssh collection for %s: %s (failed sections: %s)",
        device_name,
        outcome.overall_status,
        ", ".join(outcome.failed_sections) or "none",
    )
    return outcome


def run_irf_observation(ssh: SshConfig, device_name: str) -> list[IrfMemberSample] | None:
    """One IRF member observation (§7.3), sized for the ~15-minute cadence.

    Standalone entry point for the Wave 2 IRF scheduler: returns the
    observed members, or None when the observation failed. Never raises.
    """

    try:
        members = collect_irf_members_ssh(H3CSshClient(ssh))
    except Exception as exc:  # noqa: BLE001  (normalized upstream; class name only)
        logger.warning("irf observation failed for %s: %s", device_name, type(exc).__name__)
        return None
    return members
