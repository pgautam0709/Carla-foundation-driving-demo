"""
src/data/dataset_schemas.py — Data schema definitions for Phase 3 dataset engineering.

All records are plain dataclasses that serialize to JSON-compatible dicts via
:func:`dataclasses.asdict`.  ``DATASET_SCHEMA_VERSION`` is embedded in every
artifact produced by :mod:`src.data.dataset_builder` so downstream tooling
(the future PyTorch ``Dataset``, not implemented in this phase) can detect
format changes independently of the Phase 2 episode schema version.

Schema version history:
    1.0 — Phase 3 initial: episodes_index.jsonl, samples_index.jsonl,
           dataset_manifest.json, quality_report.json.
"""

from __future__ import annotations

import dataclasses
from typing import Any

# ── Schema version ─────────────────────────────────────────────────────────────
DATASET_SCHEMA_VERSION: str = "1.0"


# ── Per-episode index entry (written to episodes_index.jsonl) ──────────────────

@dataclasses.dataclass
class EpisodeIndexEntry:
    """One row in episodes_index.jsonl — a single discovered Phase 2 episode.

    Args:
        episode_id: Episode identifier (directory basename).
        episode_dir: Path to the episode directory, relative to the repo root.
        town: CARLA map name, or None if metadata.json could not be read.
        route_name: Route label, or None if metadata.json could not be read.
        collection_mode: ``"live"`` or ``"dry_run"``, or None if unknown.
        created_at: ISO 8601 creation timestamp from metadata.json, or None.
        frame_count: Number of front-camera frames on disk.
        control_row_count: Number of rows in controls.jsonl.
        telemetry_row_count: Number of rows in telemetry.jsonl.
        valid: Result of :class:`~src.data.validation.EpisodeValidator`.
        validation_errors: Validator error messages (empty when valid).
        aligned: True if frame/control/telemetry counts and tick numbering
            agree exactly, with no truncation required.
        alignment_issues: Human-readable alignment discrepancies.
        usable_tick_count: The longest shared contiguous ``0..N-1`` prefix
            across frames, controls, and telemetry — the number of ticks
            usable as samples. Equal to ``frame_count`` /
            ``control_row_count`` / ``telemetry_row_count`` when ``aligned``
            is True.
        included: True if this episode contributed samples to the dataset.
        exclusion_reason: Why the episode was excluded, or None if included.
        truncated: True if this episode was included despite ``aligned`` is
            False — i.e. some trailing ticks were dropped because
            ``allow_partial_alignment`` was enabled. Always False when
            ``aligned`` is True or when the episode was excluded.
        split: Assigned split (``"train"`` | ``"val"`` | ``"test"``), or None
            if excluded.
    """

    episode_id: str
    episode_dir: str
    town: str | None
    route_name: str | None
    collection_mode: str | None
    created_at: str | None
    frame_count: int
    control_row_count: int
    telemetry_row_count: int
    valid: bool
    validation_errors: list[str]
    aligned: bool
    alignment_issues: list[str]
    usable_tick_count: int
    included: bool
    exclusion_reason: str | None
    truncated: bool
    split: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


# ── Per-sample record (written to samples_index.jsonl) ─────────────────────────

@dataclasses.dataclass
class SampleRecord:
    """One row in samples_index.jsonl — a single aligned (frame, control) tick.

    Args:
        sample_id: Unique identifier: ``"{episode_id}_{tick:06d}"``.
        episode_id: Parent episode identifier.
        tick: Zero-based tick index within the episode.
        frame_path: Path to the front-camera PNG, relative to the repo root.
        throttle: [0, 1] throttle input at this tick.
        brake: [0, 1] brake input at this tick.
        steer: [-1, 1] steering input at this tick.
        speed_kph: Vehicle speed in km/h at this tick.
        split: Assigned split (``"train"`` | ``"val"`` | ``"test"``).
    """

    sample_id: str
    episode_id: str
    tick: int
    frame_path: str
    throttle: float
    brake: float
    steer: float
    speed_kph: float
    split: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


# ── Aggregate statistics ────────────────────────────────────────────────────────

