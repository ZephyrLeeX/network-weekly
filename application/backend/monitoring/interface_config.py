"""Priority (monitored) interface configuration service (W02-T005, §12).

The `monitored` flag lives on the `interfaces` row since the Wave 1 schema;
this service is the single write path for it and the read model the Web
configuration page (Wave 4) will consume.

Semantics enforced here (§11/§12):

- Newly discovered interfaces are `monitored=false` by default (discovery
  never writes the flag — W01-T004).
- Selecting an aggregation interface monitors ONLY the aggregation logical
  interface; member ports are never auto-selected.
- Member ports remain individually selectable.
- The flag is keyed by the stable business identity
  `(device_id, normalized_name)`, so rediscovery or an ifIndex change can
  never lose the administrator's selection.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import AggregationMember, Interface

logger = logging.getLogger(__name__)


class InterfaceNotFoundError(ValueError):
    """Raised when an interface id does not exist (page-level 404 input)."""


def set_monitored(session: Session, interface_id: int, monitored: bool) -> Interface:
    """Set one interface's monitored flag; nothing else is ever touched.

    Deliberately NO cascade: aggregation membership is display information
    here, not selection propagation (§11).
    """

    interface = session.get(Interface, interface_id)
    if interface is None:
        raise InterfaceNotFoundError(f"interface id {interface_id} does not exist")
    if interface.monitored != monitored:
        interface.monitored = monitored
        logger.info(
            "interface %s (device %d) monitored=%s",
            interface.display_name,
            interface.device_id,
            monitored,
        )
    return interface


@dataclass(frozen=True)
class InterfaceOverviewRow:
    """One interface row for the priority-interface configuration page (§12)."""

    interface_id: int
    display_name: str
    normalized_name: str
    description: str | None
    admin_state: str | None
    oper_state: str | None
    is_aggregation: bool
    monitored: bool
    # Member interface display names when this row is an aggregation.
    aggregation_members: tuple[str, ...]
    # Aggregation display names this interface belongs to (member rows).
    member_of: tuple[str, ...]


def interface_overview(session: Session, device_id: int) -> list[InterfaceOverviewRow]:
    """All discovered interfaces of one device with aggregation relationships."""

    interfaces = (
        session.execute(
            select(Interface)
            .where(Interface.device_id == device_id)
            .order_by(Interface.normalized_name)
        )
        .scalars()
        .all()
    )
    by_id = {interface.id: interface for interface in interfaces}

    members_by_aggregate: dict[int, list[str]] = {}
    aggregates_of_member: dict[int, list[str]] = {}
    memberships = (
        session.execute(
            select(AggregationMember).where(
                AggregationMember.aggregation_interface_id.in_(by_id.keys() or {0})
            )
        )
        .scalars()
        .all()
    )
    for membership in memberships:
        aggregate = by_id.get(membership.aggregation_interface_id)
        member = by_id.get(membership.member_interface_id)
        if aggregate is None or member is None:
            continue  # cross-device rows cannot exist; defensive only
        members_by_aggregate.setdefault(aggregate.id, []).append(member.display_name)
        aggregates_of_member.setdefault(member.id, []).append(aggregate.display_name)

    return [
        InterfaceOverviewRow(
            interface_id=interface.id,
            display_name=interface.display_name,
            normalized_name=interface.normalized_name,
            description=interface.description,
            admin_state=interface.admin_state,
            oper_state=interface.oper_state,
            is_aggregation=interface.is_aggregation,
            monitored=interface.monitored,
            aggregation_members=tuple(
                sorted(members_by_aggregate.get(interface.id, []))
            ),
            member_of=tuple(sorted(aggregates_of_member.get(interface.id, []))),
        )
        for interface in interfaces
    ]
