"""
src/data/dataset_builder.py — Phase 3 dataset engineering orchestrator.

Turns a directory of Phase 2 episodes into a flat, indexed dataset:

    1. Discover episode directories (:mod:`src.data.dataset_discovery`).
    2. Validate each episode (:class:`~src.data.validation.EpisodeValidator`).
    3. Check frame/control/telemetry alignment (:mod:`src.data.dataset_alignment`).
       By default, misaligned episodes are **excluded** — pass
       ``allow_partial_alignment=True`` to include them truncated to their
       usable prefix instead.
    4. Assign a deterministic train/val/test split per included episode, as
       a batch so small episode counts still cover every configured split
       (:mod:`src.data.dataset_splits`).
    5. Emit one :class:`~src.data.dataset_schemas.SampleRecord` per usable
       tick of every included episode.
    6. Compute aggregate statistics (:mod:`src.data.dataset_statistics`).
    7. Write ``dataset_manifest.json``, ``episodes_index.jsonl``,
       ``samples_index.jsonl``, ``stats.json``, ``quality_report.json``, and
       ``splits/<name>.jsonl`` (one per configured split) to the output
       directory.

No CARLA, GPU, or ML framework dependency — this module only reads the flat
files Phase 2 already writes to disk.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.data.dataset_alignment import AlignmentResult, check_alignment
from src.data.dataset_discovery import discover_episodes
from src.data.dataset_schemas import (
    DATASET_SCHEMA_VERSION,
    DatasetManifest,
    EpisodeIndexEntry,
    QualityIssue,
    QualityReport,
    SampleRecord,
    SplitCounts,
)
from src.data.dataset_splits import assign_splits
from src.data.dataset_statistics import compute_statistics
from src.data.episode import get_git_commit
from src.data.validation import EpisodeValidator, ValidationResult
from src.utils.logging import get_logger

log = get_logger(__name__)

#: Filenames written into the dataset engineering output directory.
DATASET_MANIFEST_FILENAME = "dataset_manifest.json"
EPISODES_INDEX_FILENAME = "episodes_index.jsonl"
SAMPLES_INDEX_FILENAME = "samples_index.jsonl"
QUALITY_REPORT_FILENAME = "quality_report.json"
STATS_FILENAME = "stats.json"
SPLITS_DIRNAME = "splits"

#: Sentinel episode_id for quality issues that apply to the whole build.
DATASET_LEVEL_ISSUE = "<dataset>"


@dataclasses.dataclass
class _PendingEpisode:
    """Intermediate per-episode state between the discovery and split passes."""

    episode_dir: Path
    episode_id: str
    meta: dict[str, Any]
    validation: ValidationResult
    alignment: AlignmentResult
    included: bool
    truncated: bool
    exclusion_reason: str | None


def build_dataset(
    *,
    raw_episodes_dir: Path,
    output_dir: Path,
    split_ratios: dict[str, float],
    split_seed: int,
    dataset_id: str | None = None,
    min_episode_ticks: int = 1,
    require_valid: bool = True,
    allow_partial_alignment: bool = False,
) -> DatasetManifest:
    """Build the Phase 3 dataset index from Phase 2 episodes.

    Args:
        raw_episodes_dir: Directory containing one subdirectory per Phase 2
            episode (typically ``data/raw/episodes``).
        output_dir: Directory to write all dataset artifacts into. Created
            if it does not exist. Callers building versioned datasets
            typically pass ``datasets_dir/<dataset_id>``.
        split_ratios: Relative train/val/test weights, e.g.
            ``{"train": 0.8, "val": 0.1, "test": 0.1}``.
        split_seed: Seed for deterministic split assignment (see
            :func:`~src.data.dataset_splits.assign_splits`).
        dataset_id: Identifier recorded in the manifest for this build.
            Defaults to ``output_dir``'s final path component when not
            given — e.g. building into ``data/processed/datasets/ds_1``
            records ``dataset_id="ds_1"`` with no extra argument needed.
        min_episode_ticks: Episodes with fewer usable ticks than this are
            excluded from the dataset.
        require_valid: If True, episodes that fail
            :class:`~src.data.validation.EpisodeValidator` are excluded.
            If False, they are still indexed (and their samples included
            when alignment allows), only flagged in the quality report.
        allow_partial_alignment: If False (default), episodes whose
            frame/control/telemetry counts or tick numbering disagree
            (``aligned`` is False) are excluded — strict alignment is the
            default. If True, such episodes are included with samples
            truncated to their usable prefix, and the truncation is
            recorded in the quality report.

    Returns:
        The :class:`~src.data.dataset_schemas.DatasetManifest` describing
        the build. The same data is written to *output_dir*.
    """
    raw_episodes_dir = Path(raw_episodes_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_dataset_id = dataset_id or output_dir.name

    log.info("dataset_builder.start", raw_episodes_dir=str(raw_episodes_dir),
             dataset_id=resolved_dataset_id)

    validator = EpisodeValidator()
    issues: list[QualityIssue] = []
    pending: list[_PendingEpisode] = []

    for episode_dir in discover_episodes(raw_episodes_dir):
        episode_id = episode_dir.name
        validation = validator.validate(episode_dir)
        alignment = check_alignment(episode_dir)
        meta = _read_metadata(episode_dir)

        for error in validation.errors:
            issues.append(QualityIssue(episode_id=episode_id, severity="error", message=error))

        exclusion_reason: str | None = None
        if require_valid and not validation.valid:
            exclusion_reason = f"failed validation ({len(validation.errors)} error(s))"
        elif not alignment.aligned and not allow_partial_alignment:
            exclusion_reason = (
                "misaligned (frame/control/telemetry counts or tick numbering disagree)"
                " and allow_partial_alignment is disabled"
            )
        elif alignment.usable_tick_count < min_episode_ticks:
            exclusion_reason = (
                f"usable_tick_count ({alignment.usable_tick_count})"
                f" < min_episode_ticks ({min_episode_ticks})"
            )

        included = exclusion_reason is None
        truncated = included and not alignment.aligned

        if not alignment.aligned:
            severity = "warning" if included else "error"
            for issue in alignment.issues:
                issues.append(QualityIssue(episode_id=episode_id, severity=severity,
                                            message=issue))
            if truncated:
                issues.append(QualityIssue(
                    episode_id=episode_id, severity="warning",
                    message=(
                        f"included with truncation — usable_tick_count="
                        f"{alignment.usable_tick_count} (frame={alignment.frame_count}, "
                        f"control={alignment.control_count}, telemetry={alignment.telemetry_count})"
                    ),
                ))

        pending.append(_PendingEpisode(
            episode_dir=episode_dir, episode_id=episode_id, meta=meta,
            validation=validation, alignment=alignment,
            included=included, truncated=truncated, exclusion_reason=exclusion_reason,
        ))

    included_ids = [p.episode_id for p in pending if p.included]
    split_assignment = assign_splits(included_ids, split_ratios, split_seed)

    episode_entries: list[EpisodeIndexEntry] = []
    sample_records: list[SampleRecord] = []

    for p in pending:
        split = split_assignment.get(p.episode_id) if p.included else None
        episode_entries.append(EpisodeIndexEntry(
            episode_id=p.episode_id,
            episode_dir=str(p.episode_dir),
            town=p.meta.get("town"),
            route_name=p.meta.get("route_name"),
            collection_mode=p.meta.get("collection_mode"),
            created_at=p.meta.get("created_at"),
            frame_count=p.alignment.frame_count,
            control_row_count=p.alignment.control_count,
            telemetry_row_count=p.alignment.telemetry_count,
            valid=p.validation.valid,
            validation_errors=list(p.validation.errors),
            aligned=p.alignment.aligned,
            alignment_issues=list(p.alignment.issues),
            usable_tick_count=p.alignment.usable_tick_count,
            included=p.included,
            exclusion_reason=p.exclusion_reason,
            truncated=p.truncated,
            split=split,
        ))
        if p.included:
            assert split is not None  # every included episode_id was assigned a split
            sample_records.extend(
                _build_samples(p.episode_dir, p.episode_id, p.alignment.usable_tick_count, split)
            )

    stats = compute_statistics(episode_entries, sample_records)
    _append_split_coverage_issues(issues, split_ratios, stats.split_counts, stats.sample_count)

    created_at = datetime.now(tz=timezone.utc).isoformat()
    included_count = len(included_ids)
    misaligned_count = sum(1 for e in episode_entries if not e.aligned)
    truncated_count = sum(1 for e in episode_entries if e.truncated)

    quality_report = QualityReport(
        schema_version=DATASET_SCHEMA_VERSION,
        created_at=created_at,
        episodes_scanned=len(episode_entries),
        episodes_valid=sum(1 for e in episode_entries if e.valid),
        episodes_invalid=sum(1 for e in episode_entries if not e.valid),
        episodes_included=included_count,
        episodes_excluded=len(episode_entries) - included_count,
        episodes_misaligned=misaligned_count,
        episodes_truncated=truncated_count,
        issues=issues,
    )

    split_index_paths = {name: f"{SPLITS_DIRNAME}/{name}.jsonl" for name in split_ratios}

    manifest = DatasetManifest(
        schema_version=DATASET_SCHEMA_VERSION,
        created_at=created_at,
        git_commit=get_git_commit(),
        dataset_id=resolved_dataset_id,
        raw_episodes_dir=str(raw_episodes_dir),
        output_dir=str(output_dir),
        episode_count_discovered=len(episode_entries),
        episode_count_included=included_count,
        episode_count_excluded=len(episode_entries) - included_count,
        sample_count=len(sample_records),
        split_ratios=dict(split_ratios),
        split_seed=split_seed,
        allow_partial_alignment=allow_partial_alignment,
        episodes_index_path=EPISODES_INDEX_FILENAME,
        samples_index_path=SAMPLES_INDEX_FILENAME,
        quality_report_path=QUALITY_REPORT_FILENAME,
        statistics_path=STATS_FILENAME,
        splits_dir=SPLITS_DIRNAME,
        split_index_paths=split_index_paths,
    )

    _write_jsonl(output_dir / EPISODES_INDEX_FILENAME, (e.to_dict() for e in episode_entries))
    _write_jsonl(output_dir / SAMPLES_INDEX_FILENAME, (s.to_dict() for s in sample_records))
    (output_dir / QUALITY_REPORT_FILENAME).write_text(
        json.dumps(quality_report.to_dict(), indent=2, default=str), encoding="utf-8",
    )
    (output_dir / STATS_FILENAME).write_text(
        json.dumps(stats.to_dict(), indent=2, default=str), encoding="utf-8",
    )
    (output_dir / DATASET_MANIFEST_FILENAME).write_text(
        json.dumps(manifest.to_dict(), indent=2, default=str), encoding="utf-8",
    )

    splits_dir = output_dir / SPLITS_DIRNAME
    splits_dir.mkdir(parents=True, exist_ok=True)
    for name in split_ratios:
        rows = (s.to_dict() for s in sample_records if s.split == name)
        _write_jsonl(splits_dir / f"{name}.jsonl", rows)

    log.info(
        "dataset_builder.done",
        dataset_id=resolved_dataset_id,
        episodes_discovered=len(episode_entries),
        episodes_included=included_count,
        samples=len(sample_records),
        misaligned=misaligned_count,
        truncated=truncated_count,
    )
    return manifest


# ── Internal helpers ───────────────────────────────────────────────────────────

def _append_split_coverage_issues(
    issues: list[QualityIssue],
    split_ratios: dict[str, float],
    split_counts: SplitCounts,
    sample_count: int,
) -> None:
    """Append a warning for every configured split that ended up with 0 samples.

    Only relevant when there are samples at all — an empty dataset has
    nothing to warn about.

    Args:
        issues: Quality issue list to append to, in place.
        split_ratios: The requested split ratios, as given to
            :func:`build_dataset`.
        split_counts: The built :class:`~src.data.dataset_schemas.SplitCounts`.
        sample_count: Total number of samples in the dataset.
    """
    if sample_count == 0:
        return
    counts_by_name = {
        "train": split_counts.train,
        "val": split_counts.val,
        "test": split_counts.test,
    }
    for name, ratio in split_ratios.items():
        count = counts_by_name.get(name, 0)
        if ratio > 0 and count == 0:
            issues.append(QualityIssue(
                episode_id=DATASET_LEVEL_ISSUE, severity="warning",
                message=(
                    f"split {name!r} has 0 samples despite a configured ratio of {ratio}"
                    " — likely too few episodes to cover every split"
                ),
            ))


def _read_metadata(episode_dir: Path) -> dict[str, Any]:
    """Best-effort read of ``metadata.json`` fields used for indexing.

    Args:
        episode_dir: Episode root directory.

    Returns:
        Parsed metadata dict, or an empty dict if ``metadata.json`` is
        missing or unparseable.
    """
    path = episode_dir / "metadata.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _build_samples(
    episode_dir: Path,
    episode_id: str,
    usable_tick_count: int,
    split: str,
) -> list[SampleRecord]:
    """Build one SampleRecord per usable tick of an included episode.

    Args:
        episode_dir: Episode root directory.
        episode_id: Episode identifier.
        usable_tick_count: Number of leading ticks that are aligned across
            frames, controls, and telemetry (see
            :func:`~src.data.dataset_alignment.check_alignment`).
        split: Split assigned to this episode.

    Returns:
        List of :class:`~src.data.dataset_schemas.SampleRecord`, one per
        tick ``0 .. usable_tick_count - 1``.
    """
    if usable_tick_count == 0:
        return []

    controls = _read_jsonl(episode_dir / "controls.jsonl")[:usable_tick_count]
    telemetry = _read_jsonl(episode_dir / "telemetry.jsonl")[:usable_tick_count]
    camera_dir = episode_dir / "frames" / "front_camera"

    samples: list[SampleRecord] = []
    for tick in range(usable_tick_count):
        control = controls[tick]
        telem = telemetry[tick]
        frame_path = camera_dir / f"{tick:06d}.png"
        samples.append(SampleRecord(
            sample_id=f"{episode_id}_{tick:06d}",
            episode_id=episode_id,
            tick=tick,
            frame_path=str(frame_path),
            throttle=float(control.get("throttle", 0.0)),
            brake=float(control.get("brake", 0.0)),
            steer=float(control.get("steer", 0.0)),
            speed_kph=float(telem.get("speed_kph", 0.0)),
            split=split,
        ))
    return samples


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file into a list of dicts, stopping at the first bad line.

    Args:
        path: Path to a ``.jsonl`` file.

    Returns:
        List of parsed records in file order. Empty if the file is missing.
    """
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            break
        records.append(record)
    return records


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """Write an iterable of dicts as one JSON object per line.

    Args:
        path: Destination file path.
        records: Iterable of JSON-serializable dicts.
    """
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, default=str) + "\n")
