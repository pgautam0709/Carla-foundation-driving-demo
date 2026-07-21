"""
src/quality/review.py — Deterministic star review: strengths, weaknesses, recommendations.

Composition-only (docs/ADR/0004-engineering-loop-architecture.md Decision
2): every fact in a :class:`~src.quality.schemas.ReviewReport` comes from
:mod:`src.quality.scoring`, :mod:`src.quality.coverage`, or (when a
baseline is given) :mod:`src.quality.regression` — this module computes no
scores of its own, only narrates and buckets numbers those modules already
produced.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.quality.artifacts import DatasetArtifact
from src.quality.config import QualityEngineeringConfig
from src.quality.coverage import compute_coverage, recommend_collection
from src.quality.regression import compare_datasets
from src.quality.schemas import QUALITY_SCHEMA_VERSION, CoverageResult, QualityScore, ReviewReport
from src.quality.scoring import compute_quality_score

#: Default filename written into a dataset directory by :func:`write_review`.
REVIEW_REPORT_FILENAME = "review.json"

#: Deterministic letter-grade -> star mapping. Reuses
#: :class:`~src.quality.config.ScoringConfig`'s ``grade_thresholds`` (the
#: same numbers that already determine the letter grade) rather than
#: introducing a second, independently-tunable set of score thresholds —
#: docs/ADR/0005-quality-scoring-strategy.md Decision 4's "one place to
#: tune" principle.
_STARS_BY_GRADE: dict[str, int] = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}


def compute_review(
    artifact: DatasetArtifact,
    cfg: QualityEngineeringConfig,
    *,
    baseline: DatasetArtifact | None = None,
) -> ReviewReport:
    """Compute a deterministic engineering review for *artifact*.

    Args:
        artifact: The dataset to review.
        cfg: Resolved engineering-loop configuration.
        baseline: An optional prior dataset to compare against — when
            given, regression findings with severity ``"improvement"``
            become strengths and ``"warning"``/``"failure"`` findings
            become weaknesses, in addition to the score- and
            coverage-derived ones.

    Returns:
        A :class:`~src.quality.schemas.ReviewReport`.
    """
    score = compute_quality_score(artifact, cfg)
    coverage = compute_coverage(artifact, cfg)
    recommendations = recommend_collection(coverage, cfg)

    strengths = _derive_strengths(score, coverage, cfg)
    weaknesses = _derive_weaknesses(score, coverage, cfg)

    if baseline is not None:
        regression = compare_datasets(baseline, artifact, cfg)
        for finding in regression.findings:
            if finding.severity == "improvement":
                strengths.append(f"Improved since {baseline.artifact_id}: {finding.message}")
            elif finding.severity in ("warning", "failure"):
                weaknesses.append(f"Regressed since {baseline.artifact_id}: {finding.message}")

    return ReviewReport(
        schema_version=QUALITY_SCHEMA_VERSION,
        created_at=datetime.now(tz=timezone.utc).isoformat(),
        artifact_id=artifact.artifact_id,
        stars=_stars_for_grade(score.grade),
        overall_score=score.overall_score,
        grade=score.grade,
        strengths=strengths,
        weaknesses=weaknesses,
        recommendations=[rec.message for rec in recommendations],
    )


def write_review(
    dataset_dir: Path, review: ReviewReport, filename: str = REVIEW_REPORT_FILENAME,
) -> Path:
    """Write *review* to ``<dataset_dir>/<filename>``.

    Args:
        dataset_dir: The dataset directory to write into.
        review: The :class:`~src.quality.schemas.ReviewReport` to persist.
        filename: Output filename, relative to *dataset_dir*.

    Returns:
        The path written to.
    """
    path = Path(dataset_dir) / filename
    path.write_text(json.dumps(review.to_dict(), indent=2, default=str), encoding="utf-8")
    return path


# ── Internal helpers ───────────────────────────────────────────────────────────

def _stars_for_grade(grade: str) -> int:
    """Map a letter grade to a ``[1, 5]`` star count.

    Args:
        grade: A letter grade from :func:`src.quality.scoring.compute_quality_score`.

    Returns:
        The mapped star count, or 1 if *grade* is not a recognized letter
        (defensive — every configured grade threshold maps to a known
        letter today).
    """
    return _STARS_BY_GRADE.get(grade, 1)


def _derive_strengths(
    score: QualityScore, coverage: CoverageResult, cfg: QualityEngineeringConfig,
) -> list[str]:
    """Collect every metric and coverage fact worth calling out as a strength.

    Args:
        score: The dataset's :class:`~src.quality.schemas.QualityScore`.
        coverage: The dataset's :class:`~src.quality.schemas.CoverageResult`.
        cfg: Resolved engineering-loop configuration (uses ``cfg.review``).

    Returns:
        Human-readable strength statements, one per metric scoring at or
        above ``cfg.review.strength_threshold``, plus a coverage-specific
        statement when the target matrix is fully met.
    """
    threshold = cfg.review.strength_threshold
    strengths = [
        f"{name}: {result.detail}"
        for name, result in sorted(score.metrics.items())
        if result.raw_score >= threshold
    ]
    if coverage.cells_total > 0 and coverage.cells_met == coverage.cells_total:
        strengths.append(
            f"Full target coverage: all {coverage.cells_total} (town, weather) cells met"
        )
    return strengths


def _derive_weaknesses(
    score: QualityScore, coverage: CoverageResult, cfg: QualityEngineeringConfig,
) -> list[str]:
    """Collect every metric and coverage fact worth calling out as a weakness.

    Args:
        score: The dataset's :class:`~src.quality.schemas.QualityScore`.
        coverage: The dataset's :class:`~src.quality.schemas.CoverageResult`.
        cfg: Resolved engineering-loop configuration (uses ``cfg.review``).

    Returns:
        Human-readable weakness statements, one per metric scoring below
        ``cfg.review.weakness_threshold``, plus a coverage-gap statement
        when the target matrix is not fully met.
    """
    threshold = cfg.review.weakness_threshold
    weaknesses = [
        f"{name}: {result.detail}"
        for name, result in sorted(score.metrics.items())
        if result.raw_score < threshold
    ]
    if coverage.cells_total > 0 and coverage.cells_met < coverage.cells_total:
        weaknesses.append(
            f"Coverage gap: only {coverage.cells_met}/{coverage.cells_total} target "
            "(town, weather) cells met"
        )
    return weaknesses
