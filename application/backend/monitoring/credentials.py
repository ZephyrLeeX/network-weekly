"""Device poll contexts from the devices table + secrets.env (W02-T002).

The worker reloads enabled devices every cycle, so inventory syncs apply
without a restart. Secrets come from the 0600 `secrets.env`
(SYSTEM_SPEC.md §22.2); a device whose SNMP community is missing cannot be
polled and is reported, never guessed around. SSH is supplementary (§7.2):
a device without SSH credentials still polls over SNMP but has no §9.1
reachability confirmation available.

IRF observation (W02-T008) needs SSH and only applies to devices *configured*
as IRF fabrics (`expected_irf_member_count > 1`) — standalone devices are
never SSH-probed for IRF (§17).
"""

import logging
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.collect.snmp import SnmpConfig
from backend.collect.ssh import SshConfig
from backend.db.models import Device
from backend.monitoring.irf import IrfDeviceContext
from backend.monitoring.poll import DevicePollContext
from backend.secrets import SecretsError, lookup_profile_secret

logger = logging.getLogger(__name__)

SNMP_KIND = "SNMP_COMMUNITY"
SSH_USERNAME_KIND = "SSH_USERNAME"
SSH_PASSWORD_KIND = "SSH_PASSWORD"


def build_contexts(session: Session, secrets: Mapping[str, str]) -> list[DevicePollContext]:
    """Build poll contexts for every enabled device; skips misconfigured ones."""

    contexts: list[DevicePollContext] = []
    for device in session.execute(
        select(Device).where(Device.enabled.is_(True)).order_by(Device.id)
    ).scalars():
        context = _build_context(device, secrets)
        if context is not None:
            contexts.append(context)
    return contexts


def build_irf_contexts(
    session: Session, secrets: Mapping[str, str]
) -> list[IrfDeviceContext]:
    """Observation contexts for enabled IRF fabrics with SSH credentials.

    A device without SSH credentials is reported and skipped: IRF observation
    is an SSH flow (§7.3) and guessing is not an option.
    """

    contexts: list[IrfDeviceContext] = []
    for device in session.execute(
        select(Device)
        .where(Device.enabled.is_(True), Device.expected_irf_member_count > 1)
        .order_by(Device.id)
    ).scalars():
        try:
            ssh = SshConfig(
                host=device.management_ip,
                username=lookup_profile_secret(
                    secrets, SSH_USERNAME_KIND, device.credential_profile
                ),
                password=lookup_profile_secret(
                    secrets, SSH_PASSWORD_KIND, device.credential_profile
                ),
            )
        except SecretsError as exc:
            logger.warning("irf observation unavailable for %s: %s", device.name, exc)
            continue
        contexts.append(
            IrfDeviceContext(device_id=device.id, device_name=device.name, ssh=ssh)
        )
    return contexts


def _build_context(
    device: Device, secrets: Mapping[str, str]
) -> DevicePollContext | None:
    try:
        community = lookup_profile_secret(secrets, SNMP_KIND, device.credential_profile)
    except SecretsError as exc:
        logger.error(
            "device %s cannot be polled: %s", device.name, exc
        )
        return None

    ssh: SshConfig | None = None
    try:
        ssh = SshConfig(
            host=device.management_ip,
            username=lookup_profile_secret(secrets, SSH_USERNAME_KIND, device.credential_profile),
            password=lookup_profile_secret(secrets, SSH_PASSWORD_KIND, device.credential_profile),
        )
    except SecretsError as exc:
        # SSH stays optional: SNMP-only device, no §9.1 confirmation available.
        logger.warning("device %s has no SSH credentials (%s)", device.name, exc)

    return DevicePollContext(
        device_id=device.id,
        device_name=device.name,
        snmp=SnmpConfig(host=device.management_ip, community=community),
        ssh=ssh,
    )
