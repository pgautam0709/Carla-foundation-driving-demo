"""
src/data/dataset_statistics.py — Aggregate statistics over a built dataset.

Computes per-signal summary statistics (throttle, brake, steer, speed) and
episode/sample counts by split and by town. Pure function of the episode
index and sample index produced by :mod:`src.data.dataset_builder` — no
file I/O here.
"""

from __future__ import annotations

import statistics

from src.data.dataset_schemas import (
    DatasetStatistics,
    EpisodeIndexEntry,
    SampleRecord,
    SplitCounts,
    ValueStats,
)


def compute_statistics(
    episodes: list[EpisodeIndexEntry],
    samples: list[SampleRecord],
) -> DatasetStatistics:
    """Compute aggregate statistics for a set of episodes and samples.

    Args:
        episodes: Full episode index (both included and excluded entries).
        samples: Full sample index (only samples from included episodes).

    Returns:
        A :class:`~src.data.dataset_schemas.DatasetStatistics` summarising
        the dataset. Per-signal stats are None when *samples* is empty.
    """
    included = [e for e in episodes if e.included]

    towns: dict[str, int] = {}
    for episode in included:
        if episode.town is not None:
            towns[episode.town] = towns.get(episode.town, 0) + 1

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
        throttle=_value_stats([s.throttle for s in samples]),
        brake=_value_stats([s.brake for s in samples]),
        steer=_value_stats([s.steer for s in samples]),
        speed_kph=_value_stats([s.speed_kph for s in samples]),
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
