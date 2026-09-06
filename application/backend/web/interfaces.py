"""Priority-interface configuration page (W04-T005, §11/§12/§20.3).

A thin Web layer over the W02-T005 `interface_config` service — the read
model comes from `interface_overview`, the ONLY write path is
`set_monitored`, which never cascades: selecting an aggregation interface
monitors only the aggregation logical interface and members remain
independently selectable (§11). The toggle POST is CSRF-protected and sits
behind :func:`backend.web.deps.require_admin`.
"""


from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from starlette.responses import Response

from backend.db.engine import get_session_factory
from backend.db.models import Device
from backend.monitoring.interface_config import (
    InterfaceNotFoundError,
    interface_overview,
    set_monitored,
)
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
    return [DeviceEntry(device_id=device.id, name=device.name) for device in devices]


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
    request: Request, admin: AdminDep, device_id: str | None = None
) -> Response:
    """The §12 configuration page: device selection + interface overview."""

    del admin  # enforced by the dependency
    with get_session_factory()() as db:
        devices = _device_entries(db)
        selected = _selected_device(devices, device_id)
        rows = interface_overview(db, selected.device_id) if selected else []
    entries = [
        InterfaceListEntry(
            interface_id=row.interface_id,
            display_name=row.display_name,
            description=row.description,
            admin_state=row.admin_state,
            oper_state=row.oper_state,
            is_aggregation=row.is_aggregation,
            monitored=row.monitored,
            aggregation_members=row.aggregation_members,
            member_of=row.member_of,
        )
        for row in rows
    ]
    csrf_token, fresh = csrf_for_render(request)
    response = HTMLResponse(interfaces_page(devices, selected, entries, csrf_token))
    if fresh:
        set_csrf_cookie(response, csrf_token)
    return response


@router.post("/interfaces/{interface_id}/monitored")
async def toggle_monitored(
    request: Request, interface_id: int, admin: AdminDep
) -> Response:
    """Toggle one interface's monitored flag via `set_monitored` (§11/§12).

    Deliberately no cascade anywhere in this route: whatever the interface
    is, ONLY its own flag changes — an aggregation toggle can never touch
    its members and vice versa.
    """

    del admin  # enforced by the dependency
    csrf_response = await csrf_guard(request)
    if csrf_response is not None:
        return csrf_response
    form = await request.form()
    monitored = str(form.get("monitored", "")) == "true"

    try:
        with get_session_factory()() as db:
            interface = set_monitored(db, interface_id, monitored)
            # set_monitored is the W02-T005 write path and does not commit
            # (the monitoring pipeline calls it inside its own transaction);
            # this route owns the transaction and commits it here.
            db.commit()
            device_id = interface.device_id
    except InterfaceNotFoundError:
        return HTMLResponse(not_found_page("接口不存在"), status_code=404)
    return RedirectResponse(f"/interfaces?device_id={device_id}", status_code=303)
