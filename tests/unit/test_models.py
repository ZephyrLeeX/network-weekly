"""Unit checks for Wave 1 ORM model registration (no database required)."""

from sqlalchemy import ColumnDefault, UniqueConstraint, inspect

from backend.db import models  # noqa: F401  (registers tables)
from backend.db.base import Base


def _columns(table: str) -> set[str]:
    return {c.name for c in inspect(Base.metadata.tables[table]).columns}


def test_wave1_tables_are_registered() -> None:
    tables = set(Base.metadata.tables)
    assert {"devices", "device_members", "interfaces", "aggregation_members"} <= tables


def test_device_columns() -> None:
    assert {
        "id",
        "name",
        "management_ip",
        "model_family",
        "expected_irf_member_count",
        "credential_profile",
        "enabled",
        "sys_name",
        "sys_description",
        "sys_object_id",
        "software_version",
        "created_at",
        "updated_at",
    } <= _columns("devices")


def test_interface_columns() -> None:
    assert {
        "device_id",
        "normalized_name",
        "display_name",
        "description",
        "if_index",
        "admin_state",
        "oper_state",
        "speed_bps",
        "is_aggregation",
        "monitored",
        "last_seen_at",
    } <= _columns("interfaces")


def test_interface_identity_is_device_and_normalized_name() -> None:
    table = Base.metadata.tables["interfaces"]
    uqs = {
        tuple(col.name for col in uq.columns)
        for uq in table.constraints
        if isinstance(uq, UniqueConstraint)
    }
    assert ("device_id", "normalized_name") in uqs


def test_defaults_for_discovered_interfaces() -> None:
    table = Base.metadata.tables["interfaces"]
    agg_default = table.c.is_aggregation.default
    monitored_default = table.c.monitored.default
    assert isinstance(agg_default, ColumnDefault)
    assert isinstance(monitored_default, ColumnDefault)
    assert agg_default.arg is False
    assert monitored_default.arg is False
