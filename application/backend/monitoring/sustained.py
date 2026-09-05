"""Sustained-high detection over persisted metric series (W02-T006/T007).

The shared semantics for §14 (CPU / memory), §15.3 (priority-interface
utilization) and the §15.4 candidate series: a sustained-high interval is a
run of consecutive *valid* samples all at or above the threshold, with

- a missing sample (None) breaking the run (§14: 缺失样本会中断连续区间，
  不得人为补齐) — unlike the §13 interface Down counting, which follows the
  valid-sample wording of §13.3, thresholds are explicitly continuity-
  breaking per §14;
- the default requirement of 3 consecutive 5-minute samples (>= 80% for
  15 minutes); thresholds and sample counts are configurable (W02-T007).

The detection is a pure function over `(timestamp, value | None)` points, so
weekly statistics (Wave 3) reuse exactly this implementation for report
values and for pages — one statistical implementation everywhere (§15.4).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SustainedHighInterval:
    """One confirmed sustained-high run (§14: start, end, duration, count)."""

    start: datetime
    end: datetime
    sample_count: int

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def find_sustained_high_intervals(
    points: Sequence[tuple[datetime, float | None]],
    threshold_percent: float,
    *,
    required_samples: int = 3,
) -> list[SustainedHighInterval]:
    """Detect runs of `required_samples`+ consecutive valid samples >= threshold.

    `points` must be in chronological order (the series loaders guarantee
    it). Missing (None) values break the run; values exactly AT the threshold
    count (§14/§15.3: ">= 80%").
    """

    intervals: list[SustainedHighInterval] = []
    run_start: datetime | None = None
    run_end: datetime | None = None
    run_length = 0

    for timestamp, value in points:
        if value is not None and value >= threshold_percent:
            if run_length == 0:
                run_start = timestamp
            run_end = timestamp
            run_length += 1
            if run_length == required_samples:
                assert run_start is not None and run_end is not None
                intervals.append(
                    SustainedHighInterval(
                        start=run_start, end=run_end, sample_count=run_length
                    )
                )
            elif run_length > required_samples:
                # Extend the just-emitted interval instead of emitting anew.
                previous = intervals[-1]
                intervals[-1] = SustainedHighInterval(
                    start=previous.start, end=run_end, sample_count=run_length
                )
        else:
            run_length = 0
            run_start = None
            run_end = None

    return intervals


def max_direction_utilization(
    in_utilization_percent: float | None, out_utilization_percent: float | None
) -> float | None:
    """The §15.4 per-sample unified value: max(ingress, egress); None if both."""

    if in_utilization_percent is None:
        return out_utilization_percent
    if out_utilization_percent is None:
        return in_utilization_percent
    return max(in_utilization_percent, out_utilization_percent)
