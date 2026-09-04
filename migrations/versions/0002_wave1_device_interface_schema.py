"""wave 1: devices, device_members, interfaces, aggregation_members

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-04

Wave 1 schema (SYSTEM_SPEC.md §2.2/§10/§11/§23): logical devices, IRF
members, discovered interfaces and aggregation membership. Interface
business identity is `(device_id, normalized_name)`; `if_index` is mutable
metadata. All timestamps are TIMESTAMPTZ (§3/§23).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("management_ip", sa.Text(), nullable=False),
        sa.Column("model_family", sa.Text(), nullable=False),
        sa.Column("expected_irf_member_count", sa.SmallInteger(), nullable=True),
        sa.Column("credential_profile", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sys_name", sa.Text(), nullable=True),
        sa.Column("sys_description", sa.Text(), nullable=True),
        sa.Column("sys_object_id", sa.Text(), nullable=True),
        sa.Column("software_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default="now()"
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default="now()"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_device_name"),
    )
    op.create_table(
        "device_members",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("member_id", sa.SmallInteger(), nullable=False),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("software_version", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default="now()"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "member_id", name="uq_device_member"),
    )
    op.create_table(
        "interfaces",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("if_index", sa.Integer(), nullable=True),
        sa.Column("admin_state", sa.Text(), nullable=True),
        sa.Column("oper_state", sa.Text(), nullable=True),
        sa.Column("speed_bps", sa.BigInteger(), nullable=True),
        sa.Column("is_aggregation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("monitored", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default="now()"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "normalized_name", name="uq_interface_identity"),
    )
    op.create_index(
        "ix_interfaces_if_index", "interfaces", ["device_id", "if_index"], unique=False
    )
    op.create_table(
        "aggregation_members",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "aggregation_interface_id",
            sa.Integer(),
            sa.ForeignKey("interfaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "member_interface_id",
            sa.Integer(),
            sa.ForeignKey("interfaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default="now()"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "aggregation_interface_id", "member_interface_id", name="uq_aggregation_member"
        ),
    )


def downgrade() -> None:
    op.drop_table("aggregation_members")
    op.drop_index("ix_interfaces_if_index", table_name="interfaces")
    op.drop_table("interfaces")
    op.drop_table("device_members")
    op.drop_table("devices")
