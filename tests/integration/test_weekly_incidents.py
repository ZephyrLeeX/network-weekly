"""Integration tests for weekly device and priority-interface incident
summaries (W03-T003, §9.4/§13/§19) over migrated PostgreSQL.

Incident rows are seeded directly — the Wave 2 state machines that produce
them have their own tests; this task reads the long-term records the way
the report does.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend.db.models import (
    Device,
    DeviceReachabilityIncident,
    Interface,
    InterfaceStateIncident,
)
from backend.reporting.incidents import (
    weekly_device_incidents,
    weekly_interface_incidents,
)
from backend.reporting.period import period_for_iso_week

pytestmark = pytest.mark.integration

# ISO 2026-W36: [2026-08-31, 2026-09-07) Asia/Shanghai.
PERIOD = period_for_iso_week(2026, 36)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        for model in (
            DeviceReachabilityIncident,
            InterfaceStateIncident,
            Interface,
            Device,
        ):
            session.query(model).delete()
        session.commit()
    yield


def _device(session: Session, name: str) -> Device:
    device = Device(
        name=name,
        management_ip=f"192.0.2.{abs(hash(name)) % 200 + 1}",
        model_family="s10500x",
        credential_profile="default",
    )
    session.add(device)
    session.flush()
    return device


def _interface(
    session: Session, device_id: int, display_name: str, *, monitored: bool = True
) -> Interface:
    interface = Interface(
        device_id=device_id,
        normalized_name=display_name.lower(),
        display_name=display_name,
        monitored=monitored,
    )
    session.add(interface)
    session.flush()
    return interface


def _device_incident(
    session: Session, device_id: int, started_at: datetime, recovered_at: datetime | None
) -> None:
    session.add(
        DeviceReachabilityIncident(
            device_id=device_id, started_at=started_at, recovered_at=recovered_at
        )
    )


def test_in_week_down_and_recovery(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        started = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
        recovered = datetime(2026, 9, 1, 9, 30, tzinfo=UTC)
        _device_incident(session, device.id, started, recovered)
        session.commit()

        summaries = weekly_device_incidents(session, PERIOD)
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.device_name == "core-1"
        assert summary.down_count == 1
        assert not summary.ongoing_episodes
        episode = summary.episodes[0]
        assert episode.started_at == started
        assert episode.recovered_at == recovered
        assert not episode.started_before_period
        assert not episode.recovered_after_period_end
        assert episode.duration_seconds == 5400.0
        assert not episode.ongoing_at_period_end


def test_cross_week_incident_recovered_inside_week(db_engine: Engine) -> None:
    """Downed last week, recovered this week: counted here with full duration."""

    with Session(db_engine) as session:
        device = _device(session, "core-1")
        started = datetime(2026, 8, 29, 22, 0, tzinfo=UTC)  # previous week
        recovered = datetime(2026, 9, 2, 6, 0, tzinfo=UTC)
        _device_incident(session, device.id, started, recovered)
        session.commit()

        summary = weekly_device_incidents(session, PERIOD)[0]
        episode = summary.episodes[0]
        assert episode.started_before_period
        assert not episode.recovered_after_period_end
        assert episode.duration_seconds == (recovered - started).total_seconds()
        assert not episode.ongoing_at_period_end


def test_incident_open_at_period_end_is_ongoing(db_engine: Engine) -> None:
    """§19 异常 evidence: open (or recovered only after the week) at end."""

    with Session(db_engine) as session:
        device_open = _device(session, "core-open")
        device_late = _device(session, "core-late")
        _device_incident(session, device_open.id, datetime(2026, 9, 3, tzinfo=UTC), None)
        _device_incident(
            session,
            device_late.id,
            datetime(2026, 9, 4, tzinfo=UTC),
            datetime(2026, 9, 7, 12, 0, tzinfo=UTC),  # recovered after period end
        )
        session.commit()

        by_name = {s.device_name: s for s in weekly_device_incidents(session, PERIOD)}
        assert by_name["core-open"].ongoing_episodes[0].recovered_at is None
        assert by_name["core-late"].ongoing_episodes[0].recovered_after_period_end


def test_incidents_outside_period_excluded(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        # Fully before the week (recovered exactly at start = previous week).
        _device_incident(
            session, device.id, datetime(2026, 8, 20, tzinfo=UTC), PERIOD.start
        )
        # Started exactly at period end: belongs to the next week.
        _device_incident(session, device.id, PERIOD.end, None)
        session.commit()

        assert weekly_device_incidents(session, PERIOD) == []


def test_multiple_episodes_ordered_and_grouped(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device_b = _device(session, "core-b")
        device_a = _device(session, "core-a")
        _device_incident(
            session, device_b.id, datetime(2026, 9, 2, 10, 0, tzinfo=UTC), None
        )
        _device_incident(
            session, device_a.id, datetime(2026, 9, 1, 1, 0, tzinfo=UTC), None
        )
        _device_incident(
            session, device_a.id, datetime(2026, 9, 5, 2, 0, tzinfo=UTC), None
        )
        session.commit()

        summaries = weekly_device_incidents(session, PERIOD)
        assert [s.device_name for s in summaries] == ["core-a", "core-b"]
        assert summaries[0].down_count == 2
        starts = [e.started_at for e in summaries[0].episodes]
        assert starts == sorted(starts)


def test_interface_incidents_monitored_only_semantics(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        device = _device(session, "core-1")
        uplink = _interface(session, device.id, "XGE1/0/1")
        # A second interface with no incidents never appears in the summary.
        _interface(session, device.id, "XGE1/0/2")
        session.add(
            InterfaceStateIncident(
                interface_id=uplink.id,
                started_at=datetime(2026, 9, 1, 3, 0, tzinfo=UTC),
                recovered_at=datetime(2026, 9, 1, 4, 0, tzinfo=UTC),
            )
        )
        # Cross-week ongoing interface outage.
        session.add(
            InterfaceStateIncident(
                interface_id=uplink.id,
                started_at=datetime(2026, 8, 30, tzinfo=UTC),
                recovered_at=None,
            )
        )
        session.commit()

        summaries = weekly_interface_incidents(session, PERIOD)
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.display_name == "XGE1/0/1"
        assert summary.device_name == "core-1"
        assert summary.down_count == 2
        assert len(summary.ongoing_episodes) == 1
        ongoing = summary.ongoing_episodes[0]
        assert ongoing.started_before_period
        assert ongoing.duration_seconds is None


def test_empty_week_has_no_incident_rows(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        _device(session, "core-1")
        session.commit()
        assert weekly_device_incidents(session, PERIOD) == []
        assert weekly_interface_incidents(session, PERIOD) == []
