"""Periodic IRF member observation (W02-T008, SYSTEM_SPEC.md §7.3/§17).

Roughly every 15 minutes, each device *configured* as an IRF fabric
(`expected_irf_member_count > 1`) is observed once over the Wave 1 SSH
collector (:func:`backend.collect.session.run_irf_observation`, re-used
unchanged). Standalone devices are never SSH-probed for IRF — there is
nothing to observe (§17).

Every *successful* observation persists one row per known member
(`irf_member_observations`): present members with their reported role,
absent members as `observed=false`. Role changes are recorded only when
both the stored and the newly reported role are known — never guessed.
A FAILED observation records nothing: "missing" must always mean "the
device itself reported the member absent", never "we could not look".

Even when the logical management IP stays reachable, member absence is
visible independently (§17) — these rows are what the Wave 3 weekly IRF
summary reads.
"""

import logging
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.collect.dto import IrfMemberSample
from backend.collect.session import run_irf_observation
from backend.collect.ssh import SshConfig
from backend.db.engine import get_session_factory
from backend.db.models import DeviceMember, IrfMemberObservation

logger = logging.getLogger(__name__)

# §7.3: IRF member state refresh ~every 15 minutes.
IRF_OBSERVATION_INTERVAL = timedelta(seconds=900)


@dataclass(frozen=True)
class IrfDeviceContext:
    """Device identity + SSH config for one IRF observation."""

    device_id: int
    device_name: str
    ssh: SshConfig | None = None


@dataclass(frozen=True)
class IrfObservationReport:
    """What one observation changed; member ids only (safe to log)."""

    device_id: int
    observed_members: tuple[int, ...]
    missing_members: tuple[int, ...]
    # (member_id, previous_role, observed_role) — reliably identified only.
    role_changes: tuple[tuple[int, str, str], ...] = ()
    # Members back after their previous observation recorded them missing.
    reappeared_members: tuple[int, ...] = ()


def _previous_observed_flags(
    session: Session, device_id: int, member_ids: set[int]
) -> dict[int, bool]:
    """Latest observation flag per member (for reappearance detection)."""

    if not member_ids:
        return {}
    rows = (
        session.execute(
            select(IrfMemberObservation)
            .where(
                IrfMemberObservation.device_id == device_id,
                IrfMemberObservation.member_id.in_(member_ids),
            )
            .distinct(IrfMemberObservation.member_id)
            .order_by(
                IrfMemberObservation.member_id,
                IrfMemberObservation.observed_at.desc(),
                IrfMemberObservation.id.desc(),
            )
        )
        .scalars()
        .all()
    )
    return {row.member_id: row.observed for row in rows}


def apply_irf_observation(
    session: Session,
    device_id: int,
    samples: list[IrfMemberSample],
    observed_at: datetime,
) -> IrfObservationReport:
    """Persist one successful observation; report missing/reappeared/changes."""

    known: dict[int, DeviceMember] = {
        member.member_id: member
        for member in session.execute(
            select(DeviceMember).where(DeviceMember.device_id == device_id)
        )
        .scalars()
        .all()
    }
    sample_by_id = {sample.member_id: sample for sample in samples}
    previous_flags = _previous_observed_flags(session, device_id, set(sample_by_id))

    observed: list[int] = []
    missing: list[int] = []
    role_changes: list[tuple[int, str, str]] = []
    reappeared: list[int] = []

    for member_id in sorted(set(known) | set(sample_by_id)):
        sample = sample_by_id.get(member_id)
        present = sample is not None
        member = known.get(member_id)

        role = sample.role if sample is not None else None
        previous_role = member.role if member is not None else None
        role_changed = bool(
            present
            and role is not None
            and previous_role is not None
            and role != previous_role
        )
        member_reappeared = bool(present and previous_flags.get(member_id) is False)

        session.add(
            IrfMemberObservation(
                device_id=device_id,
                member_id=member_id,
                observed=present,
                role=role,
                previous_role=previous_role if role_changed else None,
                role_changed=role_changed,
                observed_at=observed_at,
            )
        )

        if present and member is None:
            member = DeviceMember(device_id=device_id, member_id=member_id)
            session.add(member)
            known[member_id] = member
        if present and member is not None and sample is not None:
            # Reliable data only: a None role never erases a stored one (§17).
            if role is not None:
                member.role = role
            if sample.model is not None:
                member.model = sample.model
            if sample.software_version is not None:
                member.software_version = sample.software_version
            member.last_seen_at = observed_at

        if present:
            observed.append(member_id)
            if member_reappeared:
                reappeared.append(member_id)
            if role_changed and role is not None and previous_role is not None:
                role_changes.append((member_id, previous_role, role))
        else:
            missing.append(member_id)

    report = IrfObservationReport(
        device_id=device_id,
        observed_members=tuple(observed),
        missing_members=tuple(missing),
        role_changes=tuple(role_changes),
        reappeared_members=tuple(reappeared),
    )
    if report.missing_members or report.role_changes or report.reappeared_members:
        logger.warning(
            "irf observation device %d: missing=%s reappeared=%s role_changes=%s",
            device_id,
            list(report.missing_members),
            list(report.reappeared_members),
            [(m, p, n) for m, p, n in report.role_changes],
        )
    else:
        logger.info(
            "irf observation device %d: members %s all present", device_id, list(observed)
        )
    return report


