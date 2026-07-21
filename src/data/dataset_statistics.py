"""
src/data/dataset_statistics.py — Aggregate statistics over a built dataset.

Computes per-signal summary statistics (throttle, brake, steer, speed),
episode/sample counts by split and by town, and a steering-angle histogram
(Phase 3b hardening — informational only, does not drive sampling). Pure
function of the episode index and sample index produced by
:mod:`src.data.dataset_builder` — no file I/O here.
"""

from __future__ import annotations

import statistics

from src.data.dataset_schemas import (
    DatasetStatistics,
    EpisodeIndexEntry,
    HistogramBin,
    SampleRecord,
    SplitCounts,
    ValueStats,
)

#: Steering values are defined to lie in [-1, 1] (see ControlRecord.steer).
_STEER_RANGE_MIN = -1.0
_STEER_RANGE_MAX = 1.0


def compute_statistics(
    episodes: list[EpisodeIndexEntry],
    samples: list[SampleRecord],
    steering_histogram_bins: int = 10,
) -> DatasetStatistics:
    """Compute aggregate statistics for a set of episodes and samples.

    Args:
        episodes: Full episode index (both included and excluded entries).
        samples: Full sample index (only samples from included episodes).
        steering_histogram_bins: Number of equal-width bins to divide
            ``[-1.0, 1.0]`` into for the steering histogram.

    Returns:
        A :class:`~src.data.dataset_schemas.DatasetStatistics` summarising
        the dataset. Per-signal stats are None when *samples* is empty; the
        steering histogram is an empty list in that case.
    """
    included = [e for e in episodes if e.included]

    towns: dict[str, int] = {}
    weather: dict[str, int] = {}
    for episode in included:
        if episode.town is not None:
            towns[episode.town] = towns.get(episode.town, 0) + 1
        if episode.weather is not None:
            weather[episode.weather] = weather.get(episode.weather, 0) + 1

    split_counts = SplitCounts()
    for sample in samples:
        if sample.split == "train":
            split_counts.train += 1
        elif sample.split == "val":
            split_counts.val += 1
        elif sample.split == "test":
            split_counts.test += 1

    return DatasetStatistics(
        episode_count=len(included),
        sample_count=len(samples),
        split_counts=split_counts,
        towns=towns,
        weather=weather,
        throttle=_value_stats([s.throttle for s in samples]),
        brake=_value_stats([s.brake for s in samples]),
        steer=_value_stats([s.steer for s in samples]),
        speed_kph=_value_stats([s.speed_kph for s in samples]),
        steering_histogram=_histogram(
            [s.steer for s in samples], steering_histogram_bins,
            _STEER_RANGE_MIN, _STEER_RANGE_MAX,
        ),
    )


def _value_stats(values: list[float]) -> ValueStats | None:
    """Compute mean/std/min/max for a list of values.

    Args:
        values: Sample values for one signal, across all included samples.

    Returns:
        A :class:`~src.data.dataset_schemas.ValueStats`, or None if *values*
        is empty.
    """
    if not values:
        return None
    return ValueStats(
        mean=statistics.fmean(values),
        std=statistics.pstdev(values),
        min=min(values),
        max=max(values),
    )


def _histogram(
    values: list[float],
    bins: int,
    range_min: float,
    range_max: float,
) -> list[HistogramBin]:
    """Compute a fixed-width histogram of *values* over ``[range_min, range_max]``.

    Values outside the range are clamped into the first/last bin — this
    only matters for float rounding at the exact boundary (e.g. a steer
    value of precisely ``1.0``), since steering is otherwise defined to
    stay within range.

    Args:
        values: Values to bin.
        bins: Number of equal-width bins.
        range_min: Inclusive lower bound of the first bin.
        range_max: Inclusive upper bound of the last bin.

    Returns:
        List of *bins* :class:`~src.data.dataset_schemas.HistogramBin`
        objects in ascending range order, or an empty list if *values* is
        empty.
    """
    if not values:
        return []
    width = (range_max - range_min) / bins
    counts = [0] * bins
    for value in values:
        index = int((value - range_min) / width) if width > 0 else 0
        index = max(0, min(bins - 1, index))
        counts[index] += 1
    return [
        HistogramBin(
            range_min=range_min + i * width,
            range_max=range_min + (i + 1) * width,
            count=counts[i],
        )
        for i in range(bins)
    ]
