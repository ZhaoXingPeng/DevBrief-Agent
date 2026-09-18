from __future__ import annotations

from collections.abc import Sequence
from math import ceil, isfinite

from devbrief.domain.contracts import BenchmarkLatencySummary


def nearest_rank_percentile(samples: Sequence[float], *, percentile: float) -> float:
    """Return a non-negative percentile using the documented nearest-rank rule."""
    values = _normalized_samples(samples)
    if not isfinite(percentile) or not 0 < percentile <= 1:
        raise ValueError("percentile must be finite and within (0, 1]")
    rank = ceil(percentile * len(values))
    return sorted(values)[rank - 1]


def summarize_latencies(samples: Sequence[float]) -> BenchmarkLatencySummary:
    """Build an ordered p50/p95 latency summary without retaining raw samples."""
    values = _normalized_samples(samples)
    ordered = sorted(values)
    return BenchmarkLatencySummary(
        sample_count=len(ordered),
        min_ms=ordered[0],
        p50_ms=nearest_rank_percentile(ordered, percentile=0.5),
        p95_ms=nearest_rank_percentile(ordered, percentile=0.95),
        max_ms=ordered[-1],
    )


def _normalized_samples(samples: Sequence[float]) -> tuple[float, ...]:
    if not samples:
        raise ValueError("benchmark samples are required")
    values = tuple(float(value) for value in samples)
    if any(not isfinite(value) or value < 0 for value in values):
        raise ValueError("benchmark samples must be finite and non-negative")
    return values
