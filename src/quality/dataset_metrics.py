"""
src/quality/dataset_metrics.py — Concrete dataset-level quality metrics.

Six metrics, each a direct, explainable function of fields already present
in ``quality_report.json`` / ``stats.json`` (no raw episode re-reading), each
normalized to ``[0, 100]``, registered under category ``"dataset"`` into
:data:`~src.quality.metrics.METRIC_REGISTRY` at import time — see
docs/ADR/0005-quality-scoring-strategy.md.
"""

from __future__ import annotations

import math

from src.quality.artifacts import DatasetArtifact
from src.quality.config import QualityEngineeringConfig
from src.quality.coverage import compute_coverage
from src.quality.metrics import METRIC_REGISTRY, Metric
from src.quality.schemas import Artifact, MetricResult

#: Category every metric in this module registers under.
CATEGORY = "dataset"


def _configured_weight(name: str, cfg: QualityEngineeringConfig) -> float:
    """Return the configured (un-normalized) weight for metric *name*.

    Args:
        name: The metric's registry name.
        cfg: Resolved engineering-loop configuration.

    Returns:
        The configured weight, or 0.0 if not present in
        ``cfg.scoring.weights``.
    """
    return cfg.scoring.weights.get(name, 0.0)


def _as_dataset_artifact(artifact: Artifact, metric_name: str) -> DatasetArtifact:
    """Narrow *artifact* to :class:`DatasetArtifact`, per the category convention.

    Args:
        artifact: The artifact passed to a ``"dataset"``-category metric.
        metric_name: The calling metric's name, for the error message.

    Returns:
        *artifact*, narrowed to :class:`DatasetArtifact`.

    Raises:
        TypeError: If *artifact* is not a :class:`DatasetArtifact` —
            indicates a caller registered this metric under the wrong
            category, or queried the wrong category (docs/ADR/0004
            Decision 6b's isinstance-narrowing convention).
    """
    if not isinstance(artifact, DatasetArtifact):
        raise TypeError(
            f"{metric_name!r} metric requires a DatasetArtifact, got {type(artifact).__name__}"
        )
    return artifact


class SynchronizationMetric(Metric):
    """Fraction of scanned episodes with perfect frame/control/telemetry alignment."""

    name = "synchronization"

    def compute(self, artifact: Artifact, cfg: QualityEngineeringConfig) -> MetricResult:
        """See :meth:`src.quality.metrics.Metric.compute`."""
        ds = _as_dataset_artifact(artifact, self.name)
        report = ds.quality_report
        if report.episodes_scanned == 0:
            score, detail = 100.0, "no episodes scanned"
        else:
            aligned = report.episodes_scanned - report.episodes_misaligned
            score = 100.0 * aligned / report.episodes_scanned
            detail = (
                f"{aligned}/{report.episodes_scanned} episodes fully aligned "
                f"({report.episodes_misaligned} misaligned)"
            )
        return MetricResult(
            name=self.name, raw_score=score, weight=_configured_weight(self.name, cfg),
            detail=detail,
        )


class CoverageMetric(Metric):
    """Percentage of the configured town x weather target matrix that is met.

    Delegates entirely to :func:`src.quality.coverage.compute_coverage` —
    this metric never recomputes cell coverage itself (docs/ADR/0005
    Decision 1 consequence).
    """

    name = "coverage"

    def compute(self, artifact: Artifact, cfg: QualityEngineeringConfig) -> MetricResult:
        """See :meth:`src.quality.metrics.Metric.compute`."""
        ds = _as_dataset_artifact(artifact, self.name)
        coverage = compute_coverage(ds, cfg)
        detail = f"{coverage.cells_met}/{coverage.cells_total} target (town, weather) cells met"
        return MetricResult(
            name=self.name, raw_score=coverage.coverage_pct,
            weight=_configured_weight(self.name, cfg), detail=detail,
        )


class MetadataMetric(Metric):
    """Fraction of scanned episodes that passed :class:`~src.data.validation.EpisodeValidator`."""

    name = "metadata"

    def compute(self, artifact: Artifact, cfg: QualityEngineeringConfig) -> MetricResult:
        """See :meth:`src.quality.metrics.Metric.compute`."""
        ds = _as_dataset_artifact(artifact, self.name)
        report = ds.quality_report
        if report.episodes_scanned == 0:
            score, detail = 100.0, "no episodes scanned"
        else:
            score = 100.0 * report.episodes_valid / report.episodes_scanned
            detail = f"{report.episodes_valid}/{report.episodes_scanned} episodes passed validation"
        return MetricResult(
            name=self.name, raw_score=score, weight=_configured_weight(self.name, cfg),
            detail=detail,
        )


