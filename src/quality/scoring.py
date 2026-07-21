"""
src/quality/scoring.py — Weighted quality score and letter grade.

Combines every registered ``"dataset"``-category metric
(:mod:`src.quality.dataset_metrics`) into one overall score via a
configurable weighted mean (never a weakest-link minimum — see
docs/ADR/0005-quality-scoring-strategy.md Decision 4) and a letter grade
from configured thresholds. This module contains no metric-specific
scoring math of its own — see docs/ADR/0004 Decision 2's
composition-only rule, though scoring.py sits just above the metrics
layer and does perform the weighting/aggregation math itself, which is
its own single responsibility.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# Imported for its module-level registration side effect (registers all
# six dataset metrics into METRIC_REGISTRY under category "dataset") —
# see src.quality.dataset_metrics.register_dataset_metrics().
from src.quality import dataset_metrics  # noqa: F401
from src.quality.artifacts import DatasetArtifact
from src.quality.config import QualityEngineeringConfig
from src.quality.metrics import METRIC_REGISTRY
from src.quality.schemas import QUALITY_SCHEMA_VERSION, MetricResult, QualityScore

#: Default filename written into a dataset directory by
#: :func:`write_quality_score`.
QUALITY_SCORE_FILENAME = "quality_score.json"


def compute_quality_score(
    artifact: DatasetArtifact, cfg: QualityEngineeringConfig,
) -> QualityScore:
    """Compute the overall weighted quality score for *artifact*.

    Args:
        artifact: The dataset to score.
        cfg: Resolved engineering-loop configuration (uses ``cfg.scoring``).

    Returns:
        A :class:`~src.quality.schemas.QualityScore` with every registered
        ``"dataset"``-category metric's result and the derived overall
        score and grade.
    """
    results: dict[str, MetricResult] = {
        metric.name: metric.compute(artifact, cfg) for metric in METRIC_REGISTRY.all("dataset")
    }

    total_weight = sum(result.weight for result in results.values())
    if total_weight > 0:
        normalized_weights = {
            name: result.weight / total_weight for name, result in results.items()
        }
    elif results:
        normalized_weights = {name: 1.0 / len(results) for name in results}
    else:
        normalized_weights = {}

    overall = sum(
        results[name].raw_score * weight for name, weight in normalized_weights.items()
    )
    grade = _grade_for_score(overall, cfg.scoring.grade_thresholds)

    return QualityScore(
        schema_version=QUALITY_SCHEMA_VERSION,
        created_at=datetime.now(tz=timezone.utc).isoformat(),
        artifact_id=artifact.artifact_id,
        overall_score=overall,
        grade=grade,
        metrics=results,
        weights_used=normalized_weights,
        grade_thresholds_used=dict(cfg.scoring.grade_thresholds),
    )


def write_quality_score(
    dataset_dir: Path, score: QualityScore, filename: str = QUALITY_SCORE_FILENAME,
) -> Path:
    """Write *score* to ``<dataset_dir>/<filename>``.

    Args:
        dataset_dir: The dataset directory to write into.
        score: The :class:`~src.quality.schemas.QualityScore` to persist.
        filename: Output filename, relative to *dataset_dir*.

    Returns:
        The path written to.
    """
    path = Path(dataset_dir) / filename
    path.write_text(json.dumps(score.to_dict(), indent=2, default=str), encoding="utf-8")
    return path


def _grade_for_score(score: float, thresholds: dict[str, float]) -> str:
    """Map *score* to the highest-threshold letter grade it meets.

    Args:
        score: Overall score in ``[0, 100]``.
        thresholds: Letter grade -> inclusive minimum score.

    Returns:
        The matching letter grade, or ``"F"`` if *score* is below every
        configured threshold.
    """
    for letter, minimum in sorted(thresholds.items(), key=lambda item: -item[1]):
        if score >= minimum:
            return letter
    return "F"
