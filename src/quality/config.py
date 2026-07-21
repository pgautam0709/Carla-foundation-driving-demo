"""
src/quality/config.py — Typed configuration for the Phase 3.5 engineering loop.

Parses the ``quality_engineering:`` section of the merged config dict
(:func:`src.utils.config.load_config`) into explicit dataclasses. Every
public function elsewhere in :mod:`src.quality` takes its thresholds as one
of these dataclasses, never a raw dict lookup — the same pattern
:class:`~src.data.dataset_outliers.OutlierThresholds` already established
in Phase 3b (see docs/ADR/0004-engineering-loop-architecture.md Decision 5).

No module in :mod:`src.quality` reads ``config/default.yaml`` directly;
every entry point resolves a :class:`QualityEngineeringConfig` once, here.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from src.utils.config import ConfigDict, get_nested

# ── Scoring (ADR-0005) ───────────────────────────────────────────────────────────

#: Metric names scored today — used to validate that every configured
#: weight corresponds to a real, registered metric (see
#: :func:`load_quality_config`).
DATASET_METRIC_NAMES: tuple[str, ...] = (
    "synchronization", "coverage", "metadata", "outliers", "duplicates", "steering_balance",
)

_DEFAULT_SCORING_WEIGHTS: dict[str, float] = {
    "synchronization": 0.25,
    "coverage": 0.20,
    "metadata": 0.20,
    "outliers": 0.15,
    "duplicates": 0.10,
    "steering_balance": 0.10,
}
_DEFAULT_GRADE_THRESHOLDS: dict[str, float] = {"A": 90.0, "B": 80.0, "C": 70.0, "D": 60.0}
_DEFAULT_STEERING_BALANCE_LABELS: dict[str, float] = {"Good": 80.0, "Fair": 60.0}


@dataclasses.dataclass
class ScoringConfig:
    """Weights and grade boundaries for :mod:`src.quality.scoring`.

    Args:
        weights: Metric name -> relative weight. Need not sum to 1 —
            normalized internally, mirroring
            :func:`src.data.dataset_splits.assign_splits`'s existing
            ratio-normalization convention.
        grade_thresholds: Letter grade -> inclusive minimum score.
            Evaluated from the highest threshold down; anything below the
            lowest configured threshold is graded ``"F"``.
        steering_balance_qualitative_thresholds: Qualitative label ->
            inclusive minimum score, used only for the human-readable
            ``steering_balance`` detail string (e.g. ``"Good"``).
    """

    weights: dict[str, float] = dataclasses.field(
        default_factory=lambda: dict(_DEFAULT_SCORING_WEIGHTS)
    )
    grade_thresholds: dict[str, float] = dataclasses.field(
        default_factory=lambda: dict(_DEFAULT_GRADE_THRESHOLDS)
    )
    steering_balance_qualitative_thresholds: dict[str, float] = dataclasses.field(
        default_factory=lambda: dict(_DEFAULT_STEERING_BALANCE_LABELS)
    )


# ── Coverage (ADR-0008) ──────────────────────────────────────────────────────────

_DEFAULT_TARGET_TOWNS: list[str] = ["Town01", "Town02", "Town03", "Town04", "Town05", "Town10"]
_DEFAULT_TARGET_WEATHER: list[str] = [
    "ClearNoon", "CloudyNoon", "WetNoon", "HardRainNoon", "ClearSunset", "ClearNight",
]


@dataclasses.dataclass
class CoverageConfig:
    """Target diversity matrix for :mod:`src.quality.coverage`.

    Args:
        target_towns: Towns that make up the coverage target matrix.
        target_weather: Weather presets that make up the coverage target
            matrix. The full matrix is the Cartesian product of the two.
        min_episodes_per_cell: Episodes required in a ``(town, weather)``
            cell before it counts as "met."
        max_recommendations: Maximum number of recommendations
            :func:`~src.quality.coverage.recommend_collection` returns.
    """

    target_towns: list[str] = dataclasses.field(default_factory=lambda: list(_DEFAULT_TARGET_TOWNS))
    target_weather: list[str] = dataclasses.field(
        default_factory=lambda: list(_DEFAULT_TARGET_WEATHER)
    )
    min_episodes_per_cell: int = 3
    max_recommendations: int = 5


# ── Regression (ADR-0007) ────────────────────────────────────────────────────────

@dataclasses.dataclass
class RegressionThresholds:
    """One severity tier's per-dimension thresholds.

    Args:
        sample_count_drop_pct: Candidate below baseline by more than this
            percent triggers this severity.
        quality_score_drop_pts: Candidate's overall score below baseline's
            by more than this many points triggers this severity.
        duplicate_rate_increase_pct: Candidate's duplicate rate above
            baseline's by more than this many percentage points triggers
            this severity.
        outlier_rate_increase_pct: Candidate's outlier rate above
            baseline's by more than this many percentage points triggers
            this severity.
        town_or_weather_cell_lost: If True, any previously-covered
            town/weather cell dropping to zero episodes triggers this
            severity outright, regardless of the numeric thresholds above.
            Only meaningful set on the failure tier.
    """

    sample_count_drop_pct: float
    quality_score_drop_pts: float
    duplicate_rate_increase_pct: float
    outlier_rate_increase_pct: float
    town_or_weather_cell_lost: bool = False


@dataclasses.dataclass
class RegressionConfig:
    """Two-tier severity thresholds for :mod:`src.quality.regression`.

    Args:
        warning: Thresholds at or beyond which a finding is a "warning."
        failure: Thresholds at or beyond which a finding is a "failure."
    """

    warning: RegressionThresholds = dataclasses.field(
        default_factory=lambda: RegressionThresholds(
            sample_count_drop_pct=10.0, quality_score_drop_pts=5.0,
            duplicate_rate_increase_pct=2.0, outlier_rate_increase_pct=2.0,
        )
    )
    failure: RegressionThresholds = dataclasses.field(
        default_factory=lambda: RegressionThresholds(
            sample_count_drop_pct=40.0, quality_score_drop_pts=15.0,
            duplicate_rate_increase_pct=10.0, outlier_rate_increase_pct=10.0,
            town_or_weather_cell_lost=True,
        )
    )


# ── Review (ADR-0004) ────────────────────────────────────────────────────────────

@dataclasses.dataclass
class ReviewConfig:
    """Strength/weakness call-out thresholds for :mod:`src.quality.review`.

    Args:
        strength_threshold: A metric's ``raw_score`` at or above this
            value is called out as a named strength.
        weakness_threshold: A metric's ``raw_score`` below this value is
            called out as a named weakness. Independent of
            ``strength_threshold`` — a score between the two thresholds
            is simply unremarkable, neither called out.
    """

    strength_threshold: float = 80.0
    weakness_threshold: float = 50.0


# ── Gates (ADR-0004) ─────────────────────────────────────────────────────────────

@dataclasses.dataclass
class GatesConfig:
    """Pass/fail thresholds for :mod:`src.quality.gates`.

    Args:
        min_quality_score: Minimum ``QualityScore.overall_score`` to pass.
        min_coverage_score: Minimum ``coverage`` metric sub-score to pass.
        min_steering_balance_score: Minimum ``steering_balance`` metric
            sub-score to pass.
        block_on_regression_severity: A regression finding at or above
            this severity blocks the gate. One of ``"failure"`` |
            ``"warning"``.
        require_regression_baseline: If True, a missing regression
            baseline (nothing to compare against) fails the regression
            check. If False (default), a missing baseline is treated as
            "nothing to block on" — the first dataset ever built should
            not fail its own gate for lack of history.
    """

    min_quality_score: float = 70.0
    min_coverage_score: float = 50.0
    min_steering_balance_score: float = 50.0
    block_on_regression_severity: str = "failure"
    require_regression_baseline: bool = False


# ── Dashboard (ADR-0009) ─────────────────────────────────────────────────────────

@dataclasses.dataclass
class DashboardConfig:
    """Output location and trend window for :mod:`src.quality.dashboard`.

    Args:
        output_dir: Directory dashboards are written into.
        trend_window: Maximum number of historical dataset versions shown
            in the Quality Trend section.
    """

    output_dir: str = "outputs/dashboard"
    trend_window: int = 10


# ── Versioning (ADR-0006) ────────────────────────────────────────────────────────

@dataclasses.dataclass
class VersioningConfig:
    """File names for :mod:`src.quality.versioning`'s output.

    Args:
        version_filename: Name of the version record file written into
            each artifact's own directory.
        changelog_filename: Name of the generated changelog file.
    """

    version_filename: str = "version.json"
    changelog_filename: str = "CHANGELOG.md"


# ── Lineage (ADR-0011) ───────────────────────────────────────────────────────────

_DEFAULT_ARTIFACT_ROOTS: dict[str, str] = {
    "dataset": "data/processed/datasets",
    "model": "outputs/training",
    "evaluation": "outputs/evaluation",
    "deployment": "outputs/deployment",
}


@dataclasses.dataclass
class LineageConfig:
    """Where to look for each artifact type's versioned directories.

    Args:
        artifact_roots: Artifact type -> parent directory containing one
            subdirectory per artifact of that type. A root that doesn't
            exist yet (e.g. ``"evaluation"`` before Phase 5 ships) is
            silently skipped by :func:`src.quality.lineage.build_lineage_graph`,
            not treated as an error.
    """

    artifact_roots: dict[str, str] = dataclasses.field(
        default_factory=lambda: dict(_DEFAULT_ARTIFACT_ROOTS)
    )


# ── Top-level bundle ──────────────────────────────────────────────────────────────

@dataclasses.dataclass
class QualityEngineeringConfig:
    """Every ``quality_engineering:`` sub-section, parsed and typed.

    Args:
        scoring: See :class:`ScoringConfig`.
        coverage: See :class:`CoverageConfig`.
        regression: See :class:`RegressionConfig`.
        review: See :class:`ReviewConfig`.
        gates: See :class:`GatesConfig`.
        dashboard: See :class:`DashboardConfig`.
        versioning: See :class:`VersioningConfig`.
        lineage: See :class:`LineageConfig`.
    """

    scoring: ScoringConfig = dataclasses.field(default_factory=ScoringConfig)
    coverage: CoverageConfig = dataclasses.field(default_factory=CoverageConfig)
    regression: RegressionConfig = dataclasses.field(default_factory=RegressionConfig)
    review: ReviewConfig = dataclasses.field(default_factory=ReviewConfig)
    gates: GatesConfig = dataclasses.field(default_factory=GatesConfig)
    dashboard: DashboardConfig = dataclasses.field(default_factory=DashboardConfig)
    versioning: VersioningConfig = dataclasses.field(default_factory=VersioningConfig)
    lineage: LineageConfig = dataclasses.field(default_factory=LineageConfig)


def load_quality_config(cfg: ConfigDict) -> QualityEngineeringConfig:
    """Parse ``cfg["quality_engineering"]`` into a typed :class:`QualityEngineeringConfig`.

    Every key is optional — any key (or the whole section) missing from
    *cfg* falls back to the dataclass defaults above, which match
    ``config/default.yaml``.

    Args:
        cfg: The merged configuration dict from
            :func:`src.utils.config.load_config`.

    Returns:
        A fully-populated :class:`QualityEngineeringConfig`.
    """
    scoring_raw = get_nested(cfg, "quality_engineering", "scoring", default={}) or {}
    coverage_raw = get_nested(cfg, "quality_engineering", "coverage", default={}) or {}
    regression_raw = get_nested(cfg, "quality_engineering", "regression", default={}) or {}
    review_raw = get_nested(cfg, "quality_engineering", "review", default={}) or {}
    gates_raw = get_nested(cfg, "quality_engineering", "gates", default={}) or {}
    dashboard_raw = get_nested(cfg, "quality_engineering", "dashboard", default={}) or {}
    versioning_raw = get_nested(cfg, "quality_engineering", "versioning", default={}) or {}
    lineage_raw = get_nested(cfg, "quality_engineering", "lineage", default={}) or {}

    default_scoring = ScoringConfig()
    scoring = ScoringConfig(
        weights=scoring_raw.get("weights", default_scoring.weights),
        grade_thresholds=scoring_raw.get("grade_thresholds", default_scoring.grade_thresholds),
        steering_balance_qualitative_thresholds=scoring_raw.get(
            "steering_balance_qualitative_thresholds",
            default_scoring.steering_balance_qualitative_thresholds,
        ),
    )

    default_coverage = CoverageConfig()
    coverage = CoverageConfig(
        target_towns=coverage_raw.get("target_towns", default_coverage.target_towns),
        target_weather=coverage_raw.get("target_weather", default_coverage.target_weather),
        min_episodes_per_cell=coverage_raw.get(
            "min_episodes_per_cell", default_coverage.min_episodes_per_cell
        ),
        max_recommendations=coverage_raw.get(
            "max_recommendations", default_coverage.max_recommendations
        ),
    )

    default_regression = RegressionConfig()
    regression = RegressionConfig(
        warning=_regression_thresholds_from_dict(
            regression_raw.get("warning_thresholds"), default_regression.warning,
        ),
        failure=_regression_thresholds_from_dict(
            regression_raw.get("failure_thresholds"), default_regression.failure,
        ),
    )

    default_review = ReviewConfig()
    review = ReviewConfig(
        strength_threshold=review_raw.get("strength_threshold", default_review.strength_threshold),
        weakness_threshold=review_raw.get("weakness_threshold", default_review.weakness_threshold),
    )

    default_gates = GatesConfig()
    gates = GatesConfig(
        min_quality_score=gates_raw.get("min_quality_score", default_gates.min_quality_score),
        min_coverage_score=gates_raw.get("min_coverage_score", default_gates.min_coverage_score),
        min_steering_balance_score=gates_raw.get(
            "min_steering_balance_score", default_gates.min_steering_balance_score
        ),
        block_on_regression_severity=gates_raw.get(
            "block_on_regression_severity", default_gates.block_on_regression_severity
        ),
        require_regression_baseline=gates_raw.get(
            "require_regression_baseline", default_gates.require_regression_baseline
        ),
    )

    default_dashboard = DashboardConfig()
    dashboard = DashboardConfig(
        output_dir=dashboard_raw.get("output_dir", default_dashboard.output_dir),
        trend_window=dashboard_raw.get("trend_window", default_dashboard.trend_window),
    )

    default_versioning = VersioningConfig()
    versioning = VersioningConfig(
        version_filename=versioning_raw.get(
            "version_filename", default_versioning.version_filename
        ),
        changelog_filename=versioning_raw.get(
            "changelog_filename", default_versioning.changelog_filename
        ),
    )

    default_lineage = LineageConfig()
    lineage = LineageConfig(
        artifact_roots=lineage_raw.get("artifact_roots", default_lineage.artifact_roots),
    )

    return QualityEngineeringConfig(
        scoring=scoring, coverage=coverage, regression=regression, review=review, gates=gates,
        dashboard=dashboard, versioning=versioning, lineage=lineage,
    )


def _regression_thresholds_from_dict(
    raw: dict[str, Any] | None, default: RegressionThresholds,
) -> RegressionThresholds:
    """Merge a raw config dict over one tier's :class:`RegressionThresholds` defaults.

    Args:
        raw: The raw ``warning_thresholds`` / ``failure_thresholds`` dict
            from config, or None if not configured.
        default: The tier's dataclass defaults to fall back to per key.

    Returns:
        A new :class:`RegressionThresholds` with any provided keys
        overriding the defaults.
    """
    if not raw:
        return default
    return RegressionThresholds(
        sample_count_drop_pct=raw.get("sample_count_drop_pct", default.sample_count_drop_pct),
        quality_score_drop_pts=raw.get("quality_score_drop_pts", default.quality_score_drop_pts),
        duplicate_rate_increase_pct=raw.get(
            "duplicate_rate_increase_pct", default.duplicate_rate_increase_pct
        ),
        outlier_rate_increase_pct=raw.get(
            "outlier_rate_increase_pct", default.outlier_rate_increase_pct
        ),
        town_or_weather_cell_lost=raw.get(
            "town_or_weather_cell_lost", default.town_or_weather_cell_lost
        ),
    )