class OutlierMetric(Metric):
    """Fraction of included episodes with no steering-spike / stuck-throttle findings."""

    name = "outliers"

    def compute(self, artifact: Artifact, cfg: QualityEngineeringConfig) -> MetricResult:
        """See :meth:`src.quality.metrics.Metric.compute`."""
        ds = _as_dataset_artifact(artifact, self.name)
        report = ds.quality_report
        if report.episodes_included == 0:
            score, detail = 100.0, "no episodes included"
        else:
            clean = report.episodes_included - report.episodes_with_outliers
            score = 100.0 * clean / report.episodes_included
            detail = (
                f"{report.episodes_with_outliers}/{report.episodes_included} included episodes "
                "flagged with steering-spike or stuck-throttle findings"
            )
        return MetricResult(
            name=self.name, raw_score=score, weight=_configured_weight(self.name, cfg),
            detail=detail,
        )


class DuplicateMetric(Metric):
    """Fraction of samples not belonging to an exact duplicate-frame group."""

    name = "duplicates"

    def compute(self, artifact: Artifact, cfg: QualityEngineeringConfig) -> MetricResult:
        """See :meth:`src.quality.metrics.Metric.compute`."""
        ds = _as_dataset_artifact(artifact, self.name)
        report = ds.quality_report
        sample_count = ds.manifest.sample_count
        if sample_count == 0:
            score, detail = 100.0, "no samples"
        else:
            score = 100.0 * (1 - report.duplicate_sample_count / sample_count)
            detail = (
                f"{report.duplicate_sample_count}/{sample_count} samples belong to an exact "
                f"duplicate-frame group ({report.duplicate_frame_groups} group(s))"
            )
        return MetricResult(
            name=self.name, raw_score=score, weight=_configured_weight(self.name, cfg),
            detail=detail,
        )


class SteeringBalanceMetric(Metric):
    """Normalized Shannon entropy of the steering-angle histogram.

    100 = perfectly uniform across bins, 0 = all samples in one bin —
    shape-agnostic on purpose (docs/ADR/0005-quality-scoring-strategy.md
    Decision 3): this does not assert what a "good" steering distribution
    looks like, only how concentrated the observed one is.
    """

    name = "steering_balance"

    def compute(self, artifact: Artifact, cfg: QualityEngineeringConfig) -> MetricResult:
        """See :meth:`src.quality.metrics.Metric.compute`."""
        ds = _as_dataset_artifact(artifact, self.name)
        histogram = ds.stats.steering_histogram
        total = sum(b.count for b in histogram)

        if total == 0:
            score, entropy_detail = 100.0, "no samples"
        elif len(histogram) <= 1:
            score, entropy_detail = 0.0, "single-bin histogram (no balance possible)"
        else:
            entropy = -sum(
                (b.count / total) * math.log(b.count / total) for b in histogram if b.count > 0
            )
            max_entropy = math.log(len(histogram))
            score = 100.0 * entropy / max_entropy if max_entropy > 0 else 100.0
            score = max(0.0, min(100.0, score))  # clamp -0.0 / float rounding to [0, 100]
            entropy_detail = f"entropy {entropy:.3f} / max {max_entropy:.3f} nats"

        label = _qualitative_label(score, cfg)
        return MetricResult(
            name=self.name, raw_score=score, weight=_configured_weight(self.name, cfg),
            detail=f"{entropy_detail} — {label}",
        )


def _qualitative_label(score: float, cfg: QualityEngineeringConfig) -> str:
    """Map a steering-balance score to its configured qualitative label.

    Args:
        score: The metric's raw score in ``[0, 100]``.
        cfg: Resolved engineering-loop configuration.

    Returns:
        The highest-threshold label *score* meets, or ``"Poor"`` if none.
    """
    thresholds = cfg.scoring.steering_balance_qualitative_thresholds
    for label, minimum in sorted(thresholds.items(), key=lambda item: -item[1]):
        if score >= minimum:
            return label
    return "Poor"


#: Every concrete metric this module provides, in registration order.
_DATASET_METRICS: tuple[type[Metric], ...] = (
    SynchronizationMetric,
    CoverageMetric,
    MetadataMetric,
    OutlierMetric,
    DuplicateMetric,
    SteeringBalanceMetric,
)


def register_dataset_metrics() -> None:
    """Idempotently register every dataset metric under category ``"dataset"``.

    Safe to call more than once — already-registered metric names are
    skipped rather than raising, so re-importing this module (e.g. under
    ``importlib.reload`` in tests) does not fail.
    """
    already = {metric.name for metric in METRIC_REGISTRY.all(CATEGORY)}
    for metric_cls in _DATASET_METRICS:
        if metric_cls.name not in already:
            METRIC_REGISTRY.register(CATEGORY, metric_cls())


register_dataset_metrics()
