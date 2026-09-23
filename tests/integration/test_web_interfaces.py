"""Web-level priority-interface page tests (W04-T005, §11/§12/§20.3).

The page is a thin layer over the W02-T005 service: the tests pin the Web
behaviors (device selection, relationship display, CSRF-protected toggle,
NO aggregation/member cascade through the web route) against migrated
PostgreSQL.
"""

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend.auth.admin import initialize_admin
from backend.db.models import (
    AggregationMember,
    Device,
    Interface,
    InterfaceDiscoveryJob,
    User,
    UserSession,
)
from backend.main import app

pytestmark = pytest.mark.integration

USERNAME = "admin"
PASSWORD = "pw-web-interfaces-tests"


@pytest.fixture(autouse=True)
def _admin(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(UserSession).delete()
        session.query(User).delete()
        session.query(InterfaceDiscoveryJob).delete()
        session.query(AggregationMember).delete()
        session.query(Interface).delete()
        session.query(Device).delete()
        session.commit()
        initialize_admin(session, USERNAME, PASSWORD)
    yield
    with Session(db_engine) as session:
        session.query(UserSession).delete()
        session.query(User).delete()
        session.query(InterfaceDiscoveryJob).delete()
        session.query(AggregationMember).delete()
        session.query(Interface).delete()
        session.query(Device).delete()
        session.commit()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, follow_redirects=False) as test_client:
        assert _login(test_client).status_code == 303
        yield test_client


def _login(client: TestClient) -> httpx.Response:
    page = client.get("/login")
    marker = 'name="csrf_token" value="'
    start = page.text.index(marker) + len(marker)
    csrf = page.text[start : page.text.index('"', start)]
    return client.post(
        "/login", data={"username": USERNAME, "password": PASSWORD, "csrf_token": csrf}
    )


def _csrf(client: TestClient) -> str:
    """The authenticated home page always renders the logout form with the
    current cookie's token; the same value works for the toggle form."""

    page = client.get("/")
    marker = 'name="csrf_token" value="'
    start = page.text.index(marker) + len(marker)
    return page.text[start : page.text.index('"', start)]


@pytest.fixture
def topology(db_engine: Engine) -> dict[str, int]:
    """core-irf with aggregation Bridge-Aggr1 + members T1/T2 and a standalone."""

    with Session(db_engine) as session:
        device = Device(
            name="core-irf",
            management_ip="192.0.2.41",
            model_family="s10500x",
            expected_irf_member_count=2,
            credential_profile="default",
        )
        session.add(device)
        session.flush()

        def _interface(name: str, description: str, oper: str, aggregate: bool) -> Interface:
            return Interface(
                device_id=device.id,
                normalized_name=name.lower(),
                display_name=name,
                description=description,
                admin_state="up",
                oper_state=oper,
                speed_bps=10_000_000_000,
                is_aggregation=aggregate,
            )

        aggregate = _interface("Bridge-Aggr1", "uplink-po", "up", True)
        member1 = _interface("Ten-GigabitEthernet1/0/1", "uplink-m1", "up", False)
        member2 = _interface("Ten-GigabitEthernet1/0/2", "uplink-m2", "down", False)
        session.add_all([aggregate, member1, member2])
        session.flush()
        session.add_all(
            [
                AggregationMember(
                    aggregation_interface_id=aggregate.id, member_interface_id=member1.id
                ),
                AggregationMember(
                    aggregation_interface_id=aggregate.id, member_interface_id=member2.id
                ),
            ]
        )
        session.commit()
        return {
            "device_id": device.id,
            "aggregate_id": aggregate.id,
            "member1_id": member1.id,
            "member2_id": member2.id,
        }


def test_unauthenticated_interface_page_is_denied(db_engine: Engine) -> None:
    with TestClient(app, follow_redirects=False) as bare:
        assert bare.get("/interfaces").status_code == 303
        assert bare.post("/interfaces/1/discover", data={}).status_code == 303
        assert bare.post("/interfaces/1/monitored", data={}).status_code == 303


def test_page_without_devices_shows_hint(client: TestClient) -> None:
    page = client.get("/interfaces")

    assert page.status_code == 200
    assert "尚未发现设备" in page.text


def test_page_lists_devices_for_selection(
    client: TestClient, db_engine: Engine, topology: dict[str, int]
) -> None:
    page = client.get("/interfaces")

    assert page.status_code == 200
    assert f'action="/interfaces/{topology["device_id"]}/discover"' in page.text
    assert "core-irf" in page.text


def test_device_page_shows_names_states_and_relationships(
    client: TestClient, db_engine: Engine, topology: dict[str, int]
) -> None:
    page = client.get(f"/interfaces?device_id={topology['device_id']}")
    text = page.text

    assert page.status_code == 200
    # Interface names + descriptions (§12).
    assert "Bridge-Aggr1" in text and "uplink-po" in text
    assert "Ten-GigabitEthernet1/0/1" in text
    # Current admin/oper states.
    assert "up" in text and "down" in text
    # Aggregation/member relationship display (§11).
    assert "聚合接口（成员：Ten-GigabitEthernet1/0/1, Ten-GigabitEthernet1/0/2）" in text
    # All discovered interfaces default to unmonitored (§12).
    assert text.count('type="checkbox"') == 3
    assert "保存重点接口" in text


