"""Wave 0/1/2/3 ORM models.

Wave 0: the foundation-required `worker_heartbeat` table (SYSTEM_SPEC.md §23).
Wave 1: `devices`, `device_members`, `interfaces` and `aggregation_members`
(SYSTEM_SPEC.md §2.2/§10/§11/§23).
Wave 2: `device_poll_runs`, `device_metrics` and `interface_metrics`
(W02-T001, SYSTEM_SPEC.md §7.1/§8/§15/§23). Reachability/interface incident
and IRF observation tables arrive with their own Wave 2 tasks. All business
timestamps use TIMESTAMPTZ (§3/§23).
Wave 3: `report_jobs` + `weekly_reports` (W03-T009, SYSTEM_SPEC.md §4/§23).
Wave 4: `users` (W04-T001, §21 single administrator) — the password lives
only as a salted scrypt hash, never as plaintext.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
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


# --- Wave 2: monitoring pipeline (W02-T001) -------------------------------------


class DevicePollRun(Base):
    """One planned 5-minute DEVICE_POLL of one logical device (SYSTEM_SPEC.md §7.1/§8).

    Exactly one row per `(device_id, cycle_started_at)` — the planned cycle
    start, aligned to the 5-minute boundary — whatever the outcome, so
    Coverage counts stay honest (§18): every planned cycle lands a row. A
    cycle that could not even be attempted is bookkept as FAILED with the
    reason as its failed section (`overlap` = the device's previous poll was
    still in flight, `credentials` = no usable SNMP secret); it is NOT
    treated as device evidence — the §9 reachability and §13 interface state
    machines never advance for such a row, and the §9 continuity anchor
    treats the cycle as a gap.

    `status` is SUCCESS / PARTIAL / FAILED (§8). `ssh_reachable` is the §9.1
    probe result — NULL when the probe did not run (SNMP channel healthy or
    the cycle was never attempted). `failed_sections` holds the comma-joined
    section *names* only; section error strings are deliberately not
    persisted here.
    """

    __tablename__ = "device_poll_runs"
    __table_args__ = (
        UniqueConstraint("device_id", "cycle_started_at", name="uq_device_poll_run_cycle"),
        Index("ix_device_poll_runs_cycle_started_at", "cycle_started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    cycle_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    ssh_reachable: Mapped[bool | None] = mapped_column(nullable=True)
    failed_sections: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    device: Mapped[Device] = relationship()
    device_metric: Mapped[DeviceMetric | None] = relationship(
        back_populates="poll_run", cascade="all, delete-orphan", uselist=False
    )
    interface_metrics: Mapped[list[InterfaceMetric]] = relationship(
        back_populates="poll_run", cascade="all, delete-orphan"
    )


class DeviceMetric(Base):
    """Device-level CPU/memory sample for one poll cycle (SYSTEM_SPEC.md §14).

    Weekly statistics are per *logical device* (§14); a device (an IRF fabric
    included) therefore gets one row per cycle with the peak usage across the
    entities the collector reported. NULL means the section produced no valid
    data this cycle — a missing sample must stay missing (§14: missing
    samples break continuity and are never backfilled).
    """

    __tablename__ = "device_metrics"
    __table_args__ = (
        UniqueConstraint("poll_run_id", name="uq_device_metric_poll_run"),
        Index("ix_device_metrics_device_collected", "device_id", "collected_at"),
        Index("ix_device_metrics_collected_at", "collected_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_run_id: Mapped[int] = mapped_column(
        ForeignKey("device_poll_runs.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalized from poll_run so weekly statistics never need a join to
    # filter by device/time; always equal to poll_run.device_id.
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    # Actual sample time (end of the collection), NOT the planned cycle time:
    # utilization-style interval math and weekly windows use real timestamps.
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cpu_usage_percent: Mapped[float | None] = mapped_column(nullable=True)
    memory_usage_percent: Mapped[float | None] = mapped_column(nullable=True)

    poll_run: Mapped[DevicePollRun] = relationship(back_populates="device_metric")


class DeviceMonitoringState(Base):
    """Per-device cycle tracking for the §9 reachability state machine.

    One row per device. Holds the consecutive failed/reachable cycle counts
    with the timestamps of the first cycle of each run, plus
    `last_cycle_started_at` as the continuity anchor: a cycle that does not
    exactly follow the previous planned cycle resets both runs (§9.2/§9.3
    count *consecutive* cycles; gaps must never confirm a state).
    """

    __tablename__ = "device_monitoring_state"

    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    # "normal" or "down" (SYSTEM_SPEC.md §9.2).
    state: Mapped[str] = mapped_column(default="normal", nullable=False)
    last_cycle_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_failed_cycles: Mapped[int] = mapped_column(
        SmallInteger, default=0, nullable=False
    )
    consecutive_reachable_cycles: Mapped[int] = mapped_column(
        SmallInteger, default=0, nullable=False
    )
    # First cycle of the current failed/reachable run (the incident anchor).
    failed_run_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reachable_run_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class DeviceReachabilityIncident(Base):
    """A confirmed device Down episode (SYSTEM_SPEC.md §9.4, long-term).

    Open incident: `recovered_at` is NULL (device still Down at period end —
    the §19 异常 condition). Long-term record: retention never touches it.
    """

    __tablename__ = "device_reachability_incidents"
    __table_args__ = (
        Index("ix_device_reachability_incidents_device", "device_id", "recovered_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    # First failed cycle of the confirming run (§9.4 started_at).
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # First reachable cycle of the confirming recovery run; NULL while Down.
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    device: Mapped[Device] = relationship()


class InterfaceMetric(Base):
    """Per-interface sample for one poll cycle (SYSTEM_SPEC.md §10/§15/§16).

    Counters are the cumulative values read this cycle; utilization columns
    are the delta-based percentages computed against the previous valid
    sample (W02-T003). NULL utilization with `utilization_rebaselined` set
    marks a baseline (re)establishment — never a fake 0% or a fake spike.
    """

    __tablename__ = "interface_metrics"
    __table_args__ = (
        UniqueConstraint("poll_run_id", "interface_id", name="uq_interface_metric_sample"),
        Index("ix_interface_metrics_interface_collected", "interface_id", "collected_at"),
        Index("ix_interface_metrics_collected_at", "collected_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_run_id: Mapped[int] = mapped_column(
        ForeignKey("device_poll_runs.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    interface_id: Mapped[int] = mapped_column(
        ForeignKey("interfaces.id", ondelete="CASCADE"), nullable=False
    )
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    admin_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    oper_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Speed metadata snapshot for this cycle (§15.1: utilization needs the
    # effective speed of the sample interval, not of "now").
    speed_bps: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Cumulative counters (§7.1/§16); None when the device reports none.
    in_octets: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    out_octets: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    in_errors: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    out_errors: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    in_discards: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    out_discards: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fcs_errors: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Delta-based utilization vs the previous valid sample (W02-T003).
    in_utilization_percent: Mapped[float | None] = mapped_column(nullable=True)
    out_utilization_percent: Mapped[float | None] = mapped_column(nullable=True)
    # Actual interval (seconds) the utilization was computed over.
    utilization_elapsed_seconds: Mapped[float | None] = mapped_column(nullable=True)
    # True when this sample (re)established the counter baseline instead of
    # producing utilization (first sample, reset, invalid speed/interval).
    utilization_rebaselined: Mapped[bool] = mapped_column(default=False, nullable=False)

    poll_run: Mapped[DevicePollRun] = relationship(back_populates="interface_metrics")
    interface: Mapped[Interface] = relationship()


class InterfaceMonitoringState(Base):
    """Per-interface valid-sample tracking for the §13 state machine.

    One row per interface (created lazily for monitored interfaces). Counts
    only *valid* samples — oper state exactly "up" or "down" (§13.1/§13.2);
    missing or undetermined samples do not participate (§13.3) and do not
    break a valid-sample run.
    """

    __tablename__ = "interface_monitoring_state"

    interface_id: Mapped[int] = mapped_column(
        ForeignKey("interfaces.id", ondelete="CASCADE"), primary_key=True
    )
    # "normal" or "down" (SYSTEM_SPEC.md §13.1).
    state: Mapped[str] = mapped_column(default="normal", nullable=False)
    consecutive_down_samples: Mapped[int] = mapped_column(
        SmallInteger, default=0, nullable=False
    )
    consecutive_up_samples: Mapped[int] = mapped_column(
        SmallInteger, default=0, nullable=False
    )
    # First valid sample of the current down/up run (the incident anchor).
    down_run_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    up_run_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class InterfaceStateIncident(Base):
    """A confirmed monitored-interface Down episode (§13, long-term).

    Only monitored interfaces ever get incidents. Open incident:
    `recovered_at` is NULL (the §19 异常 condition at period end).
    """

    __tablename__ = "interface_state_incidents"
    __table_args__ = (
        Index("ix_interface_state_incidents_interface", "interface_id", "recovered_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    interface_id: Mapped[int] = mapped_column(
        ForeignKey("interfaces.id", ondelete="CASCADE"), nullable=False
    )
    # First valid Down sample of the confirming run.
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # First valid Up sample of the confirming recovery run; NULL while Down.
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    interface: Mapped[Interface] = relationship()


class SystemSetting(Base):
    """One configurable system value (SYSTEM_SPEC.md §23, W02-T007).

    Values are stored as text; key validity and numeric ranges are enforced
    by :mod:`backend.monitoring.thresholds` (e.g. the §14 thresholds).
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class IrfMemberObservation(Base):
    """One member of one ~15-minute IRF observation (SYSTEM_SPEC.md §17).

    Only SUCCESSFUL observations produce rows: an SSH failure records
    nothing, so "missing" always means "the device itself reported the
    member absent" — never "we could not look". `role` is the role exactly
    as reported; `role_changed` with `previous_role` marks a reliably
    identified change (both sides non-NULL). Long-term record (§24).
    """

    __tablename__ = "irf_member_observations"
    __table_args__ = (
        Index(
            "ix_irf_member_observations_device_member_time",
            "device_id",
            "member_id",
            "observed_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    member_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # False = the (successful) observation did not see this member.
    observed: Mapped[bool] = mapped_column(nullable=False)
    role: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_changed: Mapped[bool] = mapped_column(default=False, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


# --- Wave 3: weekly reports (W03-T009) ------------------------------------------


class ReportJob(Base):
    """One weekly-report generation responsibility (SYSTEM_SPEC.md §4/§23).

    Lifecycle: pending -> running -> succeeded | failed. A FAILED job is
    still ACTIVE (it owns the week and is retried every 10 minutes, §4.3)
    until one attempt succeeds. The partial unique index
    `uq_report_jobs_active_week` (status pending/running/failed) makes
    "同一统计周最多允许一个活跃生成任务" a database guarantee while a
    SUCCEEDED week can still get a fresh manual job (§4.4 regenerate).
    `last_error` holds a sanitized, readable summary (§4.3) — never
    secret-bearing material (§22.2).
    """

    __tablename__ = "report_jobs"
    __table_args__ = (
        Index(
            "uq_report_jobs_active_week",
            "week_code",
            unique=True,
            postgresql_where=sa_text("status IN ('pending', 'running', 'failed')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # ISO week-year code, e.g. "2026-W36" (§3).
    week_code: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # Exact statistics period [start, end), Asia/Shanghai instants (§3).
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    # "scheduled" (Monday 00:10, §4.1) or "manual" (regenerate, §4.4).
    trigger: Mapped[str] = mapped_column(Text, nullable=False, default="scheduled")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Sanitized readable summary of the last failure (§4.3); None when ok.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When the next retry may run (failed jobs only); NULL otherwise.
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class WeeklyReport(Base):
    """The one current weekly report per ISO week (SYSTEM_SPEC.md §4.4/§24).

    `status='success'` rows always point at a complete DOCX on the shared
    reports volume (`file_path`); the file is replaced only atomically
    (§5), so `file_path` never references a half-written file. A `failed`
    row records a generation failure for the report list (§20.2) and is
    NEVER written over a `success` row — regenerate keeps the old file
    downloadable until the new one succeeds (§4.4). Long-term (§24).
    """

    __tablename__ = "weekly_reports"
    __table_args__ = (
        Index("ix_weekly_reports_week_code", "week_code", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_code: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # "success" (file_path set) or "failed" (last_error set).
    status: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


# --- Wave 4: authentication (W04-T001) ------------------------------------------


class User(Base):
    """The single local administrator account (SYSTEM_SPEC.md §21, W04-T001).

    `password_hash` stores a salted scrypt hash in the self-describing
    format written by :func:`backend.auth.passwords.hash_password` — the
    plaintext password never enters this column, a log line or an error
    message (§21/§22.2).

    The system has exactly one administrator; every row carries
    `singleton = true`, so the unique index below makes "at most one
    account" a database guarantee instead of a service-level convention.
    """

    __tablename__ = "users"
    __table_args__ = (Index("uq_users_single_admin", "singleton", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # Always true — see the unique index above (§21: 单管理员).
    singleton: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