def observe_irf_device(
    context: IrfDeviceContext,
    *,
    session_factory: sessionmaker | None = None,
    now: datetime | None = None,
) -> IrfObservationReport | None:
    """One observation of one IRF device; None when the SSH path failed.

    A failed observation is deliberately invisible in the history: missing
    members are only ever recorded from a successful observation.
    """

    if context.ssh is None:
        logger.warning(
            "irf observation skipped for %s: no SSH credentials", context.device_name
        )
        return None

    at = now if now is not None else datetime.now(UTC)
    samples = run_irf_observation(context.ssh, context.device_name)
    if samples is None:
        logger.warning(
            "irf observation for %s failed; no observation recorded", context.device_name
        )
        return None

    factory = session_factory if session_factory is not None else get_session_factory()
    with factory() as session:
        report = apply_irf_observation(session, context.device_id, samples, at)
        session.commit()
    return report


class IrfObservationLoop:
    """Runs one IRF observation pass per ~15-minute boundary until stopped.

    Passes run inline (single thread): a pass that overruns the interval
    simply delays the next one — the cadence stays approximate (§7.3), and
    an IRF fabric is never observed concurrently with itself.
    """

    def __init__(
        self,
        load_devices: Callable[[], list[IrfDeviceContext]],
        *,
        interval: timedelta = IRF_OBSERVATION_INTERVAL,
        clock: Callable[[], datetime] | None = None,
        sleep_until: Callable[[datetime], bool] | None = None,
        observe: Callable[[IrfDeviceContext], IrfObservationReport | None] | None = None,
    ) -> None:
        self._load_devices = load_devices
        self._interval = interval
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep_until = sleep_until
        self._observe = observe if observe is not None else observe_irf_device
        self._running = threading.Lock()

    def run_pass(self) -> int:
        """Observe every configured IRF device once; returns devices seen."""

        devices = self._load_devices()
        for context in devices:
            try:
                self._observe(context)
            except Exception as exc:  # noqa: BLE001  (contained per device)
                logger.error(
                    "irf observation for %s failed (%s)",
                    context.device_name,
                    type(exc).__name__,
                )
        return len(devices)

    def _wait_until(self, target: datetime, stop: threading.Event) -> bool:
        """Sleep until `target`; return False when a stop was requested."""

        if self._sleep_until is not None:
            return self._sleep_until(target)
        remaining = (target - self._clock()).total_seconds()
        return not stop.wait(timeout=max(0.0, remaining))

    def run_loop(self, stop: threading.Event) -> None:
        logger.info(
            "irf observation loop started (interval %d s)",
            int(self._interval.total_seconds()),
        )
        last_target: datetime | None = None
        while not stop.is_set():
            target = _next_boundary(self._clock(), self._interval)
            if last_target is not None and target <= last_target:
                target += self._interval
            if not self._wait_until(target, stop):
                break
            last_target = target
            if not self._running.acquire(blocking=False):
                continue  # previous pass still active: skip this slot
            try:
                self.run_pass()
            finally:
                self._running.release()
        logger.info("irf observation loop stopped")


def _next_boundary(now: datetime, interval: timedelta) -> datetime:
    """The interval boundary at or after `now` (UTC, epoch-aligned)."""

    seconds = int(interval.total_seconds())
    epoch = datetime(1970, 1, 1, tzinfo=now.tzinfo)
    steps = math.ceil((now - epoch).total_seconds() / seconds)
    return epoch + timedelta(seconds=steps * seconds)
