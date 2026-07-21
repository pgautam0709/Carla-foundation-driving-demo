"""
src/quality/regression.py — Artifact-to-artifact-of-the-same-type comparison.

:func:`compare_metric_snapshots` is the artifact-agnostic core: it diffs two
named numeric snapshots against two-tier (warning/failure) thresholds — the
same shape Phase 5 will reuse to compare two evaluation runs instead of two
datasets (docs/ADR/0010-future-ml-integration.md Section 3). It knows
nothing about datasets specifically.

:func:`compare_datasets` is the dataset-specific caller: it builds the
numeric snapshot (sample count, quality score, duplicate rate, outlier
rate), then separately compares the dataset-only dimensions (steering/
throttle/brake means, towns, weather, routes) that have no generic
equivalent — see docs/ADR/0007-regression-detection.md Decision 3.

Every comparison is always ``candidate`` relative to ``baseline`` — never
symmetric (Decision 1): a positive ``delta`` on a "lower is better"
dimension is not automatically bad, and a positive delta on a "higher is
better" dimension is not automatically good; severity is derived from a
per-dimension "badness" computation, not from the raw sign of ``delta``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.quality.artifacts import DatasetArtifact
from src.quality.config import QualityEngineeringConfig, RegressionThresholds
from src.quality.schemas import (
    QUALITY_SCHEMA_VERSION,
    MetricResult,
    QualityScore,
    RegressionFinding,
    RegressionReport,
)
from src.quality.scoring import compute_quality_score

#: Default filename written into a candidate dataset directory by
#: :func:`write_regression_report`.
REGRESSION_REPORT_FILENAME = "regression_report.json"

#: Numeric dimensions with a configured two-tier threshold, mapped to the
#: field on RegressionThresholds that names their warning/failure cutoff,
#: and a function computing how much worse *candidate* is than *baseline*
#: in that dimension's own unit (positive = worse; negative = improvement).
_THRESHOLD_FIELD_BY_DIMENSION: dict[str, str] = {
    "sample_count": "sample_count_drop_pct",
    "quality_score": "quality_score_drop_pts",
    "duplicate_rate": "duplicate_rate_increase_pct",
    "outlier_rate": "outlier_rate_increase_pct",
}


def compare_metric_snapshots(
    baseline: dict[str, float] | None,
    candidate: dict[str, float],
    warning: RegressionThresholds,
    failure: RegressionThresholds,
) -> list[RegressionFinding]:
    """Diff two named numeric metric snapshots against two-tier thresholds.

    Recognizes four dimension names with configured thresholds today —
    ``"sample_count"``, ``"quality_score"``, ``"duplicate_rate"``,
    ``"outlier_rate"`` (matching :class:`~src.quality.config.RegressionThresholds`'s
    own field names) — and reports any other shared key as
    ``"informational"``, since only thresholded dimensions can be
    classified as an improvement, warning, or failure.

    Args:
        baseline: Baseline snapshot, or None if no baseline is available
            (e.g. the first artifact of its type ever built).
        candidate: Candidate snapshot — always present.
        warning: Warning-tier thresholds.
        failure: Failure-tier thresholds.

    Returns:
        One :class:`~src.quality.schemas.RegressionFinding` per key present
        in *candidate* (and *baseline*, when given).
    """
    if baseline is None:
        return [
            RegressionFinding(
                dimension=key, baseline_value=None, candidate_value=value, delta=None,
                severity="informational", message=f"{key}: no baseline available ({value:.2f})",
            )
            for key, value in sorted(candidate.items())
        ]

    findings: list[RegressionFinding] = []
    for key in sorted(set(baseline) | set(candidate)):
        base_val = baseline.get(key)
        cand_val = candidate.get(key)
        if base_val is None or cand_val is None:
            findings.append(RegressionFinding(
                dimension=key, baseline_value=base_val, candidate_value=cand_val, delta=None,
                severity="informational", message=f"{key}: present in only one snapshot",
            ))
            continue

        delta = cand_val - base_val
        field = _THRESHOLD_FIELD_BY_DIMENSION.get(key)
        if field is None:
            severity = "informational"
        else:
            badness = _badness(key, base_val, cand_val)
            severity = _severity_for_badness(
                badness, getattr(warning, field), getattr(failure, field),
            )
        findings.append(RegressionFinding(
            dimension=key, baseline_value=base_val, candidate_value=cand_val, delta=delta,
            severity=severity, message=f"{key}: {base_val:.2f} -> {cand_val:.2f} ({delta:+.2f})",
        ))
    return findings


def compare_datasets(
    baseline: DatasetArtifact | None,
    candidate: DatasetArtifact,
    cfg: QualityEngineeringConfig,
) -> RegressionReport:
    """Compare *candidate* against *baseline* across every dataset dimension.

    Args:
        baseline: The baseline dataset, or None if unavailable — every
            dimension is then reported ``"informational"`` with no
            baseline value.
        candidate: The candidate dataset — always compared.
        cfg: Resolved engineering-loop configuration (uses
            ``cfg.regression``).

    Returns:
        A :class:`~src.quality.schemas.RegressionReport` covering samples,
        episodes, quality score (overall and per sub-metric), steering/
        throttle/brake/speed means, towns, weather, routes, and duplicate/
        outlier rates.
    """
    created_at = datetime.now(tz=timezone.utc).isoformat()

    if baseline is None:
        candidate_score = compute_quality_score(candidate, cfg)
        snapshot = _numeric_snapshot(candidate, candidate_score)
        findings = compare_metric_snapshots(
            None, snapshot, cfg.regression.warning, cfg.regression.failure,
        )
        return RegressionReport(
            schema_version=QUALITY_SCHEMA_VERSION,
            created_at=created_at,
            artifact_type="dataset",
            baseline_artifact_id=None,
            candidate_artifact_id=candidate.artifact_id,
            findings=findings,
        )

    baseline_score = compute_quality_score(baseline, cfg)
    candidate_score = compute_quality_score(candidate, cfg)

    findings = compare_metric_snapshots(
        _numeric_snapshot(baseline, baseline_score), _numeric_snapshot(candidate, candidate_score),
        cfg.regression.warning, cfg.regression.failure,
    )

    findings.append(_informational_count(
        "episode_count",
        baseline.manifest.episode_count_included,
        candidate.manifest.episode_count_included,
    ))
    findings.extend(_compare_submetrics(baseline_score.metrics, candidate_score.metrics))
    findings.extend(_compare_signal_means(baseline, candidate))

    lost_is_failure = cfg.regression.failure.town_or_weather_cell_lost
    findings.extend(
        _compare_categorical(baseline.stats.towns, candidate.stats.towns, "town", lost_is_failure)
    )
    findings.extend(
        _compare_categorical(
            baseline.stats.weather, candidate.stats.weather, "weather", lost_is_failure,
        )
    )
    findings.extend(
        _compare_categorical(
            _route_counts(baseline), _route_counts(candidate), "route", lost_is_failure=False,
        )
    )

    return RegressionReport(
        schema_version=QUALITY_SCHEMA_VERSION,
        created_at=created_at,
        artifact_type="dataset",
        baseline_artifact_id=baseline.artifact_id,
        candidate_artifact_id=candidate.artifact_id,
        findings=findings,
    )


def write_regression_report(
    dataset_dir: Path, report: RegressionReport, filename: str = REGRESSION_REPORT_FILENAME,
) -> Path:
    """Write *report* to ``<dataset_dir>/<filename>``.

    Args:
        dataset_dir: The candidate dataset's own directory to write into.
        report: The :class:`~src.quality.schemas.RegressionReport` to persist.
        filename: Output filename, relative to *dataset_dir*.

    Returns:
        The path written to.
    """
    path = Path(dataset_dir) / filename
    path.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    return path


# ── Internal helpers ───────────────────────────────────────────────────────────

def _badness(key: str, baseline: float, candidate: float) -> float:
    """Return how much worse *candidate* is than *baseline* for *key* (positive = worse).

    Args:
        key: One of :data:`_THRESHOLD_FIELD_BY_DIMENSION`'s keys.
        baseline: Baseline value.
        candidate: Candidate value.

    Returns:
        A "badness" value in the threshold's own unit — percent-of-baseline
        for ``sample_count``, absolute points for ``quality_score``,
        percentage points for the two rate dimensions.
    """
    if key == "sample_count":
        return 0.0 if baseline <= 0 else 100.0 * (baseline - candidate) / baseline
    if key == "quality_score":
        return baseline - candidate
    # duplicate_rate / outlier_rate: an increase is worse.
    return candidate - baseline


def _severity_for_badness(
    badness: float, warning_threshold: float, failure_threshold: float,
) -> str:
    """Classify a "badness" value against two-tier thresholds.

    Args:
        badness: Positive means worse than baseline by that much.
        warning_threshold: Badness at or above this is a "warning".
        failure_threshold: Badness at or above this is a "failure".

    Returns:
        ``"failure"`` | ``"warning"`` | ``"improvement"`` | ``"informational"``.
    """
    if badness >= failure_threshold:
        return "failure"
    if badness >= warning_threshold:
        return "warning"
    if badness < 0:
        return "improvement"
    return "informational"


def _numeric_snapshot(artifact: DatasetArtifact, score: QualityScore) -> dict[str, float]:
    """Build the four-key snapshot :func:`compare_metric_snapshots` recognizes.

    Args:
        artifact: The dataset to snapshot.
        score: That dataset's already-computed
            :class:`~src.quality.schemas.QualityScore`.

    Returns:
        ``{"sample_count", "quality_score", "duplicate_rate", "outlier_rate"}``.
    """
    sample_count = artifact.manifest.sample_count
    dup_count = artifact.quality_report.duplicate_sample_count
    duplicate_rate = 100.0 * dup_count / sample_count if sample_count else 0.0
    included = artifact.quality_report.episodes_included
    outlier_count = artifact.quality_report.episodes_with_outliers
    outlier_rate = 100.0 * outlier_count / included if included else 0.0
    return {
        "sample_count": float(sample_count),
        "quality_score": score.overall_score,
        "duplicate_rate": duplicate_rate,
        "outlier_rate": outlier_rate,
    }


def _informational_count(dimension: str, baseline: int, candidate: int) -> RegressionFinding:
    """Build an always-informational finding for a plain integer count.

    Args:
        dimension: Finding dimension name.
        baseline: Baseline count.
        candidate: Candidate count.

    Returns:
        A :class:`~src.quality.schemas.RegressionFinding` with severity
        ``"informational"``.
    """
    return RegressionFinding(
        dimension=dimension, baseline_value=baseline, candidate_value=candidate,
        delta=float(candidate - baseline), severity="informational",
        message=f"{dimension}: {baseline} -> {candidate}",
    )


def _compare_submetrics(
    baseline_metrics: dict[str, MetricResult], candidate_metrics: dict[str, MetricResult],
) -> list[RegressionFinding]:
    """Diff each quality sub-metric's raw score, informationally.

    Not individually thresholded — only the blended ``quality_score``
    dimension is (via :func:`compare_metric_snapshots`); this is visibility
    into *which* sub-metric moved, not a second set of gates.

    Args:
        baseline_metrics: ``QualityScore.metrics`` from the baseline.
        candidate_metrics: ``QualityScore.metrics`` from the candidate.

    Returns:
        One finding per sub-metric name present in both.
    """
    findings: list[RegressionFinding] = []
    for name in sorted(set(baseline_metrics) & set(candidate_metrics)):
        b = baseline_metrics[name].raw_score
        c = candidate_metrics[name].raw_score
        delta = c - b
        if delta > 0.01:
            severity = "improvement"
        elif delta < -0.01:
            severity = "warning"
        else:
            severity = "informational"
        findings.append(RegressionFinding(
            dimension=f"quality_score.{name}", baseline_value=b, candidate_value=c, delta=delta,
            severity=severity, message=f"{name} sub-score {b:.1f} -> {c:.1f}",
        ))
    return findings


def _compare_signal_means(
    baseline: DatasetArtifact, candidate: DatasetArtifact,
) -> list[RegressionFinding]:
    """Report mean-value drift for steer/throttle/brake/speed, informationally.

    Args:
        baseline: Baseline dataset.
        candidate: Candidate dataset.

    Returns:
        One finding per signal present (as a computed mean) in either
        dataset.
    """
    findings: list[RegressionFinding] = []
    for signal in ("steer", "throttle", "brake", "speed_kph"):
        base_stats = getattr(baseline.stats, signal)
        cand_stats = getattr(candidate.stats, signal)
        base_mean = base_stats.mean if base_stats is not None else None
        cand_mean = cand_stats.mean if cand_stats is not None else None
        if base_mean is None and cand_mean is None:
            continue
        delta: float | None = None
        if base_mean is not None and cand_mean is not None:
            delta = cand_mean - base_mean
        message = f"{signal} mean {base_mean} -> {cand_mean}"
        findings.append(RegressionFinding(
            dimension=f"{signal}_mean", baseline_value=base_mean, candidate_value=cand_mean,
            delta=delta, severity="informational", message=message,
        ))
    return findings


def _compare_categorical(
    baseline_counts: dict[str, int],
    candidate_counts: dict[str, int],
    label: str,
    lost_is_failure: bool,
) -> list[RegressionFinding]:
    """Diff two name->episode-count dicts (towns, weather, or routes).

    Args:
        baseline_counts: Baseline's counts.
        candidate_counts: Candidate's counts.
        label: ``"town"`` | ``"weather"`` | ``"route"`` — used as the
            dimension prefix.
        lost_is_failure: If True, a key present in *baseline_counts* with
            count > 0 but absent (or 0) from *candidate_counts* is a
            ``"failure"`` finding rather than a ``"warning"`` — the
            ``town_or_weather_cell_lost`` hard trigger (docs/ADR/0007
            Decision 4).

    Returns:
        One finding per key whose count changed, added, or was removed.
        Unchanged keys produce no finding.
    """
    findings: list[RegressionFinding] = []
    for key in sorted(set(baseline_counts) | set(candidate_counts)):
        base = baseline_counts.get(key, 0)
        cand = candidate_counts.get(key, 0)
        if base == cand:
            continue
        if base == 0 and cand > 0:
            severity = "improvement"
            message = f"{label} {key!r} added ({cand} episode(s))"
        elif base > 0 and cand == 0:
            severity = "failure" if lost_is_failure else "warning"
            message = f"{label} {key!r} removed (had {base} episode(s))"
        else:
            severity = "informational"
            message = f"{label} {key!r} episode count {base} -> {cand}"
        findings.append(RegressionFinding(
            dimension=f"{label}:{key}", baseline_value=base, candidate_value=cand,
            delta=float(cand - base), severity=severity, message=message,
        ))
    return findings


def _route_counts(artifact: DatasetArtifact) -> dict[str, int]:
    """Return included-episode counts keyed by route name.

    Args:
        artifact: The dataset to tally.

    Returns:
        Mapping of route name to included-episode count. Episodes with no
        recorded route name are omitted.
    """
    counts: dict[str, int] = {}
    for episode in artifact.episodes:
        if episode.included and episode.route_name is not None:
            counts[episode.route_name] = counts.get(episode.route_name, 0) + 1
    return counts
