"""Interface utilization from counter deltas with rebaseline semantics
(W02-T003, SYSTEM_SPEC.md §15).

Utilization per direction:

    delta_octets * 8 / (elapsed_seconds * speed_bps) * 100

using the *actual* elapsed time between two valid samples and the interface's
effective speed. The computation is deliberately conservative — anything that
cannot be computed reliably rebaselines the interface instead of producing a
number, so a counter reset, a stale gap or a bad speed can never surface as a
fake utilization spike (§15.2):

- no previous valid sample (first observation, discovery gap, previous
  counters missing) — rebaseline;
- negative delta (counter reset, or a 64-bit wrap the system cannot rule
  out) — rebaseline;
- speed missing, non-positive, or changed since the previous sample (the
  interval then has no single effective speed) — rebaseline;
- interval non-positive or longer than MAX_SAMPLE_INTERVAL_SECONDS (a stale
  gap averaged into one sample would dilute and mislead) — rebaseline;
- computed utilization above 100% (physically impossible; indicates broken
  counters) — rebaseline.

A rebaselined sample still establishes the baseline for the next interval:
its counters are valid, it just yields no utilization value (§15.2).
"""

from dataclasses import dataclass
from datetime import datetime

# A utilization interval longer than three poll cycles is a stale gap.
MAX_SAMPLE_INTERVAL_SECONDS = 900.0


@dataclass(frozen=True)
class PreviousSample:
    """The counter baseline: the interface's latest stored sample."""

    collected_at: datetime
    in_octets: int | None
    out_octets: int | None
    speed_bps: int | None


@dataclass(frozen=True)
class UtilizationResult:
    """Per-direction utilization for one sample, or a rebaseline marker.

    Directions are independent: one may compute while the other rebaselines.
    `rebaselined` is True when at least one direction could not produce a
    utilization value.
    """

    in_utilization_percent: float | None
    out_utilization_percent: float | None
    elapsed_seconds: float | None
    rebaselined: bool


def _direction_utilization(
    prev_octets: int | None,
    cur_octets: int | None,
    elapsed_seconds: float,
    speed_bps: int,
) -> float | None:
    """One direction's utilization, or None when it must rebaseline."""

    if prev_octets is None or cur_octets is None:
        return None
    delta = cur_octets - prev_octets
    if delta < 0:
        # Counter reset or an unreliable wrap: never fabricate a spike (§15.2).
        return None
    utilization = delta * 8 / (elapsed_seconds * speed_bps) * 100
    if utilization > 100:
        # Physically impossible — broken counters rather than real traffic.
        return None
    return utilization


def compute_utilization(
    previous: PreviousSample | None,
    now: datetime,
    cur_in_octets: int | None,
    cur_out_octets: int | None,
    cur_speed_bps: int | None,
) -> UtilizationResult:
    """Compute one sample's utilization against the previous valid sample."""

    if (
        previous is None
        or cur_speed_bps is None
        or cur_speed_bps <= 0
        or previous.speed_bps != cur_speed_bps
    ):
        return UtilizationResult(
            in_utilization_percent=None,
            out_utilization_percent=None,
            elapsed_seconds=None,
            rebaselined=True,
        )

    elapsed = (now - previous.collected_at).total_seconds()
    if elapsed <= 0 or elapsed > MAX_SAMPLE_INTERVAL_SECONDS:
        return UtilizationResult(
            in_utilization_percent=None,
            out_utilization_percent=None,
            elapsed_seconds=None,
            rebaselined=True,
        )

    in_utilization = _direction_utilization(
        previous.in_octets, cur_in_octets, elapsed, cur_speed_bps
    )
    out_utilization = _direction_utilization(
        previous.out_octets, cur_out_octets, elapsed, cur_speed_bps
    )
    return UtilizationResult(
        in_utilization_percent=in_utilization,
        out_utilization_percent=out_utilization,
        elapsed_seconds=elapsed,
        rebaselined=in_utilization is None or out_utilization is None,
    )
