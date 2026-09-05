"""Integration tests for IRF member observation persistence (W02-T008).

`apply_irf_observation` against the real migrated PostgreSQL: missing,
reappearance and reliably-identified role changes are recorded; a failed
observation records nothing; standalone devices never reach the loader.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from backend.collect.dto import IrfMemberSample
from backend.db.models import (
    Device,
    DeviceMember,
    IrfMemberObservation,
)
from backend.monitoring.credentials import build_irf_contexts
from backend.monitoring.irf import apply_irf_observation

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
STEP = timedelta(minutes=15)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        session.query(IrfMemberObservation).delete()
        session.query(DeviceMember).delete()
        session.query(Device).delete()
        session.commit()
    yield


@pytest.fixture
def irf_device_id(db_engine: Engine) -> int:
    with Session(db_engine) as session:
        device = Device(
            name="core-s12500-irf",
            management_ip="192.0.2.71",
            model_family="s12500",
            expected_irf_member_count=2,
            credential_profile="default",
        )
        session.add(device)
        session.commit()
        return device.id


def _standalone_id(db_engine: Engine) -> int:
    with Session(db_engine) as session:
        device = Device(
            name="access-1",
            management_ip="192.0.2.72",
            model_family="s10500x",
            expected_irf_member_count=None,
            credential_profile="default",
        )
        session.add(device)
        session.commit()
        return device.id


SECRETS_WITH_SSH = {
    "SNMP_COMMUNITY_DEFAULT": "community",
    "SSH_USERNAME_DEFAULT": "monitor",
    "SSH_PASSWORD_DEFAULT": "s3cret",
}


def test_first_observation_records_present_members(
    db_engine: Engine, irf_device_id: int
) -> None:
    with Session(db_engine) as session:
        report = apply_irf_observation(
            session,
            irf_device_id,
            [
                IrfMemberSample(member_id=1, role="Master"),
                IrfMemberSample(member_id=2, role="Slave"),
            ],
            T0,
        )
        session.commit()

        assert report.observed_members == (1, 2)
        assert report.missing_members == ()
        assert report.role_changes == ()

        rows = session.execute(select(IrfMemberObservation)).scalars().all()
        assert len(rows) == 2
        assert all(row.observed for row in rows)
        members = {
            m.member_id: m
            for m in session.execute(
                select(DeviceMember).where(DeviceMember.device_id == irf_device_id)
            )
            .scalars()
            .all()
        }
        assert members[1].role == "Master"
        assert members[2].role == "Slave"


def test_member_missing_then_reappear(db_engine: Engine, irf_device_id: int) -> None:
    with Session(db_engine) as session:
        apply_irf_observation(
            session,
            irf_device_id,
            [
                IrfMemberSample(member_id=1, role="Master"),
                IrfMemberSample(member_id=2, role="Slave"),
            ],
            T0,
        )
        session.commit()

        # Member 2 disappears from a SUCCESSFUL observation.
        report = apply_irf_observation(
            session, irf_device_id, [IrfMemberSample(member_id=1, role="Master")], T0 + STEP
        )
        session.commit()
        assert report.missing_members == (2,)
        assert report.reappeared_members == ()

        missing_row = session.execute(
            select(IrfMemberObservation).where(
                IrfMemberObservation.observed.is_(False),
            )
        ).scalar_one()
        assert missing_row.member_id == 2
        assert missing_row.observed_at == T0 + STEP
        # Member row kept: absence is an observation event, not a deletion.
        assert session.execute(select(func.count()).select_from(DeviceMember)).scalar_one() == 2

        # Member 2 reappears.
        report = apply_irf_observation(
            session,
            irf_device_id,
            [
                IrfMemberSample(member_id=1, role="Master"),
                IrfMemberSample(member_id=2, role="Slave"),
            ],
            T0 + 2 * STEP,
        )
        session.commit()
        assert report.observed_members == (1, 2)
        assert report.reappeared_members == (2,)


def test_role_change_recorded_only_when_reliable(
    db_engine: Engine, irf_device_id: int
) -> None:
    with Session(db_engine) as session:
        apply_irf_observation(
            session,
            irf_device_id,
            [
                IrfMemberSample(member_id=1, role="Master"),
                IrfMemberSample(member_id=2, role="Slave"),
            ],
            T0,
        )
        session.commit()

        # A failover: member 2 is now Master — reliably identified.
        report = apply_irf_observation(
            session,
            irf_device_id,
            [IrfMemberSample(member_id=2, role="Master")],
            T0 + STEP,
        )
        session.commit()
        assert report.role_changes == ((2, "Slave", "Master"),)

        change_row = session.execute(
            select(IrfMemberObservation).where(
                IrfMemberObservation.role_changed.is_(True)
            )
        ).scalar_one()
        assert change_row.member_id == 2
        assert change_row.previous_role == "Slave"
        assert change_row.role == "Master"
        # The missing member 1 got a plain absent row with no role claims.
        missing_row = session.execute(
            select(IrfMemberObservation).where(IrfMemberObservation.observed.is_(False))
        ).scalar_one()
        assert missing_row.role_changed is False
        assert missing_row.previous_role is None


def test_unknown_role_never_generates_change(db_engine: Engine, irf_device_id: int) -> None:
    with Session(db_engine) as session:
        apply_irf_observation(
            session, irf_device_id, [IrfMemberSample(member_id=1, role="Master")], T0
        )
        session.commit()
        # Same observation with role=None: nothing claimable, nothing changed.
        report = apply_irf_observation(
            session, irf_device_id, [IrfMemberSample(member_id=1, role=None)], T0 + STEP
        )
        session.commit()
        assert report.role_changes == ()
        member = session.execute(
            select(DeviceMember).where(DeviceMember.member_id == 1)
        ).scalar_one()
        assert member.role == "Master"  # stored role not erased


def test_loader_skips_standalone_devices(db_engine: Engine, irf_device_id: int) -> None:
    standalone_id = _standalone_id(db_engine)
    with Session(db_engine) as session:
        contexts = build_irf_contexts(session, SECRETS_WITH_SSH)
    # Only the IRF fabric (expected_irf_member_count > 1) is a target; the
    # standalone device is never SSH-probed for IRF (§17).
    assert [ctx.device_id for ctx in contexts] == [irf_device_id]
    assert standalone_id not in [ctx.device_id for ctx in contexts]
    assert contexts[0].ssh is not None
    assert contexts[0].device_name == "core-s12500-irf"


def test_loader_requires_ssh(db_engine: Engine, irf_device_id: int) -> None:
    with Session(db_engine) as session:
        contexts = build_irf_contexts(session, {"SNMP_COMMUNITY_DEFAULT": "c"})
    assert contexts == []  # no SSH credentials -> no IRF observation
