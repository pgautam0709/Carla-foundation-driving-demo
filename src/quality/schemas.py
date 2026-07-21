"""
src/quality/schemas.py — Data schema definitions for the Phase 3.5 engineering loop.

All records are plain dataclasses that serialize to JSON-compatible dicts
via ``to_dict()`` (thin wrappers around :func:`dataclasses.asdict`), mirroring
the convention already established in :mod:`src.data.dataset_schemas`.
``QUALITY_SCHEMA_VERSION`` is embedded in every artifact this package writes.

``Artifact`` is the one type every other module in this package that needs
to work across artifact kinds (:mod:`src.quality.versioning`,
:mod:`src.quality.lineage`) is written against — see
docs/ADR/0004-engineering-loop-architecture.md Decision 6a. Dataset-specific
code (:mod:`src.quality.scoring`, :mod:`src.quality.coverage`,
:mod:`src.quality.review`) uses the fully-typed
:class:`~src.quality.artifacts.DatasetArtifact` subtype instead.

Schema version history:
    1.0 — Phase 3.5 initial.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

# ── Schema version ─────────────────────────────────────────────────────────────
QUALITY_SCHEMA_VERSION: str = "1.0"


# ── Generic artifact envelope ───────────────────────────────────────────────────

@dataclasses.dataclass
class Artifact:
    """Generic envelope every engineering-loop artifact type extends.

    Args:
        artifact_id: The artifact's own identity, assigned by whatever
            produced it (``dataset_id`` for a dataset, a checkpoint name
            for a future model artifact, etc.).
        artifact_type: ``"dataset"`` today; ``"model"`` | ``"evaluation"``
            | ``"deployment"`` from Phase 4 onward.
        artifact_dir: Directory containing the artifact's own files plus
            any ``version.json`` / ``quality_score.json`` / etc. this
            package writes alongside them.
        created_at: ISO 8601 UTC timestamp the artifact itself was
            created, or None if unknown.
        git_commit: Short git HEAD hash at creation time, or None.
    """

    artifact_id: str
    artifact_type: str
    artifact_dir: Path
    created_at: str | None
    git_commit: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (``artifact_dir`` as a string)."""
        d = dataclasses.asdict(self)
        d["artifact_dir"] = str(self.artifact_dir)
        return d


# ── Metrics / scoring ────────────────────────────────────────────────────────────

