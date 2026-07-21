"""
src/quality/gates.py — Pass/fail training-readiness gate.

Composition-only (docs/ADR/0004-engineering-loop-architecture.md Decision
2): every check reads from :mod:`src.quality.scoring`,
:mod:`src.quality.coverage`, and (optionally) :mod:`src.quality.regression`
output already computed into a shared :class:`GateContext` — no check
recomputes a score or re-derives coverage itself.

The check list (:data:`DATASET_GATE_CHECKS`) is ADR-0004's Extension Point
3: Phase 4 appends model-readiness checks (e.g. a
:func:`src.quality.lineage.evaluate_lineage_check`-backed "checkpoint
trained on this exact dataset version" check) to the same list, without
changing :func:`evaluate_gate`'s control flow.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from src.quality.artifacts import DatasetArtifact
from src.quality.config import QualityEngineeringConfig
from src.quality.coverage import compute_coverage
from src.quality.regression import compare_datasets
from src.quality.schemas import (
    QUALITY_SCHEMA_VERSION,
    CoverageResult,
    GateCheckResult,
    GateReport,
    QualityScore,
    RegressionReport,
)
from src.quality.scoring import compute_quality_score

#: Default filename written into a dataset directory by :func:`write_gate_report`.
GATE_REPORT_FILENAME = "gate_report.json"

#: A regression finding's severity is "blocking" for a given configured
#: ``block_on_regression_severity`` tier if it appears in this tier's set —
#: e.g. configuring ``"warning"`` blocks on both warnings and failures,
#: while ``"failure"`` blocks only on failures.
_BLOCKING_SEVERITIES: dict[str, frozenset[str]] = {
    "warning": frozenset({"warning", "failure"}),
    "failure": frozenset({"failure"}),
}


@dataclasses.dataclass
class GateContext:
    """Shared input every :data:`GateCheck` reads from — never recomputed per check.

    Args:
        artifact: The dataset being gated.
        score: Its :class:`~src.quality.schemas.QualityScore`.
        coverage: Its :class:`~src.quality.schemas.CoverageResult`.
        regression: Its :class:`~src.quality.schemas.RegressionReport`
            against ``baseline``, or None if no baseline was given.
        cfg: Resolved engineering-loop configuration.
    """

    artifact: DatasetArtifact
    score: QualityScore
    coverage: CoverageResult
    regression: RegressionReport | None
    cfg: QualityEngineeringConfig


#: A named gate check: takes the shared :class:`GateContext`, returns one
#: :class:`~src.quality.schemas.GateCheckResult`. Takes a context object
#: rather than a fixed parameter list so future checks (e.g. Phase 4's
#: lineage-aware check) can read additional context fields without
#: changing this type's signature — docs/PHASE3_5_DESIGN_REVIEW.md's
#: "every interface takes a context object" risk mitigation.
GateCheck = Callable[[GateContext], GateCheckResult]


def check_sample_count_nonzero(ctx: GateContext) -> GateCheckResult:
    """Fail if the dataset has zero samples — a score is not trustworthy on an empty dataset.

    docs/ADR/0005-quality-scoring-strategy.md Decision 1's consequence:
    every metric except ``coverage`` reports 100 on an empty dataset, so
    the gate must check ``sample_count > 0`` independently of the score
    before trusting it at all.
    """
    sample_count = ctx.artifact.manifest.sample_count
    return GateCheckResult(
        name="sample_count_nonzero",
        passed=sample_count > 0,
        detail=f"sample_count={sample_count}",
    )


def check_min_quality_score(ctx: GateContext) -> GateCheckResult:
    """Fail if the overall weighted quality score is below the configured floor."""
    minimum = ctx.cfg.gates.min_quality_score
    return GateCheckResult(
        name="min_quality_score",
        passed=ctx.score.overall_score >= minimum,
        detail=f"overall_score={ctx.score.overall_score:.2f}, minimum={minimum:.2f}",
    )


def check_min_coverage_score(ctx: GateContext) -> GateCheckResult:
    """Fail if the ``coverage`` sub-metric is below its configured floor.

    A hard per-metric floor, independent of the blended overall score —
    docs/ADR/0005-quality-scoring-strategy.md Decision 4: a weighted mean
    can hide one genuinely poor metric behind others that are high, so
    gates.py enforces floors the blended grade alone cannot.
    """
    minimum = ctx.cfg.gates.min_coverage_score
    result = ctx.score.metrics.get("coverage")
    raw = result.raw_score if result is not None else 0.0
    return GateCheckResult(
        name="min_coverage_score",
        passed=raw >= minimum,
        detail=f"coverage_score={raw:.2f}, minimum={minimum:.2f}",
    )


def check_min_steering_balance_score(ctx: GateContext) -> GateCheckResult:
    """Fail if the ``steering_balance`` sub-metric is below its configured floor."""
    minimum = ctx.cfg.gates.min_steering_balance_score
    result = ctx.score.metrics.get("steering_balance")
    raw = result.raw_score if result is not None else 0.0
    return GateCheckResult(
        name="min_steering_balance_score",
        passed=raw >= minimum,
        detail=f"steering_balance_score={raw:.2f}, minimum={minimum:.2f}",
    )


def check_regression(ctx: GateContext) -> GateCheckResult:
    """Fail if any regression finding meets or exceeds the configured blocking severity.

    A missing baseline (``ctx.regression is None``) passes unless
    ``cfg.gates.require_regression_baseline`` is True — the first dataset
    ever built should not fail its own gate for lack of history.
    """
    if ctx.regression is None:
        if ctx.cfg.gates.require_regression_baseline:
            return GateCheckResult(
                name="regression",
                passed=False,
                detail="no regression baseline available and one is required",
            )
        return GateCheckResult(
            name="regression",
            passed=True,
            detail="no regression baseline available (not required)",
        )

    blocking = _BLOCKING_SEVERITIES.get(
        ctx.cfg.gates.block_on_regression_severity, frozenset({"failure"}),
    )
    blockers = [f for f in ctx.regression.findings if f.severity in blocking]
    if blockers:
        names = ", ".join(f"{f.dimension} ({f.severity})" for f in blockers)
        return GateCheckResult(
            name="regression", passed=False, detail=f"blocking regression finding(s): {names}",
        )
    return GateCheckResult(
        name="regression",
        passed=True,
        detail=f"no finding at or above blocking severity "
        f"({ctx.cfg.gates.block_on_regression_severity!r})",
    )


#: Every check run by :func:`evaluate_gate`, in a fixed order — matches
#: ADR-0004 Extension Point 3: Phase 4 appends to this tuple, never edits
#: an existing entry's meaning.
DATASET_GATE_CHECKS: tuple[GateCheck, ...] = (
    check_sample_count_nonzero,
    check_min_quality_score,
    check_min_coverage_score,
    check_min_steering_balance_score,
    check_regression,
)


def evaluate_gate(
    artifact: DatasetArtifact,
    cfg: QualityEngineeringConfig,
    *,
    baseline: DatasetArtifact | None = None,
) -> GateReport:
    """Run every check in :data:`DATASET_GATE_CHECKS` against *artifact*.

    Args:
        artifact: The dataset to gate.
        cfg: Resolved engineering-loop configuration.
        baseline: An optional prior dataset — when given, the
            ``"regression"`` check compares against it; when omitted, that
            check passes unless ``cfg.gates.require_regression_baseline``.

    Returns:
        A :class:`~src.quality.schemas.GateReport` — ``passed`` is True
        only if every check passed.
    """
    score = compute_quality_score(artifact, cfg)
    coverage = compute_coverage(artifact, cfg)
    regression = compare_datasets(baseline, artifact, cfg) if baseline is not None else None

    ctx = GateContext(
        artifact=artifact, score=score, coverage=coverage, regression=regression, cfg=cfg,
    )
    checks = [check(ctx) for check in DATASET_GATE_CHECKS]

    return GateReport(
        schema_version=QUALITY_SCHEMA_VERSION,
        created_at=datetime.now(tz=timezone.utc).isoformat(),
        artifact_id=artifact.artifact_id,
        passed=all(c.passed for c in checks),
        checks=checks,
    )


def write_gate_report(
    dataset_dir: Path, report: GateReport, filename: str = GATE_REPORT_FILENAME,
) -> Path:
    """Write *report* to ``<dataset_dir>/<filename>``.

    Args:
        dataset_dir: The dataset directory to write into.
        report: The :class:`~src.quality.schemas.GateReport` to persist.
        filename: Output filename, relative to *dataset_dir*.

    Returns:
        The path written to.
    """
    path = Path(dataset_dir) / filename
    path.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    return path