def test_bulk_selection_and_empty_clear(
    client: TestClient, db_engine: Engine, topology: dict[str, int]
) -> None:
    device_id = topology["device_id"]
    aggregate_id = topology["aggregate_id"]
    member_id = topology["member1_id"]
    csrf = _csrf(client)
    on = client.post(
        f"/interfaces/{device_id}/monitored",
        data={"interface_id": [str(aggregate_id), str(member_id)], "csrf_token": csrf},
    )
    assert on.status_code == 303
    with Session(db_engine) as session:
        assert session.get(Interface, aggregate_id).monitored  # type: ignore[union-attr]
        assert session.get(Interface, member_id).monitored  # type: ignore[union-attr]
        assert not session.get(Interface, topology["member2_id"]).monitored  # type: ignore[union-attr]
    off = client.post(f"/interfaces/{device_id}/monitored", data={"csrf_token": csrf})
    assert off.status_code == 303
    with Session(db_engine) as session:
        assert not session.get(Interface, aggregate_id).monitored  # type: ignore[union-attr]
        assert not session.get(Interface, member_id).monitored  # type: ignore[union-attr]


def test_bulk_rejects_cross_device_and_bad_csrf(
    client: TestClient, db_engine: Engine, topology: dict[str, int]
) -> None:
    with Session(db_engine) as session:
        other = Device(
            name="other",
            management_ip="192.0.2.99",
            model_family="s12500",
            credential_profile="other",
        )
        session.add(other)
        session.flush()
        foreign = Interface(device_id=other.id, normalized_name="x", display_name="x")
        session.add(foreign)
        session.commit()
        foreign_id = foreign.id
    device_id = topology["device_id"]
    url = f"/interfaces/{device_id}/monitored"
    assert (
        client.post(
            url, data={"interface_id": str(foreign_id), "csrf_token": _csrf(client)}
        ).status_code
        == 404
    )
    assert client.post(url, data={"interface_id": str(topology["member1_id"])}).status_code == 403
    with Session(db_engine) as session:
        assert not session.get(Interface, topology["member1_id"]).monitored  # type: ignore[union-attr]


def test_discovery_post_and_status(
    client: TestClient, db_engine: Engine, topology: dict[str, int]
) -> None:
    device_id = topology["device_id"]
    url = f"/interfaces/{device_id}/discover"
    assert client.post(url, data={}).status_code == 403
    first = client.post(url, data={"csrf_token": _csrf(client)})
    assert first.status_code == 303
    second = client.post(url, data={"csrf_token": _csrf(client)})
    assert second.headers["location"] == first.headers["location"]
    with Session(db_engine) as session:
        jobs = session.query(InterfaceDiscoveryJob).all()
        assert len(jobs) == 1
        job_id = jobs[0].id
    status = client.get(f"/interfaces/{device_id}/discovery/{job_id}")
    assert status.json() == {"id": job_id, "status": "pending", "error": ""}
    assert "正在获取接口" in client.get(first.headers["location"]).text
    assert client.get(f"/interfaces/{device_id}/discovery/999999").status_code == 404


def test_static_assets_are_local(client: TestClient) -> None:
    page = client.get("/reports")
    assert "/static/app.css" in page.text and "/static/app.js" in page.text
    assert "https://" not in page.text and "http://" not in page.text
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_unknown_device_id_renders_selection_list(
    client: TestClient, topology: dict[str, int]
) -> None:
    for bad in ("999999", "abc"):
        page = client.get(f"/interfaces?device_id={bad}")

        assert page.status_code == 200
        assert "请先选择一台设备" in page.text


def test_relationship_names_are_html_escaped(client: TestClient, db_engine: Engine) -> None:
    """W04-AUDIT: interface/aggregation names are device-reported text and
    untrusted — a stored <script>/<img onerror> name may only ever reach
    the page as escaped text, in the relationship cell like everywhere else."""

    with Session(db_engine) as session:
        device = Device(
            name="xss-irf",
            management_ip="192.0.2.44",
            model_family="s10500x",
            expected_irf_member_count=1,
            credential_profile="default",
        )
        session.add(device)
        session.flush()

        def _interface(name: str, aggregate: bool) -> Interface:
            return Interface(
                device_id=device.id,
                normalized_name=name.lower(),
                display_name=name,
                description="desc-<b>not-bold</b>",
                admin_state="up",
                oper_state="up",
                speed_bps=10_000_000_000,
                is_aggregation=aggregate,
            )

        aggregate = _interface('<script>alert("agg")</script>', True)
        member = _interface("<img src=x onerror=alert(1)>", False)
        session.add_all([aggregate, member])
        session.flush()
        session.add(
            AggregationMember(aggregation_interface_id=aggregate.id, member_interface_id=member.id)
        )
        session.commit()
        device_id = device.id

    page = client.get(f"/interfaces?device_id={device_id}")
    text = page.text

    assert page.status_code == 200
    # The escaped forms appear — including in BOTH relationship directions
    # (聚合接口成员 list and 属于聚合 list).
    assert "&lt;script&gt;alert(&quot;agg&quot;)&lt;/script&gt;" in text
    assert "&lt;img src=x onerror=alert(1)&gt;" in text
    assert "聚合接口（成员：&lt;img src=x onerror=alert(1)&gt;）" in text
    assert "属于聚合：&lt;script&gt;alert(&quot;agg&quot;)&lt;/script&gt;" in text
    # Raw tags never enter the HTML — the stored payload stays inert text.
    assert "<script>" not in text
    assert "<img src=x onerror" not in text
    assert "<b>not-bold</b>" not in text
