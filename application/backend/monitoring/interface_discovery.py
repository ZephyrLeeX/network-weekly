"""Serial, worker-owned inventory refresh for the priority-interface page.

This path never calls DEVICE_POLL or its state/metric persistence functions.
"""

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from sqlalchemy import Table, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from backend.collect.discovery import sync_aggregations, sync_interfaces
from backend.collect.h3c.collectors import collect_aggregations, collect_interface_inventory
from backend.collect.snmp import SnmpClient, SnmpConfig
from backend.db.models import Device, InterfaceDiscoveryJob
from backend.secrets import load_secrets, lookup_profile_secret

logger = logging.getLogger(__name__)
DISCOVERY_INTERVAL_SECONDS = 3
SAFE_FAILURE = "接口获取失败，请检查设备连接与采集配置后重试。"


def request_discovery(session: Session, device_id: int) -> InterfaceDiscoveryJob:
    """Create or reuse one active job; DB uniqueness resolves concurrent clicks."""

    if session.get(Device, device_id) is None:
        raise ValueError("device not found")
    table = cast(Table, InterfaceDiscoveryJob.__table__)
    statement = insert(table).values(device_id=device_id, status="pending").on_conflict_do_nothing()
    session.execute(statement)
    job = session.execute(
        select(InterfaceDiscoveryJob)
        .where(
            InterfaceDiscoveryJob.device_id == device_id,
            InterfaceDiscoveryJob.status.in_(["pending", "running"]),
        )
        .order_by(InterfaceDiscoveryJob.id.desc())
    ).scalar_one()
    return job


def run_next_discovery(session_factory: sessionmaker, secrets_file: Path) -> bool:
    """Claim and execute one job. Returns false when the queue is empty."""

    with session_factory() as session:
        job = session.execute(
            select(InterfaceDiscoveryJob)
            .where(InterfaceDiscoveryJob.status == "pending")
            .order_by(InterfaceDiscoveryJob.requested_at, InterfaceDiscoveryJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).scalar_one_or_none()
        if job is None:
            return False
        job.status = "running"
        job.started_at = datetime.now(UTC)
        job_id = job.id
        device_id = job.device_id
        session.commit()

    try:
        with session_factory() as session:
            device = session.get(Device, device_id)
            if device is None:
                raise ValueError("device removed")
            secrets = load_secrets(secrets_file)
            community = lookup_profile_secret(secrets, "SNMP_COMMUNITY", device.credential_profile)
            config = SnmpConfig(host=device.management_ip, community=community)
        client = SnmpClient(config)
        samples = collect_interface_inventory(client)
        mappings = collect_aggregations(client)
        with session_factory() as session:
            sync_interfaces(session, device_id, samples, datetime.now(UTC))
            session.flush()  # new interface ids are needed by topology reconciliation
            sync_aggregations(session, device_id, mappings)
            job = session.get(InterfaceDiscoveryJob, job_id)
            if job is not None:
                job.status = "succeeded"
                job.finished_at = datetime.now(UTC)
                job.last_error = None
            session.commit()
    except Exception as exc:  # noqa: BLE001 - no credential or transport text persists
        logger.warning("interface discovery %d failed (%s)", job_id, type(exc).__name__)
        with session_factory() as session:
            job = session.get(InterfaceDiscoveryJob, job_id)
            if job is not None:
                job.status = "failed"
                job.finished_at = datetime.now(UTC)
                job.last_error = SAFE_FAILURE
                session.commit()
    return True


def discovery_loop(
    stop: threading.Event, session_factory: sessionmaker, secrets_file: Path
) -> None:
    """One serial loop; reclaim an interrupted running job after worker restart."""

    while not stop.is_set():
        try:
            with session_factory() as session:
                session.execute(
                    update(InterfaceDiscoveryJob)
                    .where(InterfaceDiscoveryJob.status == "running")
                    .values(status="pending", started_at=None)
                )
                session.commit()
            while not stop.is_set() and run_next_discovery(session_factory, secrets_file):
                pass
        except Exception as exc:  # noqa: BLE001 - database outages retry next pass
            logger.warning("interface discovery loop failed (%s)", type(exc).__name__)
        stop.wait(DISCOVERY_INTERVAL_SECONDS)