@dataclasses.dataclass
class SplitCounts:
    """Sample counts per split.

    Args:
        train: Number of samples assigned to the train split.
        val: Number of samples assigned to the val split.
        test: Number of samples assigned to the test split.
    """

    train: int = 0
    val: int = 0
    test: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class ValueStats:
    """Summary statistics for a single numeric signal.

    Args:
        mean: Arithmetic mean.
        std: Population standard deviation (0.0 for a single sample).
        min: Minimum observed value.
        max: Maximum observed value.
    """

    mean: float
    std: float
    min: float
    max: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class HistogramBin:
    """One bin of a fixed-width histogram over a signal's value range.

    Args:
        range_min: Inclusive lower bound of the bin.
        range_max: Exclusive upper bound of the bin (inclusive for the last
            bin, to capture values exactly at the signal's maximum).
        count: Number of samples whose value falls in this bin.
    """

    range_min: float
    range_max: float
    count: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class DatasetStatistics:
    """Aggregate statistics over all included samples and episodes.

    Args:
        episode_count: Number of episodes included in the dataset.
        sample_count: Number of samples included in the dataset.
        split_counts: Sample counts per split.
        towns: Mapping of town name to number of included episodes.
        throttle: Summary statistics for the throttle signal, or None if
            ``sample_count`` is 0.
        brake: Summary statistics for the brake signal, or None.
        steer: Summary statistics for the steer signal, or None.
        speed_kph: Summary statistics for the speed_kph signal, or None.
        steering_histogram: Fixed-width histogram of the steer signal over
            ``[-1.0, 1.0]``, informational only — it does not drive any
            resampling or class-balancing in this phase. Empty when
            ``sample_count`` is 0.
    """

    episode_count: int
    sample_count: int
    split_counts: SplitCounts
    towns: dict[str, int]
    throttle: ValueStats | None
    brake: ValueStats | None
    steer: ValueStats | None
    speed_kph: ValueStats | None
    steering_histogram: list[HistogramBin]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (nested dataclasses expanded)."""
        return dataclasses.asdict(self)


# ── Quality report ───────────────────────────────────────────────────────────────

@dataclasses.dataclass
class QualityIssue:
    """A single quality issue attributed to one episode.

    Args:
        episode_id: The episode the issue concerns, or the sentinel
            ``"<dataset>"`` for issues that apply to the whole build rather
            than a single episode (e.g. a split with zero samples).
        severity: ``"error"`` (episode excluded) or ``"warning"`` (episode
            included with a caveat, or a build-level observation).
        message: Human-readable description of the issue.
    """

    episode_id: str
    severity: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class QualityReport:
    """Full quality report written to quality_report.json.

    Args:
        schema_version: Always :data:`DATASET_SCHEMA_VERSION`.
        created_at: ISO 8601 UTC timestamp when the report was generated.
        episodes_scanned: Total episode directories discovered.
        episodes_valid: Number that passed :class:`EpisodeValidator`.
        episodes_invalid: Number that failed :class:`EpisodeValidator`.
        episodes_included: Number that contributed samples to the dataset.
        episodes_excluded: Number excluded (invalid, misaligned, or too short).
        episodes_misaligned: Number whose frame/control/telemetry counts or
            tick numbering did not agree exactly (``aligned`` is False),
            regardless of whether they were ultimately included or excluded.
        episodes_truncated: Number included despite being misaligned — i.e.
            samples were generated only for the shared usable prefix,
            because ``allow_partial_alignment`` was enabled.
        episodes_with_outliers: Number of episodes with at least one
            steering-spike or stuck-throttle finding (see
            :mod:`src.data.dataset_outliers`). Purely informational — never
            affects inclusion.
        duplicate_frame_groups: Number of distinct sets of samples (within
            one episode or across episodes) that are exact duplicates —
            their frame files are byte-for-byte identical (see
            :mod:`src.data.dataset_duplicates`). This is exact-match
            detection only; it does not detect perceptually similar but
            non-identical ("near-duplicate") frames. Purely informational
            — never affects inclusion.
        issues: Ordered list of all :class:`QualityIssue` records.
    """

    schema_version: str
    created_at: str
    episodes_scanned: int
    episodes_valid: int
    episodes_invalid: int
    episodes_included: int
    episodes_excluded: int
    episodes_misaligned: int
    episodes_truncated: int
    episodes_with_outliers: int
    duplicate_frame_groups: int
    issues: list[QualityIssue]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (issues expanded to list of dicts)."""
        d = dataclasses.asdict(self)
        return d


# ── Dataset manifest ──────────────────────────────────────────────────────────────

@dataclasses.dataclass
class DatasetManifest:
    """Top-level summary written to dataset_manifest.json after a build.

    Args:
        schema_version: Always :data:`DATASET_SCHEMA_VERSION`.
        created_at: ISO 8601 UTC timestamp when the dataset was built.
        git_commit: Short git HEAD hash, or None if not in a git repo.
        dataset_id: Identifier for this build, e.g. ``dataset_20260708_030000``.
            Defaults to ``output_dir``'s final path component when not given
            explicitly, so every build is independently identifiable even if
            its directory is later moved or copied.
        raw_episodes_dir: Source directory that was scanned, as given.
        output_dir: Directory the index/manifest/report files were written to.
        episode_count_discovered: Total episode directories found.
        episode_count_included: Episodes that contributed samples.
        episode_count_excluded: Episodes dropped (invalid/misaligned/too short).
        sample_count: Total samples across all included episodes.
        split_ratios: The requested train/val/test ratios.
        split_seed: Seed used for deterministic split assignment.
        allow_partial_alignment: Whether misaligned episodes were truncated
            and included rather than excluded outright.
        outlier_detection_enabled: Whether steering-spike/stuck-throttle
            detection (:mod:`src.data.dataset_outliers`) ran for this build.
        outlier_thresholds: The thresholds used for outlier detection, or
            None if ``outlier_detection_enabled`` is False.
        duplicate_detection_enabled: Whether exact duplicate-frame detection
            (:mod:`src.data.dataset_duplicates`) ran for this build.
        episodes_index_path: Filename of the episode index (relative to
            ``output_dir``).
        samples_index_path: Filename of the sample index (relative to
            ``output_dir``).
        quality_report_path: Filename of the quality report (relative to
            ``output_dir``).
        statistics_path: Filename of the aggregate statistics file (relative
            to ``output_dir``).
        splits_dir: Directory name (relative to ``output_dir``) containing
            one JSONL file per split — a pre-filtered view of
            ``samples_index.jsonl``.
        split_index_paths: Mapping of split name to its JSONL file path,
            relative to ``output_dir`` (e.g. ``{"train": "splits/train.jsonl"}``).
    """

    schema_version: str
    created_at: str
    git_commit: str | None
    dataset_id: str
    raw_episodes_dir: str
    output_dir: str
    episode_count_discovered: int
    episode_count_included: int
    episode_count_excluded: int
    sample_count: int
    split_ratios: dict[str, float]
    split_seed: int
    allow_partial_alignment: bool
    outlier_detection_enabled: bool
    outlier_thresholds: dict[str, float] | None
    duplicate_detection_enabled: bool
    episodes_index_path: str
    samples_index_path: str
    quality_report_path: str
    statistics_path: str
    splits_dir: str
    split_index_paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)
