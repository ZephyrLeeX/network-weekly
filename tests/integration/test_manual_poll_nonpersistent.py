"""Manual diagnostics read inventory but never write monitoring data."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from backend.collect.dto import DeviceIdentity, EntityLoadSample
from backend.collect.session import DeviceCollectionOutcome, SectionResult
from backend.config import Settings
from backend.db.models import Device, DeviceMetric, DevicePollRun, InterfaceMetric
from backend.manual_poll import run_diagnostics
from backend.monitoring.credentials import build_contexts

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        for model in (InterfaceMetric, DeviceMetric, DevicePollRun, Device):
            session.query(model).delete()
        session.commit()
    yield


def _raw_counts(session: Session) -> tuple[int, int, int]:
    return (
        session.scalar(select(func.count()).select_from(DevicePollRun)) or 0,
        session.scalar(select(func.count()).select_from(DeviceMetric)) or 0,
        session.scalar(select(func.count()).select_from(InterfaceMetric)) or 0,
    )


def test_manual_diagnostic_does_not_write_poll_or_metric_rows(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        session.add(
            Device(
                name="core-manual",
                management_ip="192.0.2.30",
                model_family="s10500x",
                credential_profile="default",
                enabled=True,
            )
        )
        session.commit()
        contexts = build_contexts(
            session,
            {
                "SNMP_COMMUNITY_DEFAULT": "integration-community",
                "SSH_USERNAME_DEFAULT": "integration-user",
                "SSH_PASSWORD_DEFAULT": "integration-password",
            },
        )
        before = _raw_counts(session)

    def collect(*_args: object, **_kwargs: object) -> DeviceCollectionOutcome:
        return DeviceCollectionOutcome(
            device_name="core-manual",
            sections=[SectionResult("identity", "SUCCESS")],
            identity=DeviceIdentity("core", None, "1.3.6.1.4.1.25506", 100.0),
            cpu=[EntityLoadSample(1, 10.0)],
        )

    assert run_diagnostics(contexts, Settings(), collect=collect) == 0

    with Session(db_engine) as session:
        assert _raw_counts(session) == before == (0, 0, 0)