@dataclasses.dataclass
class MetricResult:
    """Result of computing one registered :class:`~src.quality.metrics.Metric`.

    Args:
        name: The metric's own identifier (matches its registry key).
        raw_score: Normalized score in ``[0, 100]``.
        weight: The configured weight applied to this metric when
            combined into an overall score (see
            :mod:`src.quality.scoring`). Recorded here, not just in the
            caller's weight table, so ``quality_score.json`` is
            self-explaining without cross-referencing config.
        detail: Human-readable explanation of how the score was derived.
    """

    name: str
    raw_score: float
    weight: float
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class QualityScore:
    """Full quality score written to ``quality_score.json``.

    Args:
        schema_version: Always :data:`QUALITY_SCHEMA_VERSION`.
        created_at: ISO 8601 UTC timestamp when the score was computed.
        artifact_id: The artifact this score describes.
        overall_score: Weighted-mean score in ``[0, 100]``.
        grade: Letter grade (``"A"``..``"F"``) from the configured
            thresholds.
        metrics: Every registered metric's result, keyed by metric name.
        weights_used: The (normalized) weights applied, keyed by metric
            name — recorded so a later reviewer can tell whether a grade
            change reflects the data or the scoring rubric.
        grade_thresholds_used: The grade boundaries applied.
    """

    schema_version: str
    created_at: str
    artifact_id: str
    overall_score: float
    grade: str
    metrics: dict[str, MetricResult]
    weights_used: dict[str, float]
    grade_thresholds_used: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (nested metrics expanded)."""
        return dataclasses.asdict(self)


# ── Versioning / lineage ─────────────────────────────────────────────────────────

@dataclasses.dataclass
class LineageEdge:
    """One derivation edge: "this artifact was produced from that one."

    Args:
        parent_artifact_type: The parent's artifact type — may differ
            from the child's (e.g. a model artifact's parent is a
            dataset artifact).
        parent_artifact_id: The parent's own identity.
        relation: Human-readable label for the edge, e.g. ``"trained_on"``,
            ``"evaluated"``, ``"exported_from"``.
    """

    parent_artifact_type: str
    parent_artifact_id: str
    relation: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class VersionRecord:
    """Release record written to ``version.json``, generic across artifact types.

    Args:
        schema_version: Always :data:`QUALITY_SCHEMA_VERSION`.
        artifact_type: ``"dataset"`` today; other types from Phase 4 on.
        artifact_id: Unchanged identity from the artifact's own producer.
        created_at: ISO 8601 UTC timestamp when versioning was run (may be
            later than the artifact's own build timestamp).
        git_commit: Short git HEAD hash, or None.
        config_hash: Hash of only the configuration section(s) that are
            actual inputs to this artifact's build (see ADR-0006
            Decision 3) — never the whole config file.
        content_hashes: Named hash per artifact-defining file, e.g.
            ``{"manifest": ..., "statistics": ..., "quality_report": ...}``
            for a dataset.
        generator_version: :data:`src.quality.__version__` at the time
            this record was written.
        summary_counts: Named counts, e.g. ``{"sample_count": ...,
            "episode_count": ...}`` for a dataset.
        previous_artifact_id: Prior version of this same artifact (same
            type), or None if this is the first. Distinct from
            ``lineage_parents`` — see docs/ADR/0011.
        lineage_parents: Artifacts this one was derived from, possibly of
            a different type (docs/ADR/0011).
    """

    schema_version: str
    artifact_type: str
    artifact_id: str
    created_at: str
    git_commit: str | None
    config_hash: str
    content_hashes: dict[str, str]
    generator_version: str
    summary_counts: dict[str, int]
    previous_artifact_id: str | None
    lineage_parents: list[LineageEdge]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (nested lineage edges expanded)."""
        return dataclasses.asdict(self)


