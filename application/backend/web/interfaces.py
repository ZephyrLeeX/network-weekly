"""Authenticated interface discovery and batch priority selection Web routes.

The worker owns SNMP and credentials. Web only queues a PostgreSQL job, reads
its safe status, and saves monitored flags for one device after CSRF checks.
"""

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from starlette.responses import Response

from backend.db.engine import get_session_factory
from backend.db.models import Device, Interface, InterfaceDiscoveryJob
from backend.monitoring.interface_config import (
    InterfaceNotFoundError,
    interface_overview,
    set_monitored_for_device,
)
from backend.monitoring.interface_discovery import SAFE_FAILURE, request_discovery
from backend.web.deps import AdminDep
from backend.web.pages import (
    DeviceEntry,
    InterfaceListEntry,
    interfaces_page,
    not_found_page,
)
from backend.web.security import csrf_for_render, csrf_guard, set_csrf_cookie

router = APIRouter()


def _device_entries(db: DbSession) -> list[DeviceEntry]:
    devices = db.execute(select(Device).order_by(Device.name.asc())).scalars().all()
    cached_ids = set(db.scalars(select(Interface.device_id).distinct()).all())
    entries = []
    for device in devices:
        has_interfaces = device.id in cached_ids
        jobs = select(InterfaceDiscoveryJob).where(InterfaceDiscoveryJob.device_id == device.id)
        if has_interfaces:
            jobs = jobs.where(InterfaceDiscoveryJob.status.in_(["pending", "running"]))
        latest = db.scalar(jobs.order_by(InterfaceDiscoveryJob.id.desc()).limit(1))
        if latest is not None and latest.status == "succeeded":
            latest = None  # a successful but empty inventory is still uncached
        entries.append(
            DeviceEntry(
                device_id=device.id,
                name=device.name,
                has_interfaces=has_interfaces,
                discovery_status=latest.status if latest is not None else None,
                discovery_job_id=latest.id if latest is not None else None,
            )
        )
    return entries


def _selected_device(devices: list[DeviceEntry], raw_device_id: str | None) -> DeviceEntry | None:
    if not raw_device_id:
        return None
    try:
        device_id = int(raw_device_id)
    except ValueError:
        return None
    return next((device for device in devices if device.device_id == device_id), None)


@router.get("/interfaces")
async def interfaces_home(
    request: Request, admin: AdminDep, device_id: str | None = None, job_id: int | None = None
) -> Response:
    """The §12 configuration page: device selection + interface overview."""

    del admin  # enforced by the dependency
    with get_session_factory()() as db:
        devices = _device_entries(db)
        selected = _selected_device(devices, device_id)
        rows = interface_overview(db, selected.device_id) if selected else []
        job = None
        if selected is not None:
            if job_id is not None:
                candidate = db.get(InterfaceDiscoveryJob, job_id)
                if candidate is not None and candidate.device_id == selected.device_id:
                    job = candidate
            elif selected.discovery_job_id is not None:
                job = db.get(InterfaceDiscoveryJob, selected.discovery_job_id)
    entries = [
        InterfaceListEntry(
            interface_id=row.interface_id,
            display_name=row.display_name,
            description=row.description,
            admin_state=row.admin_state,
            oper_state=row.oper_state,
            speed_bps=row.speed_bps,
            is_aggregation=row.is_aggregation,
            monitored=row.monitored,
            aggregation_members=row.aggregation_members,
            member_of=row.member_of,
        )
        for row in rows
    ]
    csrf_token, fresh = csrf_for_render(request)
    response = HTMLResponse(
        interfaces_page(
            devices,
            selected,
            entries,
            csrf_token,
            job_id=job.id if job is not None else None,
            job_status=job.status if job is not None else None,
        )
    )
    if fresh:
        set_csrf_cookie(response, csrf_token)
    return response


@router.post("/interfaces/{device_id}/discover")
async def discover(request: Request, device_id: int, admin: AdminDep) -> Response:
    del admin  # enforced by the dependency
    csrf_response = await csrf_guard(request)
    if csrf_response is not None:
        return csrf_response

    def create() -> int:
        with get_session_factory()() as db:
            job = request_discovery(db, device_id)
            job_id = job.id
            db.commit()
            return job_id

    try:
        job_id = await run_in_threadpool(create)
    except ValueError:
        return HTMLResponse(not_found_page("设备不存在"), status_code=404)
    return RedirectResponse(f"/interfaces?device_id={device_id}&job_id={job_id}", status_code=303)


@router.get("/interfaces/{device_id}/discovery/{job_id}")
async def discovery_status(device_id: int, job_id: int, admin: AdminDep) -> Response:
    del admin

    def read() -> dict[str, str | int] | None:
        with get_session_factory()() as db:
            job = db.get(InterfaceDiscoveryJob, job_id)
            if job is None or job.device_id != device_id:
                return None
            return {
                "id": job.id,
                "status": job.status,
                "error": SAFE_FAILURE if job.status == "failed" else "",
            }

    result = await run_in_threadpool(read)
    return JSONResponse(
        result if result is not None else {"error": "任务不存在"},
        status_code=200 if result is not None else 404,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/interfaces/{device_id}/monitored")
async def save_monitored(request: Request, device_id: int, admin: AdminDep) -> Response:
    del admin
    csrf_response = await csrf_guard(request)
    if csrf_response is not None:
        return csrf_response
    form = await request.form()
    try:
        selected_ids = {int(str(value)) for value in form.getlist("interface_id")}
    except ValueError, TypeError:
        return HTMLResponse(not_found_page("接口不存在"), status_code=404)

    try:
        with get_session_factory()() as db:
            if db.get(Device, device_id) is None:
                return HTMLResponse(not_found_page("设备不存在"), status_code=404)
            set_monitored_for_device(db, device_id, selected_ids)
            db.commit()
    except InterfaceNotFoundError:
        return HTMLResponse(not_found_page("接口不存在"), status_code=404)
    return RedirectResponse(f"/interfaces?device_id={device_id}", status_code=303)
