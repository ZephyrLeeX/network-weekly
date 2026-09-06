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
from backend.db.models import AggregationMember, Device, Interface, User, UserSession
from backend.main import app

pytestmark = pytest.mark.integration

USERNAME = "admin"
PASSWORD = "pw-web-interfaces-tests"


@pytest.fixture(autouse=True)
def _admin(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(UserSession).delete()
        session.query(User).delete()
        session.query(AggregationMember).delete()
        session.query(Interface).delete()
        session.query(Device).delete()
        session.commit()
        initialize_admin(session, USERNAME, PASSWORD)
    yield
    with Session(db_engine) as session:
        session.query(UserSession).delete()
        session.query(User).delete()
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
    assert f'href="/interfaces?device_id={topology["device_id"]}"' in page.text
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
    assert text.count(">否") == 3
    assert "设为监控" in text


def test_toggle_monitored_on_and_off_via_web(
    client: TestClient, db_engine: Engine, topology: dict[str, int]
) -> None:
    device_id = topology["device_id"]
    member_id = topology["member1_id"]
    csrf = _csrf(client)

    on = client.post(
        f"/interfaces/{member_id}/monitored",
        data={"monitored": "true", "csrf_token": csrf},
    )
    assert on.status_code == 303
    assert f"/interfaces?device_id={device_id}" in on.headers["location"]

    with Session(db_engine) as session:
        assert session.get(Interface, member_id) is not None
        assert session.get(Interface, member_id).monitored  # type: ignore[union-attr]
    assert "是" in client.get(f"/interfaces?device_id={device_id}").text
    assert "取消监控" in client.get(f"/interfaces?device_id={device_id}").text

    off = client.post(
        f"/interfaces/{member_id}/monitored",
        data={"monitored": "false", "csrf_token": csrf},
    )
    assert off.status_code == 303
    with Session(db_engine) as session:
        assert session.get(Interface, member_id) is not None
        assert not session.get(Interface, member_id).monitored  # type: ignore[union-attr]


def test_aggregation_toggle_never_toggles_members(
    client: TestClient, db_engine: Engine, topology: dict[str, int]
) -> None:
    """§11 through the web route: selecting the aggregate selects ONLY it."""

    csrf = _csrf(client)

    client.post(
        f"/interfaces/{topology['aggregate_id']}/monitored",
        data={"monitored": "true", "csrf_token": csrf},
    )

    with Session(db_engine) as session:
        assert session.get(Interface, topology["aggregate_id"]).monitored is True  # type: ignore[union-attr]
        assert session.get(Interface, topology["member1_id"]).monitored is False  # type: ignore[union-attr]
        assert session.get(Interface, topology["member2_id"]).monitored is False  # type: ignore[union-attr]

    # A member can then be selected independently (and the aggregate stays).
    client.post(
        f"/interfaces/{topology['member2_id']}/monitored",
        data={"monitored": "true", "csrf_token": csrf},
    )
    with Session(db_engine) as session:
        assert session.get(Interface, topology["aggregate_id"]).monitored is True  # type: ignore[union-attr]
        assert session.get(Interface, topology["member1_id"]).monitored is False  # type: ignore[union-attr]
        assert session.get(Interface, topology["member2_id"]).monitored is True  # type: ignore[union-attr]


def test_toggle_requires_valid_csrf(
    client: TestClient, db_engine: Engine, topology: dict[str, int]
) -> None:
    missing = client.post(f"/interfaces/{topology['member1_id']}/monitored", data={})
    forged = client.post(
        f"/interfaces/{topology['member1_id']}/monitored",
        data={"monitored": "true", "csrf_token": "forged"},
    )

    assert missing.status_code == 403
    assert forged.status_code == 403
    with Session(db_engine) as session:
        assert session.get(Interface, topology["member1_id"]).monitored is False  # type: ignore[union-attr]


def test_toggle_unknown_interface_is_denied(client: TestClient) -> None:
    response = client.post(
        "/interfaces/999999/monitored", data={"monitored": "true", "csrf_token": _csrf(client)}
    )

    assert response.status_code == 404


def test_unknown_device_id_renders_selection_list(
    client: TestClient, topology: dict[str, int]
) -> None:
    for bad in ("999999", "abc"):
        page = client.get(f"/interfaces?device_id={bad}")

        assert page.status_code == 200
        assert "请先选择一台设备" in page.text
