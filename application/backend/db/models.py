"""Wave 0/1 ORM models.

Wave 0: the foundation-required `worker_heartbeat` table (SYSTEM_SPEC.md §23).
Wave 1: `devices`, `device_members`, `interfaces` and `aggregation_members`
(SYSTEM_SPEC.md §2.2/§10/§11/§23). Metric/poll-run tables arrive with the
Wave 2 migrations. All business timestamps use TIMESTAMPTZ (§3/§23).
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class WorkerHeartbeat(Base):
    """Liveness record for one worker identity, upserted on every heartbeat.

    `last_heartbeat` proves the worker loop is running; `started_at` lets
    restarts be distinguished from a continuously running worker. Web health
    and worker heartbeat are deliberately independent checks.
    """

    __tablename__ = "worker_heartbeat"

    worker_id: Mapped[str] = mapped_column(Text, primary_key=True)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[str | None] = mapped_column(Text, nullable=True)
    hostname: Mapped[str | None] = mapped_column(Text, nullable=True)


class Device(Base):
    """One logical device, identified by its management IP (SYSTEM_SPEC.md §2.2).

    An IRF fabric is a single `Device` row (one management IP); its chassis
    are `DeviceMember` rows. Standalone devices have no member rows — the
    model does not require member records just for form's sake (§2.2).
    Inventory sync (W01-T001) owns name/model/expected member count; the
    collectors own the observed identity/version fields.
    """

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Stable business key from /etc/network-report/devices.toml.
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    management_ip: Mapped[str] = mapped_column(Text, nullable=False)
    # Supported families only: "s10500x" or "s12500" (SYSTEM_SPEC.md §2.2).
    model_family: Mapped[str] = mapped_column(Text, nullable=False)
    # Expected IRF member count; NULL/1 means a standalone device (§17).
    expected_irf_member_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Reference into /etc/network-report/secrets.env; the secret itself never
    # enters the database (SYSTEM_SPEC.md §22.2).
    credential_profile: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Observed identity, filled by the collectors (W01-T006 persistence).
    sys_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    sys_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sys_object_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    software_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    members: Mapped[list[DeviceMember]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    interfaces: Mapped[list[Interface]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )


class DeviceMember(Base):
    """One physical IRF member (chassis) of a logical device (SYSTEM_SPEC.md §2.2/§17).

    `member_id` is the IRF member number reported by the device; `role` is the
    observed IRF role (e.g. "Master"/"Slave" as reported) and may be NULL when
    the source data does not identify roles reliably.
    """

    __tablename__ = "device_members"
    __table_args__ = (UniqueConstraint("device_id", "member_id", name="uq_device_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    member_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    role: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    software_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    device: Mapped[Device] = relationship(back_populates="members")


class Interface(Base):
    """A discovered interface of one device (SYSTEM_SPEC.md §10).

    Business identity is `(device_id, normalized_name)` — never `if_index`.
    `if_index` is mutable metadata refreshed on every discovery (§10); a
    changed ifIndex therefore updates the row instead of duplicating it, and
    a `monitored` flag survives rediscovery (§12).
    """

    __tablename__ = "interfaces"
    __table_args__ = (
        UniqueConstraint("device_id", "normalized_name", name="uq_interface_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    # Lower-cased, whitespace-collapsed interface name; stable across reboots.
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    # Name as reported by the device (e.g. "Ten-GigabitEthernet1/0/1").
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Mutable metadata: refreshed on every discovery (SYSTEM_SPEC.md §10).
    if_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    admin_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    oper_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    speed_bps: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_aggregation: Mapped[bool] = mapped_column(default=False, nullable=False)
    monitored: Mapped[bool] = mapped_column(default=False, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    device: Mapped[Device] = relationship(back_populates="interfaces")
    aggregation_memberships: Mapped[list[AggregationMember]] = relationship(
        foreign_keys="AggregationMember.member_interface_id",
        back_populates="member_interface",
        cascade="all, delete-orphan",
    )
    aggregate_members: Mapped[list[AggregationMember]] = relationship(
        foreign_keys="AggregationMember.aggregation_interface_id",
        back_populates="aggregation_interface",
        cascade="all, delete-orphan",
    )


class AggregationMember(Base):
    """aggregation interface -> physical member interface (SYSTEM_SPEC.md §11)."""

    __tablename__ = "aggregation_members"
    __table_args__ = (
        UniqueConstraint(
            "aggregation_interface_id",
            "member_interface_id",
            name="uq_aggregation_member",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    aggregation_interface_id: Mapped[int] = mapped_column(
        ForeignKey("interfaces.id", ondelete="CASCADE"), nullable=False
    )
    member_interface_id: Mapped[int] = mapped_column(
        ForeignKey("interfaces.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    aggregation_interface: Mapped[Interface] = relationship(
        foreign_keys=[aggregation_interface_id], back_populates="aggregate_members"
    )
    member_interface: Mapped[Interface] = relationship(
        foreign_keys=[member_interface_id], back_populates="aggregation_memberships"
    )