# ── Regression ─────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class RegressionFinding:
    """One comparison dimension's result.

    Args:
        dimension: Name of the compared dimension, e.g. ``"sample_count"``,
            ``"quality_score.coverage"``, ``"town:Town10"``.
        baseline_value: The baseline artifact's value for this dimension.
        candidate_value: The candidate artifact's value for this dimension.
        delta: ``candidate - baseline`` where numeric; None otherwise.
        severity: ``"improvement"`` | ``"warning"`` | ``"failure"`` |
            ``"informational"``.
        message: Human-readable description.
    """

    dimension: str
    baseline_value: Any
    candidate_value: Any
    delta: float | None
    severity: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class RegressionReport:
    """Full comparison report written to ``regression_report.json``.

    Args:
        schema_version: Always :data:`QUALITY_SCHEMA_VERSION`.
        created_at: ISO 8601 UTC timestamp when the comparison was run.
        artifact_type: The artifact type being compared (``"dataset"``
            today).
        baseline_artifact_id: The baseline artifact's ID, or None if no
            baseline was available.
        candidate_artifact_id: The candidate artifact's ID.
        findings: Every :class:`RegressionFinding`, in a fixed dimension
            order.
    """

    schema_version: str
    created_at: str
    artifact_type: str
    baseline_artifact_id: str | None
    candidate_artifact_id: str
    findings: list[RegressionFinding]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (nested findings expanded)."""
        return dataclasses.asdict(self)


# ── Coverage ───────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class CoverageCell:
    """One ``(town, weather)`` cell of the coverage target matrix.

    Args:
        town: CARLA map name.
        weather: CARLA weather preset name.
        episode_count: Included episodes matching this cell.
        met: True if ``episode_count >= min_episodes_per_cell``.
    """

    town: str
    weather: str
    episode_count: int
    met: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class CoverageResult:
    """Full coverage report written to ``coverage_report.json``.

    Args:
        schema_version: Always :data:`QUALITY_SCHEMA_VERSION`.
        created_at: ISO 8601 UTC timestamp when coverage was computed.
        artifact_id: The dataset this report describes.
        target_towns: The configured target town list.
        target_weather: The configured target weather list.
        min_episodes_per_cell: The configured per-cell minimum.
        cells: Every cell in the target matrix (Cartesian product of
            ``target_towns x target_weather``).
        cells_met: Number of cells with ``met is True``.
        cells_total: Total number of cells (``len(target_towns) *
            len(target_weather)``).
        coverage_pct: ``100 * cells_met / cells_total`` (0.0 if
            ``cells_total`` is 0).
        routes: Informational per-route episode counts — not part of the
            gated matrix (see ADR-0008 Decision 1's rejected alternative).
        split_coverage: Informational per-split town/weather episode
            counts, keyed ``split -> "town|weather" -> count``.
    """

    schema_version: str
    created_at: str
    artifact_id: str
    target_towns: list[str]
    target_weather: list[str]
    min_episodes_per_cell: int
    cells: list[CoverageCell]
    cells_met: int
    cells_total: int
    coverage_pct: float
    routes: dict[str, int]
    split_coverage: dict[str, dict[str, int]]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (nested cells expanded)."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class CoverageRecommendation:
    """One recommended cell to collect more data for.

    Args:
        town: CARLA map name.
        weather: CARLA weather preset name.
        current_episode_count: Episodes currently in this cell.
        gap: ``min_episodes_per_cell - current_episode_count`` (clamped
            to >= 0; 0 for a zero-coverage cell means "meets the floor of
            zero," used only for ranking, never negative).
        message: Human-readable, directly actionable recommendation text.
    """

    town: str
    weather: str
    current_episode_count: int
    gap: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


# ── Review ─────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class ReviewReport:
    """Deterministic engineering review written to ``review.json``.

    Args:
        schema_version: Always :data:`QUALITY_SCHEMA_VERSION`.
        created_at: ISO 8601 UTC timestamp when the review was generated.
        artifact_id: The dataset this review describes.
        stars: Integer star rating in ``[1, 5]``, derived deterministically
            from ``overall_score``.
        overall_score: The quality score this review was generated from.
        grade: The letter grade this review was generated from.
        strengths: Rule-based list of what's good about this dataset.
        weaknesses: Rule-based list of what's missing or weak.
        recommendations: Directly actionable next steps (from the
            coverage planner).
    """

    schema_version: str
    created_at: str
    artifact_id: str
    stars: int
    overall_score: float
    grade: str
    strengths: list[str]
    weaknesses: list[str]
    recommendations: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


# ── Gates ──────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class GateCheckResult:
    """Result of one named training-gate check.

    Args:
        name: Check identifier, e.g. ``"quality_threshold"``.
        passed: True if the check passed.
        detail: Human-readable explanation.
    """

    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class GateReport:
    """Full gate verdict written to ``gate_report.json``.

    Args:
        schema_version: Always :data:`QUALITY_SCHEMA_VERSION`.
        created_at: ISO 8601 UTC timestamp when the gate was evaluated.
        artifact_id: The dataset this verdict describes.
        passed: True only if every check passed.
        checks: Every configured :class:`GateCheckResult`, in a fixed
            order — rendered verbatim by the dashboard, including
            failures, per ADR-0009 Decision 5.
    """

    schema_version: str
    created_at: str
    artifact_id: str
    passed: bool
    checks: list[GateCheckResult]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (nested checks expanded)."""
        return dataclasses.asdict(self)
