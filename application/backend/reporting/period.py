"""Report periods and ISO week codes (W03-T001, SYSTEM_SPEC.md §3).

The statistics period is a half-open interval ``[start, end)``:

    start = 上周一 00:00:00 Asia/Shanghai
    end   = 本周一 00:00:00 Asia/Shanghai

Business timezone is fixed to Asia/Shanghai (UTC+8, no DST), so weekday
arithmetic on local wall-clock dates is safe. Week numbering uses the ISO
week-year of the period start (e.g. ``2026-W36``), which is why a period can
carry an ISO year different from both its calendar years around New Year.

All arithmetic accepts and returns timezone-aware datetimes; UTC instants are
converted into the business timezone first so week boundaries are always
local Mondays 00:00:00 whatever the UTC instant.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# §3: the business timezone is fixed. The parameter exists only so tests can
# pin behavior explicitly; production config resolves to the same value.
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")

WEEK = timedelta(days=7)


@dataclass(frozen=True)
class ReportPeriod:
    """One statistics period ``[start, end)`` with its ISO week identity."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("ReportPeriod requires timezone-aware datetimes")
        if self.end <= self.start:
            raise ValueError(f"ReportPeriod end must be after start; got {self.start}..{self.end}")

    @property
    def iso_year(self) -> int:
        """ISO week-year of the period (the year of the ISO week, §3)."""

        return self.start.isocalendar()[0]

    @property
    def iso_week(self) -> int:
        return self.start.isocalendar()[1]

    @property
    def week_code(self) -> str:
        """Report week number, e.g. ``2026-W36`` (§3)."""

        return f"{self.iso_year}-W{self.iso_week:02d}"

    def contains(self, moment: datetime) -> bool:
        """Half-open membership: ``start <= moment < end`` (§3)."""

        return self.start <= moment < self.end

    def overlaps_interval(self, started_at: datetime, recovered_at: datetime | None) -> bool:
        """True when ``[started_at, recovered_at)`` intersects the period.

        Used by the weekly incident summaries: an incident belongs to the week
        when it started before the period end and was still down after the
        period start — including incidents open at either boundary (a
        recovery exactly at ``start`` belongs to the *previous* week's tail,
        a start exactly at ``end`` belongs to the *next* week).
        """

        if started_at >= self.end:
            return False
        return recovered_at is None or recovered_at > self.start


def _local_midnight(moment: datetime, timezone: ZoneInfo) -> datetime:
    """The local midnight (00:00:00) of `moment`'s local date."""

    local = moment.astimezone(timezone)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _monday_on_or_before(moment: datetime, timezone: ZoneInfo) -> datetime:
    local_midnight = _local_midnight(moment, timezone)
    return local_midnight - timedelta(days=local_midnight.weekday())  # Monday == 0


def week_containing(moment: datetime, timezone: ZoneInfo = BUSINESS_TIMEZONE) -> ReportPeriod:
    """The ``[Mon 00:00, next Mon 00:00)`` period containing `moment` (§3)."""

    start = _monday_on_or_before(moment, timezone)
    return ReportPeriod(start=start, end=start + WEEK)


def previous_period(moment: datetime, timezone: ZoneInfo = BUSINESS_TIMEZONE) -> ReportPeriod:
    """The last complete week before `moment` (§3/§4.1).

    At Monday 00:10 this is exactly the week the report must cover; earlier
    in the week it is the week before that (still complete).
    """

    this_week = week_containing(moment, timezone)
    return ReportPeriod(start=this_week.start - WEEK, end=this_week.start)


def period_for_iso_week(
    iso_year: int, iso_week: int, timezone: ZoneInfo = BUSINESS_TIMEZONE
) -> ReportPeriod:
    """The period of one ISO week, by ISO week-year (§3).

    ``period_for_iso_week(2026, 1)`` is ``[2025-12-29, 2026-01-05)`` in
    Asia/Shanghai: the ISO year boundary is handled by the ISO calendar, not
    by calendar-year arithmetic.
    """

    try:
        start_date = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise ValueError(f"invalid ISO week: {iso_year}-W{iso_week:02d}") from exc
    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone)
    return ReportPeriod(start=start, end=start + WEEK)


def is_complete(period: ReportPeriod, moment: datetime) -> bool:
    """True when the whole period lies before `moment` (§4.1)."""

    return period.end <= moment


def report_file_name(period: ReportPeriod) -> str:
    """The suggested long-term DOCX file name (§5)."""

    return f"network-weekly-report-{period.week_code}.docx"
