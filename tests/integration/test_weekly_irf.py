"""Integration tests for the weekly IRF member summary
(W03-T004, §17) over migrated PostgreSQL.

Observation rows are seeded directly — apply_irf_observation (the only
writer) has its own W02-T008 tests; here the report-side reading is what
matters: missing windows, reappearance, missing-at-period-end, reliable
role changes, and 数据缺失 when nothing was observed.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend.db.models import Device, DeviceMember, IrfMemberObservation
from backend.reporting.irf_summary import weekly_irf_summaries
from backend.reporting.period import period_for_iso_week

pytestmark = pytest.mark.integration

# ISO 2026-W36: [2026-08-31, 2026-09-07) Asia/Shanghai.
PERIOD = period_for_iso_week(2026, 36)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        for model in (IrfMemberObservation, DeviceMember, Device):
            session.query(model).delete()
        session.commit()
    yield


def _fabric(session: Session, name: str, expected_members: int) -> Device:
    device = Device(
        name=name,
        management_ip=f"192.0.2.{abs(hash(name)) % 200 + 1}",
        model_family="s10500x",
        expected_irf_member_count=expected_members,
        credential_profile="default",
    )
    session.add(device)
    session.flush()
    for member_id in range(1, expected_members + 1):
        session.add(DeviceMember(device_id=device.id, member_id=member_id))
    session.flush()
    return device


def _observe(
    session: Session,
    device_id: int,
    member_id: int,
    observed_at: datetime,
    *,
    observed: bool,
    role: str | None = None,
    previous_role: str | None = None,
    role_changed: bool = False,
) -> None:
    session.add(
        IrfMemberObservation(
            device_id=device_id,
            member_id=member_id,
            observed=observed,
            role=role,
            previous_role=previous_role,
            role_changed=role_changed,
            observed_at=observed_at,
        )
    )


def test_all_members_present_all_week(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        fabric = _fabric(session, "irf-core", 2)
        for i in range(3):
            at = datetime(2026, 9, 1, 8, i, tzinfo=UTC)
            _observe(session, fabric.id, 1, at, observed=True, role="Master")
            _observe(session, fabric.id, 2, at, observed=True, role="Slave")
        session.commit()

        summaries = weekly_irf_summaries(session, PERIOD)
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.device_name == "irf-core"
        assert summary.expected_member_count == 2
        assert summary.observed_member_count == 2
        assert not summary.data_missing
        assert not summary.any_member_missing_at_period_end
        assert not summary.had_missing_member_during_week
        assert [m.member_id for m in summary.members] == [1, 2]
        assert all(m.latest_observed for m in summary.members)


def test_member_missing_then_reappearing_mid_week(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        fabric = _fabric(session, "irf-core", 2)
        at = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
        _observe(session, fabric.id, 1, at, observed=True, role="Master")
        _observe(session, fabric.id, 2, at, observed=True, role="Slave")
        missing_from = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
        _observe(session, fabric.id, 1, missing_from, observed=True, role="Master")
        _observe(session, fabric.id, 2, missing_from, observed=False)
        reappeared = datetime(2026, 9, 2, 9, 15, tzinfo=UTC)
        _observe(session, fabric.id, 1, reappeared, observed=True, role="Master")
        _observe(session, fabric.id, 2, reappeared, observed=True, role="Slave")
        session.commit()

        summary = weekly_irf_summaries(session, PERIOD)[0]
        member2 = summary.members[1]
        assert len(member2.missing_windows) == 1
        window = member2.missing_windows[0]
        assert window.started_at == missing_from
        assert window.reappeared_at == reappeared
        assert member2.latest_observed is True
        assert not summary.any_member_missing_at_period_end
        assert summary.had_missing_member_during_week  # 关注 condition
        # Current count comes from the latest in-week observation — the
        # reappearance itself, where the member is back.
        assert summary.observed_member_count == 2


def test_member_still_missing_at_period_end(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        fabric = _fabric(session, "irf-core", 2)
        t0 = datetime(2026, 9, 3, 8, 0, tzinfo=UTC)
        _observe(session, fabric.id, 1, t0, observed=True, role="Master")
        _observe(session, fabric.id, 2, t0, observed=False)
        t1 = datetime(2026, 9, 6, 23, 50, tzinfo=UTC)  # last in-week observation
        _observe(session, fabric.id, 1, t1, observed=True, role="Master")
        _observe(session, fabric.id, 2, t1, observed=False)
        session.commit()

        summary = weekly_irf_summaries(session, PERIOD)[0]
        member2 = summary.members[1]
        assert member2.missing_at_period_end
        assert summary.current_missing_members == (2,)
        assert summary.any_member_missing_at_period_end  # §19 异常
        # The missing window never closed inside the week.
        assert member2.missing_windows[0].reappeared_at is None
        assert member2.missing_windows[0].started_at == t0


def test_observations_outside_period_ignored(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        fabric = _fabric(session, "irf-core", 2)
        # Missing member LAST week, back this week: no in-week window.
        _observe(
            session, fabric.id, 2, datetime(2026, 8, 30, tzinfo=UTC), observed=False
        )
        at = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
        _observe(session, fabric.id, 1, at, observed=True, role="Master")
        _observe(session, fabric.id, 2, at, observed=True, role="Slave")
        # Missing again AFTER the period: belongs to the next week.
        _observe(session, fabric.id, 2, datetime(2026, 9, 7, 8, 0, tzinfo=UTC), observed=False)
        session.commit()

        summary = weekly_irf_summaries(session, PERIOD)[0]
        assert summary.members[1].missing_windows == ()
        assert summary.members[1].latest_observed is True
        assert not summary.any_member_missing_at_period_end


def test_role_change_in_week_recorded(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        fabric = _fabric(session, "irf-core", 2)
        at = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
        _observe(session, fabric.id, 1, at, observed=True, role="Master")
        _observe(session, fabric.id, 2, at, observed=True, role="Slave")
        changed = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)
        _observe(
            session,
            fabric.id,
            1,
            changed,
            observed=True,
            role="Slave",
            previous_role="Master",
            role_changed=True,
        )
        _observe(session, fabric.id, 2, changed, observed=True, role="Master")
        session.commit()

        summary = weekly_irf_summaries(session, PERIOD)[0]
        assert len(summary.role_changes) == 1
        change = summary.role_changes[0]
        assert change.member_id == 1
        assert change.observed_at == changed
        assert change.previous_role == "Master"
        assert change.new_role == "Slave"
        assert summary.members[0].latest_role == "Slave"


def test_week_without_observations_is_data_missing_not_member_loss(db_engine: Engine) -> None:
    """§6.2/§17: no observation = no evidence — never read as missing."""

    with Session(db_engine) as session:
        _fabric(session, "irf-core", 2)
        session.commit()

        summary = weekly_irf_summaries(session, PERIOD)[0]
        assert summary.data_missing
        assert summary.observed_member_count is None
        assert not summary.any_member_missing_at_period_end
        assert all(m.latest_observed is None for m in summary.members)
        assert summary.expected_member_count == 2


def test_standalone_devices_excluded(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        standalone = Device(
            name="standalone-1",
            management_ip="192.0.2.99",
            model_family="s10500x",
            expected_irf_member_count=1,
            credential_profile="default",
        )
        session.add(standalone)
        session.commit()

        assert weekly_irf_summaries(session, PERIOD) == []


def test_multiple_fabrics_ordered_by_name(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        fabric_b = _fabric(session, "irf-s12500-b", 2)
        fabric_a = _fabric(session, "irf-s10500x-a", 2)
        at = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
        for fabric in (fabric_a, fabric_b):
            _observe(session, fabric.id, 1, at, observed=True, role="Master")
            _observe(session, fabric.id, 2, at, observed=True, role="Slave")
        session.commit()

        summaries = weekly_irf_summaries(session, PERIOD)
        assert [s.device_name for s in summaries] == ["irf-s10500x-a", "irf-s12500-b"]
