"""
src/quality/artifacts.py — Load and hash artifacts produced by other phases.

The only module in :mod:`src.quality` that parses raw JSON/JSONL (see
docs/ADR/0004-engineering-loop-architecture.md Decision 2). Every other
module in this package reads a dataset (or, from Phase 4 on, any other
artifact type) through :func:`load_dataset_artifacts` /
:func:`load_artifact_envelope`, never by opening a file itself.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

from src.data.dataset_builder import (
    DATASET_MANIFEST_FILENAME,
    EPISODES_INDEX_FILENAME,
    QUALITY_REPORT_FILENAME,
    SAMPLES_INDEX_FILENAME,
    STATS_FILENAME,
)
from src.data.dataset_io import read_jsonl_records
from src.data.dataset_schemas import (
    DatasetManifest,
    DatasetStatistics,
    EpisodeIndexEntry,
    HistogramBin,
    QualityIssue,
    QualityReport,
    SampleRecord,
    SplitCounts,
    ValueStats,
)
from src.quality.schemas import Artifact, LineageEdge, MetricResult, QualityScore, VersionRecord

#: Filename of the generic artifact envelope written by
#: src.quality.versioning — read here without knowing an artifact's
#: kind-specific schema (see docs/ADR/0011-experiment-tracking-lineage.md).
VERSION_FILENAME = "version.json"

#: Filename of the quality score written by src.quality.scoring. Kept as
#: an independent literal here (matching src.quality.scoring's own
#: QUALITY_SCORE_FILENAME constant) rather than imported from
#: scoring.py, which would create an artifacts.py <-> scoring.py import
#: cycle — scoring.py already imports from artifacts.py.
QUALITY_SCORE_FILENAME = "quality_score.json"


class ArtifactNotFoundError(FileNotFoundError):
    """Raised when an expected artifact file is missing on disk."""


#: Errors :func:`load_dataset_artifacts` (and the other loaders in this
#: module) can raise for a directory that exists but cannot be loaded as a
#: usable artifact today: a missing file (:class:`ArtifactNotFoundError`),
#: or a manifest written by an older schema version that no longer matches
#: the current dataclass fields (``DatasetManifest(**_read_json(...))``
#: raises ``TypeError`` for missing/unexpected keyword arguments,
#: ``KeyError``/``ValueError`` for other structurally-invalid JSON).
#: Best-effort scans that try many candidate directories — resolving
#: "previous version" by mtime, resolving a dashboard/changelog baseline —
#: treat any of these as "not a usable candidate for this purpose" and skip
#: it, rather than letting one incompatible directory crash the whole
#: operation. Callers that expect exactly one specific artifact to load
#: successfully (e.g. the primary `--dataset-dir` a CLI was pointed at)
#: should NOT use this tuple — they should let the precise error surface.
ARTIFACT_LOAD_ERRORS: tuple[type[Exception], ...] = (
    ArtifactNotFoundError, TypeError, KeyError, ValueError,
)


# ── Dataset-specific artifact ────────────────────────────────────────────────────

@dataclasses.dataclass
class DatasetArtifact(Artifact):
    """Dataset-specific :class:`~src.quality.schemas.Artifact` subtype.

    A proper ``@dataclass`` subclass (not a hand-written ``__init__``) so
    that ``to_dict()`` — inherited unchanged from :class:`Artifact` — sees
    every field via :func:`dataclasses.fields`, base and subclass alike;
    a manually-written subclass would leave ``dataclasses.asdict()``
    seeing only the base class's fields.

    Args:
        manifest: Parsed ``dataset_manifest.json``.
        episodes: Parsed ``episodes_index.jsonl`` rows (both included and
            excluded episodes).
        stats: Parsed ``stats.json``.
        quality_report: Parsed ``quality_report.json``.
        samples: Parsed ``samples_index.jsonl`` rows, or None if not
            requested (:func:`load_dataset_artifacts`'s ``load_samples``
            defaults to False — the sample index can be large and most
            callers only need the other four files).
    """

    manifest: DatasetManifest
    episodes: list[EpisodeIndexEntry]
    stats: DatasetStatistics
    quality_report: QualityReport
    samples: list[SampleRecord] | None = None


def load_dataset_artifacts(dataset_dir: Path, *, load_samples: bool = False) -> DatasetArtifact:
    """Load every dataset-engineering artifact from *dataset_dir* into one object.

    Args:
        dataset_dir: Directory containing ``dataset_manifest.json`` and
            its sibling files, e.g. ``data/processed/datasets/<dataset_id>``.
        load_samples: If True, also parse ``samples_index.jsonl`` (can be
            large — only requested by callers that actually need
            per-sample data).

    Returns:
        A fully-populated :class:`DatasetArtifact`.

    Raises:
        ArtifactNotFoundError: If ``dataset_manifest.json`` is missing.
    """
    dataset_dir = Path(dataset_dir)
    manifest_path = dataset_dir / DATASET_MANIFEST_FILENAME
    if not manifest_path.exists():
        raise ArtifactNotFoundError(f"dataset_manifest.json not found: {manifest_path}")

    manifest = DatasetManifest(**_read_json(manifest_path))
    episodes = [
        _episode_entry_from_dict(row)
        for row in read_jsonl_records(dataset_dir / EPISODES_INDEX_FILENAME)
    ]
    stats = _stats_from_dict(_read_json(dataset_dir / STATS_FILENAME))
    quality_report = _quality_report_from_dict(_read_json(dataset_dir / QUALITY_REPORT_FILENAME))

    samples: list[SampleRecord] | None = None
    if load_samples:
        samples = [
            SampleRecord(**row)
            for row in read_jsonl_records(dataset_dir / SAMPLES_INDEX_FILENAME)
        ]

    return DatasetArtifact(
        artifact_id=manifest.dataset_id,
        artifact_type="dataset",
        artifact_dir=dataset_dir,
        created_at=manifest.created_at,
        git_commit=manifest.git_commit,
        manifest=manifest,
        episodes=episodes,
        stats=stats,
        quality_report=quality_report,
        samples=samples,
    )


# ── Generic artifact envelope ────────────────────────────────────────────────────

def load_artifact_envelope(artifact_dir: Path) -> Artifact:
    """Read only *artifact_dir*'s ``version.json`` identity fields.

    Generic across artifact types — used by :mod:`src.quality.lineage`,
    which must not need to know a dataset's (or a future model's)
    kind-specific schema to build the derivation graph.

    Args:
        artifact_dir: Directory containing a ``version.json`` written by
            :mod:`src.quality.versioning`.

    Returns:
        A minimal :class:`~src.quality.schemas.Artifact`.

    Raises:
        ArtifactNotFoundError: If ``version.json`` is missing.
    """
    version_path = artifact_dir / VERSION_FILENAME
    if not version_path.exists():
        raise ArtifactNotFoundError(f"version.json not found: {version_path}")
    data = _read_json(version_path)
    return Artifact(
        artifact_id=data["artifact_id"],
        artifact_type=data["artifact_type"],
        artifact_dir=artifact_dir,
        created_at=data.get("created_at"),
        git_commit=data.get("git_commit"),
    )


def load_version_record(artifact_dir: Path) -> VersionRecord:
    """Read and fully parse *artifact_dir*'s ``version.json``.

    Unlike :func:`load_artifact_envelope` (identity fields only), this
    returns the complete :class:`~src.quality.schemas.VersionRecord`,
    including ``previous_artifact_id`` and ``lineage_parents`` — what
    :mod:`src.quality.lineage` needs to build the derivation graph.

    Args:
        artifact_dir: Directory containing a ``version.json`` written by
            :mod:`src.quality.versioning`.

    Returns:
        A fully-populated :class:`~src.quality.schemas.VersionRecord`.

    Raises:
        ArtifactNotFoundError: If ``version.json`` is missing.
    """
    version_path = artifact_dir / VERSION_FILENAME
    if not version_path.exists():
        raise ArtifactNotFoundError(f"version.json not found: {version_path}")
    d = _read_json(version_path)
    return VersionRecord(
        schema_version=d["schema_version"],
        artifact_type=d["artifact_type"],
        artifact_id=d["artifact_id"],
        created_at=d["created_at"],
        git_commit=d.get("git_commit"),
        config_hash=d["config_hash"],
        content_hashes=d["content_hashes"],
        generator_version=d["generator_version"],
        summary_counts=d["summary_counts"],
        previous_artifact_id=d.get("previous_artifact_id"),
        lineage_parents=[LineageEdge(**e) for e in d.get("lineage_parents", [])],
    )


def load_quality_score_record(dataset_dir: Path) -> QualityScore:
    """Read and fully parse *dataset_dir*'s ``quality_score.json``.

    Used by :mod:`src.quality.dashboard`'s Quality Trend section, which
    scans many dataset directories' historical scores — reading through
    this function keeps :mod:`src.quality.dashboard` from parsing raw
    JSON itself (docs/ADR/0004-engineering-loop-architecture.md Decision 2).

    Args:
        dataset_dir: Directory containing a ``quality_score.json`` written
            by :mod:`src.quality.scoring`.

    Returns:
        A fully-populated :class:`~src.quality.schemas.QualityScore`.

    Raises:
        ArtifactNotFoundError: If ``quality_score.json`` is missing.
    """
    d = _read_json(Path(dataset_dir) / QUALITY_SCORE_FILENAME)
    return QualityScore(
        schema_version=d["schema_version"],
        created_at=d["created_at"],
        artifact_id=d["artifact_id"],
        overall_score=d["overall_score"],
        grade=d["grade"],
        metrics={name: MetricResult(**m) for name, m in d["metrics"].items()},
        weights_used=d["weights_used"],
        grade_thresholds_used=d["grade_thresholds_used"],
    )


# ── Hashing ────────────────────────────────────────────────────────────────────

def hash_content(obj: Any) -> str:
    """Return the full SHA-256 hex digest of *obj*'s canonicalized JSON content.

    Reuses the canonicalize-then-hash approach
    :func:`src.data.episode.compute_route_hash` established (sorted keys,
    so field order never affects the hash) but returns the full 64-character
    digest rather than an 8-character prefix — these hashes exist for exact
    reproducibility verification, not as a compact display label (see
    docs/ADR/0006-artifact-versioning.md Decision 2).

    Args:
        obj: Any JSON-serializable value — typically the ``.to_dict()`` of
            one of this artifact's own files' contents.

    Returns:
        64-character lowercase hex string.
    """
    canonical = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── "Most recent" resolution (shared across all six new CLIs) ──────────────────

def resolve_latest_dataset_dir(datasets_dir: Path) -> Path | None:
    """Return the most recently modified immediate subdirectory of *datasets_dir*.

    The single implementation behind every "default to the most recently
    built dataset" CLI default in this package (Finding D,
    docs/ARCHITECTURE_REVIEW.md) — also used by
    :mod:`scripts.inspect_dataset`, which originally had its own private
    copy of this exact function.

    Args:
        datasets_dir: Parent directory containing one subdirectory per
            dataset build (see ``dataset_engineering.datasets_dir``).

    Returns:
        The most recently modified subdirectory, or None if *datasets_dir*
        does not exist or contains no subdirectories.
    """
    datasets_dir = Path(datasets_dir)
    if not datasets_dir.is_dir():
        return None
    candidates = [p for p in datasets_dir.iterdir() if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ── Internal deserialization helpers ────────────────────────────────────────────

def _read_json(path: Path) -> dict[str, Any]:
    """Read and parse one JSON file.

    Args:
        path: Path to the ``.json`` file.

    Returns:
        Parsed JSON as a dict.

    Raises:
        ArtifactNotFoundError: If *path* does not exist.
    """
    if not path.exists():
        raise ArtifactNotFoundError(f"Expected artifact file not found: {path}")
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _episode_entry_from_dict(d: dict[str, Any]) -> EpisodeIndexEntry:
    """Reconstruct an :class:`EpisodeIndexEntry`, defaulting a missing ``weather``.

    Args:
        d: One parsed row of ``episodes_index.jsonl``.

    Returns:
        An :class:`EpisodeIndexEntry`. Rows written before schema 1.1 (no
        ``weather`` key) default to ``weather=None``.
    """
    return EpisodeIndexEntry(**{**d, "weather": d.get("weather")})


def _stats_from_dict(d: dict[str, Any]) -> DatasetStatistics:
    """Reconstruct a :class:`DatasetStatistics` from ``stats.json``'s parsed content.

    Args:
        d: Parsed contents of ``stats.json``.

    Returns:
        A :class:`DatasetStatistics`. Missing ``weather`` (schema < 1.1)
        defaults to an empty dict.
    """
    def _value_stats(key: str) -> ValueStats | None:
        raw = d.get(key)
        return ValueStats(**raw) if raw is not None else None

    return DatasetStatistics(
        episode_count=d["episode_count"],
        sample_count=d["sample_count"],
        split_counts=SplitCounts(**d["split_counts"]),
        towns=d["towns"],
        weather=d.get("weather", {}),
        throttle=_value_stats("throttle"),
        brake=_value_stats("brake"),
        steer=_value_stats("steer"),
        speed_kph=_value_stats("speed_kph"),
        steering_histogram=[HistogramBin(**b) for b in d.get("steering_histogram", [])],
    )


def _quality_report_from_dict(d: dict[str, Any]) -> QualityReport:
    """Reconstruct a :class:`QualityReport` from ``quality_report.json``'s parsed content.

    Args:
        d: Parsed contents of ``quality_report.json``.

    Returns:
        A :class:`QualityReport`. Missing ``duplicate_sample_count``
        (schema < 1.1) defaults to 0.
    """
    return QualityReport(
        schema_version=d["schema_version"],
        created_at=d["created_at"],
        episodes_scanned=d["episodes_scanned"],
        episodes_valid=d["episodes_valid"],
        episodes_invalid=d["episodes_invalid"],
        episodes_included=d["episodes_included"],
        episodes_excluded=d["episodes_excluded"],
        episodes_misaligned=d["episodes_misaligned"],
        episodes_truncated=d["episodes_truncated"],
        episodes_with_outliers=d["episodes_with_outliers"],
        duplicate_frame_groups=d["duplicate_frame_groups"],
        duplicate_sample_count=d.get("duplicate_sample_count", 0),
        issues=[QualityIssue(**i) for i in d.get("issues", [])],
    )
