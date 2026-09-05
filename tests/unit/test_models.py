"""Unit checks for Wave 1/2 ORM model registration (no database required)."""

from sqlalchemy import ColumnDefault, UniqueConstraint, inspect

from backend.db import models  # noqa: F401  (registers tables)
from backend.db.base import Base


def _columns(table: str) -> set[str]:
    return {c.name for c in inspect(Base.metadata.tables[table]).columns}


def test_wave1_tables_are_registered() -> None:
    tables = set(Base.metadata.tables)
    assert {"devices", "device_members", "interfaces", "aggregation_members"} <= tables


def test_wave2_tables_are_registered() -> None:
    tables = set(Base.metadata.tables)
    assert {"device_poll_runs", "device_metrics", "interface_metrics"} <= tables


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


def _unique_constraints(table: str) -> set[tuple[str, ...]]:
    return {
        tuple(col.name for col in uq.columns)
        for uq in Base.metadata.tables[table].constraints
        if isinstance(uq, UniqueConstraint)
    }


def test_poll_run_identity_is_device_and_planned_cycle() -> None:
    """One poll run per planned cycle per device (SYSTEM_SPEC.md §8)."""

    assert ("device_id", "cycle_started_at") in _unique_constraints("device_poll_runs")


def test_device_metric_is_one_row_per_poll_run() -> None:
    assert ("poll_run_id",) in _unique_constraints("device_metrics")


def test_interface_metric_is_one_row_per_interface_and_run() -> None:
    assert ("poll_run_id", "interface_id") in _unique_constraints("interface_metrics")


def test_metric_tables_carry_retention_time_columns() -> None:
    """Retention (§24) and weekly windows both need plain time columns."""

    for table, column in (
        ("device_poll_runs", "cycle_started_at"),
        ("device_metrics", "collected_at"),
        ("interface_metrics", "collected_at"),
    ):
        assert column in _columns(table)


def test_interface_metric_utilization_columns() -> None:
    assert {
        "in_utilization_percent",
        "out_utilization_percent",
        "utilization_elapsed_seconds",
        "utilization_rebaselined",
    } <= _columns("interface_metrics")
