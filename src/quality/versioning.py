"""
src/quality/versioning.py — Artifact identity, hashing, changelog generation.

Dataset-specific today (the only artifact type this phase implements), but
:class:`~src.quality.schemas.VersionRecord`'s shape is generic across
artifact types — see docs/ADR/0006-artifact-versioning.md. Phase 4 adds an
analogous set of functions once a second artifact type's loader exists,
following this module's exact pattern (docs/ADR/0010-future-ml-integration.md
Section 2) rather than a rewrite.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import src.quality as quality_package
from src.data.dataset_builder import DATASET_MANIFEST_FILENAME
from src.data.episode import get_git_commit
from src.quality.artifacts import (
    ARTIFACT_LOAD_ERRORS,
    hash_content,
    load_dataset_artifacts,
)
from src.quality.config import QualityEngineeringConfig, load_quality_config
from src.quality.regression import compare_datasets
from src.quality.schemas import (
    QUALITY_SCHEMA_VERSION,
    LineageEdge,
    RegressionFinding,
    RegressionReport,
    VersionRecord,
)
from src.utils.config import ConfigDict


def compute_version_record(
    dataset_dir: Path,
    cfg: ConfigDict,
    *,
    previous_artifact_id: str | None = None,
    lineage_parents: list[LineageEdge] | None = None,
) -> VersionRecord:
    """Compute a :class:`~src.quality.schemas.VersionRecord` for a dataset (without writing it).

    Args:
        dataset_dir: The dataset directory to version.
        cfg: The full merged configuration dict (not just
            ``quality_engineering:``) — ``config_hash`` is computed over
            the resolved ``dataset_engineering:`` and
            ``quality_engineering:`` sections only (docs/ADR/0006
            Decision 3), never the whole file.
        previous_artifact_id: Explicit prior version to record. Defaults
            to the most recently built dataset in the same parent
            directory, excluding this one (docs/ADR/0006 Decision 4).
        lineage_parents: Cross-artifact-type derivation edges (docs/ADR/0011).
            Empty by default — nothing produces datasets from other
            artifacts.

    Returns:
        A fully-populated :class:`~src.quality.schemas.VersionRecord`.

    Raises:
        ArtifactNotFoundError: If *dataset_dir* has no
            ``dataset_manifest.json``.
    """
    dataset_dir = Path(dataset_dir)
    artifact = load_dataset_artifacts(dataset_dir)

    config_hash = hash_content({
        "dataset_engineering": cfg.get("dataset_engineering", {}),
        "quality_engineering": cfg.get("quality_engineering", {}),
    })
    content_hashes = {
        "manifest": hash_content(artifact.manifest.to_dict()),
        "statistics": hash_content(artifact.stats.to_dict()),
        "quality_report": hash_content(artifact.quality_report.to_dict()),
    }
    summary_counts = {
        "sample_count": artifact.manifest.sample_count,
        "episode_count": artifact.manifest.episode_count_included,
    }

    resolved_previous = (
        previous_artifact_id
        if previous_artifact_id is not None
        else _resolve_previous_artifact_id(dataset_dir)
    )

    return VersionRecord(
        schema_version=QUALITY_SCHEMA_VERSION,
        artifact_type="dataset",
        artifact_id=artifact.artifact_id,
        created_at=datetime.now(tz=timezone.utc).isoformat(),
        git_commit=get_git_commit(),
        config_hash=config_hash,
        content_hashes=content_hashes,
        generator_version=quality_package.__version__,
        summary_counts=summary_counts,
        previous_artifact_id=resolved_previous,
        lineage_parents=list(lineage_parents) if lineage_parents else [],
    )


def generate_changelog(
    dataset_dir: Path, version: VersionRecord, quality_cfg: QualityEngineeringConfig,
) -> str:
    """Generate Markdown changelog text comparing *version* to its previous artifact.

    Args:
        dataset_dir: The dataset directory *version* describes.
        version: The dataset's own :class:`~src.quality.schemas.VersionRecord`.
        quality_cfg: Resolved engineering-loop configuration (used to
            compare against the previous dataset via
            :func:`src.quality.regression.compare_datasets`).

    Returns:
        Markdown text with four sections: Added, Removed, Changed,
        Improved, Regressions. If ``version.previous_artifact_id`` is
        None, or the previous dataset's artifacts can no longer be found,
        returns a single-line placeholder instead.
    """
    if version.previous_artifact_id is None:
        return "# Changelog\n\nInitial dataset — no prior version to compare.\n"

    baseline_dir = dataset_dir.parent / version.previous_artifact_id
    try:
        baseline_artifact = load_dataset_artifacts(baseline_dir)
    except ARTIFACT_LOAD_ERRORS:
        return (
            "# Changelog\n\n"
            f"Previous version `{version.previous_artifact_id}` was recorded but its artifacts "
            "could not be found or loaded — no comparison available.\n"
        )

    candidate_artifact = load_dataset_artifacts(dataset_dir)
    report = compare_datasets(baseline_artifact, candidate_artifact, quality_cfg)
    return _render_changelog(version, report)


def write_version_artifacts(dataset_dir: Path, cfg: ConfigDict) -> VersionRecord:
    """Compute and write ``version.json`` and the changelog for a dataset.

    The CLI surface (``make version``) for this — idempotent and safe to
    re-run (docs/ADR/0006 Decision 6): overwrites only the two files this
    function writes, never any Phase 3 artifact.

    Args:
        dataset_dir: The dataset directory to version.
        cfg: The full merged configuration dict.

    Returns:
        The :class:`~src.quality.schemas.VersionRecord` written.
    """
    dataset_dir = Path(dataset_dir)
    quality_cfg = load_quality_config(cfg)

    version = compute_version_record(dataset_dir, cfg)
    version_path = dataset_dir / quality_cfg.versioning.version_filename
    version_path.write_text(json.dumps(version.to_dict(), indent=2, default=str), encoding="utf-8")

    changelog_text = generate_changelog(dataset_dir, version, quality_cfg)
    changelog_path = dataset_dir / quality_cfg.versioning.changelog_filename
    changelog_path.write_text(changelog_text, encoding="utf-8")

    return version


# ── Internal helpers ───────────────────────────────────────────────────────────

def _resolve_previous_artifact_id(dataset_dir: Path) -> str | None:
    """Resolve the most recently built dataset before *dataset_dir*, by mtime.

    Mirrors the "most recent" convention already used by
    :func:`src.quality.artifacts.resolve_latest_dataset_dir` and
    ``inspect_dataset.py``, scoped to "excluding this one."

    Args:
        dataset_dir: The dataset directory being versioned.

    Returns:
        The previous dataset's ``dataset_id``, or None if none is found.
    """
    datasets_dir = dataset_dir.parent
    if not datasets_dir.is_dir():
        return None
    candidates = [
        p for p in datasets_dir.iterdir()
        if p.is_dir() and p != dataset_dir and (p / DATASET_MANIFEST_FILENAME).exists()
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        return load_dataset_artifacts(latest).artifact_id
    except ARTIFACT_LOAD_ERRORS:
        return None


def _render_changelog(version: VersionRecord, report: RegressionReport) -> str:
    """Render a :class:`~src.quality.schemas.RegressionReport` as Markdown changelog sections.

    Args:
        version: The candidate's own version record (for the header).
        report: The regression comparison to render.

    Returns:
        Markdown text with Added / Removed / Changed / Improved /
        Regressions sections.
    """
    categorical_prefixes = ("town:", "weather:", "route:")
    added: list[RegressionFinding] = []
    removed: list[RegressionFinding] = []
    changed: list[RegressionFinding] = []
    improved: list[RegressionFinding] = []
    regressions: list[RegressionFinding] = []

    for finding in report.findings:
        is_categorical = finding.dimension.startswith(categorical_prefixes)
        if is_categorical and not finding.baseline_value and finding.candidate_value:
            added.append(finding)
        elif is_categorical and finding.baseline_value and not finding.candidate_value:
            removed.append(finding)
        elif finding.severity in ("warning", "failure"):
            regressions.append(finding)
        elif finding.severity == "improvement":
            improved.append(finding)
        else:
            changed.append(finding)

    lines = [
        "# Changelog", "",
        f"Comparing `{version.previous_artifact_id}` -> `{version.artifact_id}`.", "",
    ]
    for title, items in (
        ("Added", added), ("Removed", removed), ("Changed", changed),
        ("Improved", improved), ("Regressions", regressions),
    ):
        lines.append(f"## {title}")
        lines.append("")
        if items:
            lines.extend(f"- {finding.message}" for finding in items)
        else:
            lines.append("- (none)")
        lines.append("")

    return "\n".join(lines) + "\n"
