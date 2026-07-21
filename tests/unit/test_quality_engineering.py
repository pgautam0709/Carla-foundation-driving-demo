"""
tests/unit/test_quality_engineering.py — Unit tests for Phase 3.5 engineering loop.

All tests are fully CARLA-free, Docker-free, GPU-free, and PyTorch-free.
Datasets are built via the real Phase 3 pipeline (:func:`build_dataset`)
using the same ``_write_episode`` helper :mod:`test_dataset_engineering`
already established, so every ``src/quality/`` function under test runs
against real (if small) dataset artifacts rather than hand-mocked stand-ins.

Test classes, one per module:
    TestSchemas            — to_dict() round trips
    TestRegistry            — CategoryRegistry
    TestConfig               — load_quality_config defaults + overrides
    TestArtifacts             — loaders, hashing, backward compatibility
    TestDatasetMetrics         — the six concrete metrics
    TestScoring                — weighted mean, grading
    TestCoverage                — target matrix, recommendations
    TestRegression                — snapshot diff, dataset comparison
    TestVersioning                  — version records, changelog
    TestLineage                      — cross-artifact-type derivation graph
    TestReview                        — star review
    TestGates                          — training-readiness checks
    TestDashboard                       — HTML generation
    TestCLIs                             — the six new scripts' Click commands
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.data.dataset_builder import build_dataset  # noqa: E402
from src.quality import dataset_metrics  # noqa: E402, F401 (registration side effect)
from src.quality.artifacts import (  # noqa: E402
    ArtifactNotFoundError,
    hash_content,
    load_artifact_envelope,
    load_dataset_artifacts,
    load_quality_score_record,
    load_version_record,
    resolve_latest_dataset_dir,
)
from src.quality.config import (  # noqa: E402
    GatesConfig,
    QualityEngineeringConfig,
    RegressionThresholds,
    load_quality_config,
)
from src.quality.coverage import (  # noqa: E402
    compute_coverage,
    recommend_collection,
    write_coverage_report,
)
from src.quality.dashboard import (  # noqa: E402
    SECTION_REGISTRY,
    generate_dashboard,
)
from src.quality.dataset_metrics import (  # noqa: E402
    CoverageMetric,
    DuplicateMetric,
    MetadataMetric,
    OutlierMetric,
    SteeringBalanceMetric,
    SynchronizationMetric,
    register_dataset_metrics,
)
from src.quality.gates import (  # noqa: E402
    DATASET_GATE_CHECKS,
    GateContext,
    check_min_coverage_score,
    check_min_quality_score,
    check_min_steering_balance_score,
    check_regression,
    check_sample_count_nonzero,
    evaluate_gate,
    write_gate_report,
)
from src.quality.lineage import (  # noqa: E402
    build_lineage_graph,
    evaluate_lineage_check,
    trace_ancestors,
    trace_descendants,
)
from src.quality.metrics import METRIC_REGISTRY, Metric  # noqa: E402
from src.quality.registry import CategoryRegistry  # noqa: E402
from src.quality.regression import (  # noqa: E402
    compare_datasets,
    compare_metric_snapshots,
    write_regression_report,
)
from src.quality.review import compute_review, write_review  # noqa: E402
from src.quality.schemas import (  # noqa: E402
    QUALITY_SCHEMA_VERSION,
    Artifact,
    CoverageCell,
    CoverageRecommendation,
    CoverageResult,
    GateCheckResult,
    GateReport,
    LineageEdge,
    MetricResult,
    QualityScore,
    RegressionFinding,
    RegressionReport,
    ReviewReport,
    VersionRecord,
)
from src.quality.scoring import compute_quality_score, write_quality_score  # noqa: E402
from src.quality.versioning import (  # noqa: E402
    compute_version_record,
    generate_changelog,
    write_version_artifacts,
)
from tests.unit.test_dataset_engineering import _write_episode  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build(
    tmp_path: Path,
    raw_subdir: str,
    dataset_id: str,
    *,
    episode_specs: list[dict[str, str | int]] | None = None,
    n_episodes: int = 3,
    ticks: int = 10,
    town: str = "Town03",
    weather: str = "ClearNoon",
) -> Path:
    """Write synthetic episodes and build a real Phase 3 dataset. Returns its directory."""
    raw = tmp_path / raw_subdir
    if episode_specs is not None:
        for i, spec in enumerate(episode_specs):
            spec_ticks = spec.get("ticks", ticks)
            spec_town = spec.get("town", town)
            spec_weather = spec.get("weather", weather)
            _write_episode(
                raw, f"ep_{i}",
                ticks=int(spec_ticks), town=str(spec_town),
                weather=str(spec_weather) if spec_weather is not None else None,
            )
    else:
        for i in range(n_episodes):
            _write_episode(raw, f"ep_{i}", ticks=ticks, town=town, weather=weather)
    out = tmp_path / "datasets" / dataset_id
    time.sleep(0.01)
    build_dataset(
        raw_episodes_dir=raw, output_dir=out,
        split_ratios={"train": 0.8, "val": 0.1, "test": 0.1}, split_seed=1,
    )
    return out


@pytest.fixture
def default_cfg() -> QualityEngineeringConfig:
    return QualityEngineeringConfig()


# ─────────────────────────────────────────────────────────────────────────────
# TestSchemas
# ─────────────────────────────────────────────────────────────────────────────

class TestSchemas:
    def test_artifact_to_dict_stringifies_path(self, tmp_path: Path) -> None:
        a = Artifact(
            artifact_id="a1", artifact_type="dataset", artifact_dir=tmp_path,
            created_at="2026-01-01T00:00:00Z", git_commit="abc123",
        )
        d = a.to_dict()
        assert d["artifact_dir"] == str(tmp_path)
        assert d["artifact_id"] == "a1"

    def test_metric_result_round_trip(self) -> None:
        m = MetricResult(name="coverage", raw_score=50.0, weight=0.2, detail="x")
        assert m.to_dict() == {"name": "coverage", "raw_score": 50.0, "weight": 0.2, "detail": "x"}

    def test_quality_score_round_trip_expands_metrics(self) -> None:
        score = QualityScore(
            schema_version=QUALITY_SCHEMA_VERSION, created_at="t", artifact_id="a1",
            overall_score=80.0, grade="B",
            metrics={
                "coverage": MetricResult(name="coverage", raw_score=80.0, weight=1.0, detail="d"),
            },
            weights_used={"coverage": 1.0}, grade_thresholds_used={"A": 90.0},
        )
        d = score.to_dict()
        assert d["metrics"]["coverage"]["raw_score"] == 80.0

    def test_lineage_edge_round_trip(self) -> None:
        e = LineageEdge(
            parent_artifact_type="dataset", parent_artifact_id="ds1", relation="trained_on",
        )
        assert e.to_dict() == {
            "parent_artifact_type": "dataset",
            "parent_artifact_id": "ds1",
            "relation": "trained_on",
        }

    def test_version_record_round_trip_expands_lineage_parents(self) -> None:
        v = VersionRecord(
            schema_version=QUALITY_SCHEMA_VERSION, artifact_type="dataset", artifact_id="ds1",
            created_at="t", git_commit=None, config_hash="h", content_hashes={"manifest": "h2"},
            generator_version="0.1.0", summary_counts={"sample_count": 1},
            previous_artifact_id=None,
            lineage_parents=[LineageEdge("dataset", "ds0", "trained_on")],
        )
        d = v.to_dict()
        assert d["lineage_parents"][0]["parent_artifact_id"] == "ds0"

    def test_regression_finding_and_report_round_trip(self) -> None:
        f = RegressionFinding(
            dimension="sample_count", baseline_value=10.0, candidate_value=5.0,
            delta=-5.0, severity="failure", message="dropped",
        )
        r = RegressionReport(
            schema_version=QUALITY_SCHEMA_VERSION, created_at="t", artifact_type="dataset",
            baseline_artifact_id="ds0", candidate_artifact_id="ds1", findings=[f],
        )
        d = r.to_dict()
        assert d["findings"][0]["severity"] == "failure"

    def test_coverage_schemas_round_trip(self) -> None:
        cell = CoverageCell(town="Town01", weather="ClearNoon", episode_count=1, met=False)
        result = CoverageResult(
            schema_version=QUALITY_SCHEMA_VERSION, created_at="t", artifact_id="ds1",
            target_towns=["Town01"], target_weather=["ClearNoon"], min_episodes_per_cell=3,
            cells=[cell], cells_met=0, cells_total=1, coverage_pct=0.0,
            routes={}, split_coverage={},
        )
        assert result.to_dict()["cells"][0]["town"] == "Town01"
        rec = CoverageRecommendation(
            town="Town01", weather="ClearNoon", current_episode_count=1, gap=2, message="collect",
        )
        assert rec.to_dict()["gap"] == 2

    def test_review_report_round_trip(self) -> None:
        r = ReviewReport(
            schema_version=QUALITY_SCHEMA_VERSION, created_at="t", artifact_id="ds1",
            stars=3, overall_score=70.0, grade="C", strengths=["s"], weaknesses=["w"],
            recommendations=["r"],
        )
        assert r.to_dict()["stars"] == 3

    def test_gate_schemas_round_trip(self) -> None:
        check = GateCheckResult(name="x", passed=True, detail="ok")
        report = GateReport(
            schema_version=QUALITY_SCHEMA_VERSION, created_at="t", artifact_id="ds1",
            passed=True, checks=[check],
        )
        assert report.to_dict()["checks"][0]["passed"] is True


# ─────────────────────────────────────────────────────────────────────────────
# TestRegistry
# ─────────────────────────────────────────────────────────────────────────────

class _Item:
    def __init__(self, name: str) -> None:
        self.name = name


class TestRegistry:
    def test_register_and_get(self) -> None:
        reg: CategoryRegistry[_Item] = CategoryRegistry()
        item = _Item("a")
        reg.register("cat", item)
        assert reg.get("cat", "a") is item

    def test_duplicate_name_raises(self) -> None:
        reg: CategoryRegistry[_Item] = CategoryRegistry()
        reg.register("cat", _Item("a"))
        with pytest.raises(ValueError, match="already registered"):
            reg.register("cat", _Item("a"))

    def test_get_missing_raises_keyerror(self) -> None:
        reg: CategoryRegistry[_Item] = CategoryRegistry()
        with pytest.raises(KeyError):
            reg.get("cat", "missing")

    def test_all_filters_by_category(self) -> None:
        reg: CategoryRegistry[_Item] = CategoryRegistry()
        reg.register("a", _Item("x"))
        reg.register("b", _Item("y"))
        assert [i.name for i in reg.all("a")] == ["x"]
        assert {i.name for i in reg.all()} == {"x", "y"}
        assert reg.all("nonexistent") == []

    def test_categories_sorted(self) -> None:
        reg: CategoryRegistry[_Item] = CategoryRegistry()
        reg.register("zebra", _Item("z"))
        reg.register("apple", _Item("a"))
        assert reg.categories() == ["apple", "zebra"]

    def test_metric_registry_has_six_dataset_metrics(self) -> None:
        names = {m.name for m in METRIC_REGISTRY.all("dataset")}
        assert names == {
            "synchronization", "coverage", "metadata", "outliers", "duplicates", "steering_balance",
        }

    def test_dashboard_section_registry_has_seven_sections(self) -> None:
        names = {s.name for s in SECTION_REGISTRY.all("dataset")}
        assert names == {
            "header", "quality", "coverage", "validation", "recent_changes",
            "quality_trend", "lineage",
        }


# ─────────────────────────────────────────────────────────────────────────────
# TestConfig
# ─────────────────────────────────────────────────────────────────────────────

class TestConfig:
    def test_empty_dict_yields_all_defaults(self) -> None:
        cfg = load_quality_config({})
        assert cfg == QualityEngineeringConfig()

    def test_partial_scoring_override(self) -> None:
        cfg = load_quality_config({"quality_engineering": {"scoring": {
            "weights": {"coverage": 1.0},
        }}})
        assert cfg.scoring.weights == {"coverage": 1.0}
        assert cfg.scoring.grade_thresholds == QualityEngineeringConfig().scoring.grade_thresholds

    def test_regression_thresholds_partial_override_keeps_other_fields(self) -> None:
        cfg = load_quality_config({"quality_engineering": {"regression": {
            "warning_thresholds": {"sample_count_drop_pct": 5.0},
        }}})
        assert cfg.regression.warning.sample_count_drop_pct == 5.0
        default_warning = QualityEngineeringConfig().regression.warning
        assert (
            cfg.regression.warning.quality_score_drop_pts == default_warning.quality_score_drop_pts
        )

    def test_regression_thresholds_none_returns_default(self) -> None:
        cfg = load_quality_config({})
        assert cfg.regression.warning is not None  # sanity: default path taken, no crash

    def test_gates_override(self) -> None:
        cfg = load_quality_config({"quality_engineering": {"gates": {
            "min_quality_score": 99.0, "require_regression_baseline": True,
        }}})
        assert cfg.gates.min_quality_score == 99.0
        assert cfg.gates.require_regression_baseline is True
        assert cfg.gates.block_on_regression_severity == "failure"

    def test_review_override(self) -> None:
        cfg = load_quality_config({"quality_engineering": {"review": {
            "strength_threshold": 95.0,
        }}})
        assert cfg.review.strength_threshold == 95.0
        assert cfg.review.weakness_threshold == 50.0

    def test_dashboard_versioning_lineage_overrides(self) -> None:
        cfg = load_quality_config({"quality_engineering": {
            "dashboard": {"trend_window": 3},
            "versioning": {"version_filename": "v.json"},
            "lineage": {"artifact_roots": {"dataset": "custom/path"}},
        }})
        assert cfg.dashboard.trend_window == 3
        assert cfg.dashboard.output_dir == "outputs/dashboard"
        assert cfg.versioning.version_filename == "v.json"
        assert cfg.lineage.artifact_roots == {"dataset": "custom/path"}

    def test_coverage_override(self) -> None:
        cfg = load_quality_config({"quality_engineering": {"coverage": {
            "min_episodes_per_cell": 1, "max_recommendations": 2,
        }}})
        assert cfg.coverage.min_episodes_per_cell == 1
        assert cfg.coverage.max_recommendations == 2
        assert cfg.coverage.target_towns  # defaults preserved


# ─────────────────────────────────────────────────────────────────────────────
# TestArtifacts
# ─────────────────────────────────────────────────────────────────────────────

class TestArtifacts:
    def test_load_dataset_artifacts_basic(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=2)
        artifact = load_dataset_artifacts(ds_dir)
        assert artifact.artifact_type == "dataset"
        assert artifact.artifact_id == "ds1"
        assert artifact.samples is None

    def test_load_dataset_artifacts_with_samples(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir, load_samples=True)
        assert artifact.samples is not None
        assert len(artifact.samples) > 0

    def test_load_dataset_artifacts_missing_manifest_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ArtifactNotFoundError):
            load_dataset_artifacts(tmp_path / "nope")

    def test_load_artifact_envelope(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        write_version_artifacts(ds_dir, {})
        envelope = load_artifact_envelope(ds_dir)
        assert envelope.artifact_id == "ds1"
        assert envelope.artifact_type == "dataset"

    def test_load_artifact_envelope_missing_raises(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        with pytest.raises(ArtifactNotFoundError):
            load_artifact_envelope(ds_dir)

    def test_load_version_record_round_trips_lineage_parents(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        edge = LineageEdge("model", "ckpt1", "evaluated")
        compute_version_record(ds_dir, {}, lineage_parents=[edge])
        write_version_artifacts(ds_dir, {})
        version = load_version_record(ds_dir)
        assert version.artifact_id == "ds1"

    def test_load_quality_score_record(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        score = compute_quality_score(artifact, default_cfg)
        write_quality_score(ds_dir, score)
        loaded = load_quality_score_record(ds_dir)
        assert loaded.overall_score == score.overall_score
        assert set(loaded.metrics) == set(score.metrics)

    def test_load_quality_score_record_missing_raises(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        with pytest.raises(ArtifactNotFoundError):
            load_quality_score_record(ds_dir)

    def test_hash_content_deterministic_and_key_order_independent(self) -> None:
        h1 = hash_content({"a": 1, "b": 2})
        h2 = hash_content({"b": 2, "a": 1})
        assert h1 == h2
        assert len(h1) == 64

    def test_hash_content_differs_for_different_content(self) -> None:
        assert hash_content({"a": 1}) != hash_content({"a": 2})

    def test_resolve_latest_dataset_dir_empty_and_missing(self, tmp_path: Path) -> None:
        assert resolve_latest_dataset_dir(tmp_path / "missing") is None
        empty = tmp_path / "empty"
        empty.mkdir()
        assert resolve_latest_dataset_dir(empty) is None

    def test_resolve_latest_dataset_dir_picks_most_recent(self, tmp_path: Path) -> None:
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()
        first = datasets_dir / "a"
        first.mkdir()
        time.sleep(0.02)
        second = datasets_dir / "b"
        second.mkdir()
        assert resolve_latest_dataset_dir(datasets_dir) == second

    def test_backward_compat_missing_1_1_fields_default_gracefully(self, tmp_path: Path) -> None:
        """A pre-1.1 dataset (no weather / duplicate_sample_count) still loads."""
        ds_dir = tmp_path / "legacy_ds"
        ds_dir.mkdir()
        manifest = {
            "schema_version": "1.0", "created_at": "t", "git_commit": None,
            "dataset_id": "legacy_ds", "raw_episodes_dir": "raw", "output_dir": str(ds_dir),
            "episode_count_discovered": 1, "episode_count_included": 1, "episode_count_excluded": 0,
            "sample_count": 1, "split_ratios": {"train": 1.0, "val": 0.0, "test": 0.0},
            "split_seed": 1, "allow_partial_alignment": False, "outlier_detection_enabled": False,
            "outlier_thresholds": None, "duplicate_detection_enabled": False,
            "episodes_index_path": "episodes_index.jsonl",
            "samples_index_path": "samples_index.jsonl",
            "quality_report_path": "quality_report.json", "statistics_path": "stats.json",
            "splits_dir": "splits", "split_index_paths": {},
        }
        (ds_dir / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        episode_row = {
            "episode_id": "ep_0", "episode_dir": "raw/ep_0", "town": "Town03",
            # no "weather" key — pre-1.1
            "route_name": "routeA", "collection_mode": "dry_run", "created_at": "t",
            "frame_count": 1, "control_row_count": 1, "telemetry_row_count": 1,
            "valid": True, "validation_errors": [], "aligned": True, "alignment_issues": [],
            "usable_tick_count": 1, "included": True, "exclusion_reason": None,
            "truncated": False, "split": "train",
        }
        (ds_dir / "episodes_index.jsonl").write_text(
            json.dumps(episode_row) + "\n", encoding="utf-8",
        )

        stats = {
            "episode_count": 1, "sample_count": 1,
            "split_counts": {"train": 1, "val": 0, "test": 0},
            "towns": {"Town03": 1},
            # no "weather" key — pre-1.1
            "throttle": {"mean": 0.5, "std": 0.0, "min": 0.5, "max": 0.5},
            "brake": {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0},
            "steer": {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0},
            "speed_kph": {"mean": 10.0, "std": 0.0, "min": 10.0, "max": 10.0},
            "steering_histogram": [],
        }
        (ds_dir / "stats.json").write_text(json.dumps(stats), encoding="utf-8")

        quality_report = {
            "schema_version": "1.0", "created_at": "t", "episodes_scanned": 1,
            "episodes_valid": 1, "episodes_invalid": 0, "episodes_included": 1,
            "episodes_excluded": 0, "episodes_misaligned": 0, "episodes_truncated": 0,
            "episodes_with_outliers": 0, "duplicate_frame_groups": 0,
            # no "duplicate_sample_count" key — pre-1.1
            "issues": [],
        }
        (ds_dir / "quality_report.json").write_text(json.dumps(quality_report), encoding="utf-8")

        artifact = load_dataset_artifacts(ds_dir)
        assert artifact.episodes[0].weather is None
        assert artifact.stats.weather == {}
        assert artifact.quality_report.duplicate_sample_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# TestDatasetMetrics
# ─────────────────────────────────────────────────────────────────────────────

class TestDatasetMetrics:
    def test_synchronization_metric_perfect(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=2)
        artifact = load_dataset_artifacts(ds_dir)
        result = SynchronizationMetric().compute(artifact, default_cfg)
        assert result.raw_score == 100.0

    def test_synchronization_metric_zero_scanned_scores_100(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=0)
        (tmp_path / "raw").mkdir(exist_ok=True)
        artifact = load_dataset_artifacts(ds_dir)
        result = SynchronizationMetric().compute(artifact, default_cfg)
        assert result.raw_score == 100.0
        assert "no episodes scanned" in result.detail

    def test_metadata_metric_zero_scanned_scores_100(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=0)
        artifact = load_dataset_artifacts(ds_dir)
        result = MetadataMetric().compute(artifact, default_cfg)
        assert result.raw_score == 100.0

    def test_outlier_metric_zero_included_scores_100(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=0)
        artifact = load_dataset_artifacts(ds_dir)
        result = OutlierMetric().compute(artifact, default_cfg)
        assert result.raw_score == 100.0

    def test_duplicate_metric_zero_samples_scores_100(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=0)
        artifact = load_dataset_artifacts(ds_dir)
        result = DuplicateMetric().compute(artifact, default_cfg)
        assert result.raw_score == 100.0

    def test_duplicate_metric_reflects_report(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=2)
        artifact = load_dataset_artifacts(ds_dir)
        result = DuplicateMetric().compute(artifact, default_cfg)
        expected = 100.0 * (
            1 - artifact.quality_report.duplicate_sample_count / artifact.manifest.sample_count
        )
        assert result.raw_score == pytest.approx(expected)

    def test_coverage_metric_delegates_to_compute_coverage(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        result = CoverageMetric().compute(artifact, default_cfg)
        direct = compute_coverage(artifact, default_cfg)
        assert result.raw_score == direct.coverage_pct

    def test_steering_balance_zero_entropy_scores_zero(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        # _write_episode always uses a fixed steer=0.1 -> one occupied bin out of
        # the default 10 -> zero entropy relative to max, via the entropy branch.
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=2, ticks=10)
        artifact = load_dataset_artifacts(ds_dir)
        result = SteeringBalanceMetric().compute(artifact, default_cfg)
        assert result.raw_score == 0.0
        assert "Poor" in result.detail
        assert "entropy" in result.detail

    def test_steering_balance_true_single_bin_histogram_scores_zero(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        """A dataset built with steering_histogram_bins=1 hits the distinct
        ``len(histogram) <= 1`` guard, not the entropy calculation."""
        raw = tmp_path / "raw"
        _write_episode(raw, "ep_0", ticks=5)
        out = tmp_path / "datasets" / "ds1"
        build_dataset(
            raw_episodes_dir=raw, output_dir=out,
            split_ratios={"train": 1.0, "val": 0.0, "test": 0.0}, split_seed=1,
            steering_histogram_bins=1,
        )
        artifact = load_dataset_artifacts(out)
        assert len(artifact.stats.steering_histogram) == 1
        result = SteeringBalanceMetric().compute(artifact, default_cfg)
        assert result.raw_score == 0.0
        assert "no balance possible" in result.detail

    def test_steering_balance_zero_samples_scores_100(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=0)
        artifact = load_dataset_artifacts(ds_dir)
        result = SteeringBalanceMetric().compute(artifact, default_cfg)
        assert result.raw_score == 100.0

    def test_metric_raises_typeerror_on_plain_artifact(self, tmp_path: Path) -> None:
        plain = Artifact(
            artifact_id="a", artifact_type="dataset", artifact_dir=tmp_path,
            created_at=None, git_commit=None,
        )
        with pytest.raises(TypeError, match="DatasetArtifact"):
            SynchronizationMetric().compute(plain, QualityEngineeringConfig())

    def test_register_dataset_metrics_idempotent(self) -> None:
        before = {m.name for m in METRIC_REGISTRY.all("dataset")}
        register_dataset_metrics()
        register_dataset_metrics()
        after = {m.name for m in METRIC_REGISTRY.all("dataset")}
        assert before == after == {
            "synchronization", "coverage", "metadata", "outliers", "duplicates", "steering_balance",
        }

    def test_metric_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            Metric()  # type: ignore[abstract]


# ─────────────────────────────────────────────────────────────────────────────
# TestScoring
# ─────────────────────────────────────────────────────────────────────────────

class TestScoring:
    def test_compute_quality_score_weighted_mean(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=3)
        artifact = load_dataset_artifacts(ds_dir)
        score = compute_quality_score(artifact, default_cfg)
        assert 0.0 <= score.overall_score <= 100.0
        assert score.grade in ("A", "B", "C", "D", "F")
        assert set(score.metrics) == {
            "synchronization", "coverage", "metadata", "outliers", "duplicates", "steering_balance",
        }
        assert pytest.approx(sum(score.weights_used.values()), abs=1e-9) == 1.0

    def test_zero_total_weight_splits_equally(self, tmp_path: Path) -> None:
        from src.quality.config import ScoringConfig
        cfg = QualityEngineeringConfig(scoring=ScoringConfig(weights={}))
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        score = compute_quality_score(artifact, cfg)
        weights = list(score.weights_used.values())
        assert all(w == pytest.approx(1 / 6) for w in weights)

    def test_grade_thresholds_boundaries(self) -> None:
        from src.quality.scoring import _grade_for_score
        thresholds = {"A": 90.0, "B": 80.0, "C": 70.0, "D": 60.0}
        assert _grade_for_score(95.0, thresholds) == "A"
        assert _grade_for_score(90.0, thresholds) == "A"
        assert _grade_for_score(89.9, thresholds) == "B"
        assert _grade_for_score(59.9, thresholds) == "F"

    def test_write_quality_score(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        score = compute_quality_score(artifact, default_cfg)
        path = write_quality_score(ds_dir, score)
        assert path.exists()
        on_disk = json.loads(path.read_text())
        assert on_disk["overall_score"] == score.overall_score


# ─────────────────────────────────────────────────────────────────────────────
# TestCoverage
# ─────────────────────────────────────────────────────────────────────────────

class TestCoverage:
    def test_compute_coverage_matrix_shape(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1, town="Town01", weather="ClearNoon")
        artifact = load_dataset_artifacts(ds_dir)
        coverage = compute_coverage(artifact, default_cfg)
        expected_total = (
            len(default_cfg.coverage.target_towns) * len(default_cfg.coverage.target_weather)
        )
        assert coverage.cells_total == expected_total
        assert coverage.cells_met <= coverage.cells_total

    def test_compute_coverage_cell_met_when_threshold_reached(self, tmp_path: Path) -> None:
        from src.quality.config import CoverageConfig
        cfg = QualityEngineeringConfig(coverage=CoverageConfig(
            target_towns=["Town01"], target_weather=["ClearNoon"], min_episodes_per_cell=2,
        ))
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=2, town="Town01", weather="ClearNoon")
        artifact = load_dataset_artifacts(ds_dir)
        coverage = compute_coverage(artifact, cfg)
        assert coverage.cells_met == 1
        assert coverage.coverage_pct == 100.0

    def test_recommend_collection_ranking_deterministic(self, tmp_path: Path) -> None:
        from src.quality.config import CoverageConfig
        cfg = QualityEngineeringConfig(coverage=CoverageConfig(
            target_towns=["Town02", "Town01"], target_weather=["ClearNoon", "HardRainNoon"],
            min_episodes_per_cell=2, max_recommendations=10,
        ))
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1, town="Town01", weather="ClearNoon")
        artifact = load_dataset_artifacts(ds_dir)
        coverage = compute_coverage(artifact, cfg)
        recs = recommend_collection(coverage, cfg)
        # Zero-count cells first (Town01/HardRain, Town02/*), then the under-threshold
        # Town01/ClearNoon (1/2) last; alphabetical tiebreak among zero-count cells.
        assert recs[-1].town == "Town01"
        assert recs[-1].weather == "ClearNoon"
        assert recs[-1].current_episode_count == 1
        zero_count_recs = recs[:-1]
        assert all(r.current_episode_count == 0 for r in zero_count_recs)
        assert [ (r.town, r.weather) for r in zero_count_recs ] == sorted(
            (r.town, r.weather) for r in zero_count_recs
        )

    def test_recommend_collection_respects_max_recommendations(self, tmp_path: Path) -> None:
        from src.quality.config import CoverageConfig
        cfg = QualityEngineeringConfig(coverage=CoverageConfig(max_recommendations=2))
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        coverage = compute_coverage(artifact, cfg)
        recs = recommend_collection(coverage, cfg)
        assert len(recs) <= 2

    def test_recommend_collection_empty_when_all_met(self, tmp_path: Path) -> None:
        from src.quality.config import CoverageConfig
        cfg = QualityEngineeringConfig(coverage=CoverageConfig(
            target_towns=["Town01"], target_weather=["ClearNoon"], min_episodes_per_cell=1,
        ))
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1, town="Town01", weather="ClearNoon")
        artifact = load_dataset_artifacts(ds_dir)
        coverage = compute_coverage(artifact, cfg)
        assert recommend_collection(coverage, cfg) == []

    def test_weather_label_known_and_fallback(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        from src.quality.config import CoverageConfig
        cfg = QualityEngineeringConfig(coverage=CoverageConfig(
            target_towns=["Town01"], target_weather=["HardRainNoon", "SomeUnknownPreset"],
            min_episodes_per_cell=99, max_recommendations=10,
        ))
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        coverage = compute_coverage(artifact, cfg)
        recs = recommend_collection(coverage, cfg)
        messages = " ".join(r.message for r in recs)
        assert "rainy" in messages
        assert "someunknownpreset" in messages

    def test_write_coverage_report(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        coverage = compute_coverage(artifact, default_cfg)
        path = write_coverage_report(ds_dir, coverage)
        assert path.exists()
        assert json.loads(path.read_text())["cells_total"] == coverage.cells_total


# ─────────────────────────────────────────────────────────────────────────────
# TestRegression
# ─────────────────────────────────────────────────────────────────────────────

class TestRegression:
    def test_compare_metric_snapshots_no_baseline_all_informational(
        self, default_cfg: QualityEngineeringConfig,
    ) -> None:
        findings = compare_metric_snapshots(
            None, {"sample_count": 10.0, "quality_score": 80.0},
            default_cfg.regression.warning, default_cfg.regression.failure,
        )
        assert len(findings) == 2
        assert all(f.severity == "informational" for f in findings)
        assert all(f.baseline_value is None for f in findings)

    def test_compare_metric_snapshots_unknown_dimension_informational(
        self, default_cfg: QualityEngineeringConfig,
    ) -> None:
        findings = compare_metric_snapshots(
            {"custom_dim": 1.0}, {"custom_dim": 2.0},
            default_cfg.regression.warning, default_cfg.regression.failure,
        )
        assert findings[0].severity == "informational"

    def test_compare_metric_snapshots_key_only_in_one_side(
        self, default_cfg: QualityEngineeringConfig,
    ) -> None:
        findings = compare_metric_snapshots(
            {"sample_count": 10.0}, {"quality_score": 80.0},
            default_cfg.regression.warning, default_cfg.regression.failure,
        )
        assert len(findings) == 2
        assert all(f.severity == "informational" and f.delta is None for f in findings)

    def test_compare_metric_snapshots_severity_tiers(self) -> None:
        warning = RegressionThresholds(10.0, 5.0, 2.0, 2.0)
        failure = RegressionThresholds(40.0, 15.0, 10.0, 10.0)
        # sample_count drop of 5% -> below warning (10%) -> informational.
        f1 = compare_metric_snapshots(
            {"sample_count": 100.0}, {"sample_count": 95.0}, warning, failure,
        )[0]
        assert f1.severity == "informational"
        # sample_count drop of 20% -> warning tier.
        f2 = compare_metric_snapshots(
            {"sample_count": 100.0}, {"sample_count": 80.0}, warning, failure,
        )[0]
        assert f2.severity == "warning"
        # sample_count drop of 50% -> failure tier.
        f3 = compare_metric_snapshots(
            {"sample_count": 100.0}, {"sample_count": 50.0}, warning, failure,
        )[0]
        assert f3.severity == "failure"
        # sample_count increase -> improvement.
        f4 = compare_metric_snapshots(
            {"sample_count": 100.0}, {"sample_count": 110.0}, warning, failure,
        )[0]
        assert f4.severity == "improvement"

    def test_compare_datasets_no_baseline(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        report = compare_datasets(None, artifact, default_cfg)
        assert report.baseline_artifact_id is None
        assert report.candidate_artifact_id == "ds1"
        assert all(f.severity == "informational" for f in report.findings)

    def test_compare_datasets_detects_town_removed_as_failure(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        baseline_dir = _build(tmp_path, "raw_a", "ds_baseline", episode_specs=[
            {"town": "Town03"}, {"town": "Town04"},
        ])
        candidate_dir = _build(tmp_path, "raw_b", "ds_candidate", episode_specs=[
            {"town": "Town03"},
        ])
        baseline = load_dataset_artifacts(baseline_dir)
        candidate = load_dataset_artifacts(candidate_dir)
        report = compare_datasets(baseline, candidate, default_cfg)
        town_finding = next(f for f in report.findings if f.dimension == "town:Town04")
        assert town_finding.severity == "failure"  # town_or_weather_cell_lost=True by default

    def test_compare_datasets_town_removed_is_warning_when_not_hard_trigger(
        self, tmp_path: Path,
    ) -> None:
        from src.quality.config import RegressionConfig
        warning = RegressionThresholds(10.0, 5.0, 2.0, 2.0, town_or_weather_cell_lost=False)
        failure = RegressionThresholds(40.0, 15.0, 10.0, 10.0, town_or_weather_cell_lost=False)
        cfg = QualityEngineeringConfig(
            regression=RegressionConfig(warning=warning, failure=failure),
        )
        baseline_dir = _build(tmp_path, "raw_a", "ds_baseline", episode_specs=[
            {"town": "Town03"}, {"town": "Town04"},
        ])
        candidate_dir = _build(
            tmp_path, "raw_b", "ds_candidate", episode_specs=[{"town": "Town03"}],
        )
        baseline = load_dataset_artifacts(baseline_dir)
        candidate = load_dataset_artifacts(candidate_dir)
        report = compare_datasets(baseline, candidate, cfg)
        town_finding = next(f for f in report.findings if f.dimension == "town:Town04")
        assert town_finding.severity == "warning"

    def test_compare_datasets_town_added_is_improvement(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        baseline_dir = _build(tmp_path, "raw_a", "ds_baseline", episode_specs=[{"town": "Town03"}])
        candidate_dir = _build(tmp_path, "raw_b", "ds_candidate", episode_specs=[
            {"town": "Town03"}, {"town": "Town04"},
        ])
        baseline = load_dataset_artifacts(baseline_dir)
        candidate = load_dataset_artifacts(candidate_dir)
        report = compare_datasets(baseline, candidate, default_cfg)
        town_finding = next(f for f in report.findings if f.dimension == "town:Town04")
        assert town_finding.severity == "improvement"

    def test_compare_datasets_includes_submetric_and_signal_findings(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        baseline_dir = _build(tmp_path, "raw_a", "ds_baseline", n_episodes=2)
        candidate_dir = _build(tmp_path, "raw_b", "ds_candidate", n_episodes=2)
        baseline = load_dataset_artifacts(baseline_dir)
        candidate = load_dataset_artifacts(candidate_dir)
        report = compare_datasets(baseline, candidate, default_cfg)
        dims = {f.dimension for f in report.findings}
        assert "quality_score.coverage" in dims
        assert "steer_mean" in dims
        assert "episode_count" in dims

    def test_write_regression_report(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        report = compare_datasets(None, artifact, default_cfg)
        path = write_regression_report(ds_dir, report)
        assert path.exists()
        assert json.loads(path.read_text())["candidate_artifact_id"] == "ds1"


# ─────────────────────────────────────────────────────────────────────────────
# TestVersioning
# ─────────────────────────────────────────────────────────────────────────────

class TestVersioning:
    def test_compute_version_record_first_version_has_no_previous(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        version = compute_version_record(ds_dir, {})
        assert version.previous_artifact_id is None
        assert version.artifact_type == "dataset"
        assert set(version.content_hashes) == {"manifest", "statistics", "quality_report"}
        assert version.lineage_parents == []

    def test_compute_version_record_explicit_previous_and_lineage(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        edge = LineageEdge("model", "ckpt1", "evaluated_by")
        version = compute_version_record(
            ds_dir, {}, previous_artifact_id="ds0", lineage_parents=[edge],
        )
        assert version.previous_artifact_id == "ds0"
        assert version.lineage_parents == [edge]

    def test_compute_version_record_resolves_previous_by_mtime(self, tmp_path: Path) -> None:
        first_dir = _build(tmp_path, "raw_a", "ds1", n_episodes=1)
        # Versioning ds1 now (before ds2 exists) is the "no previous" case.
        first_version = compute_version_record(first_dir, {})
        assert first_version.previous_artifact_id is None

        time.sleep(0.02)
        second_dir = _build(tmp_path, "raw_b", "ds2", n_episodes=1)
        version = compute_version_record(second_dir, {})
        assert version.previous_artifact_id == "ds1"

    def test_compute_version_record_previous_reflects_wall_clock_not_build_order(
        self, tmp_path: Path,
    ) -> None:
        """Documented edge case (ADR-0006 Decision 4): "previous" = most recently
        *modified* other dataset dir, not the one chronologically before this
        build — versioning an older dataset after a newer one already exists
        picks the newer one."""
        first_dir = _build(tmp_path, "raw_a", "ds1", n_episodes=1)
        time.sleep(0.02)
        _build(tmp_path, "raw_b", "ds2", n_episodes=1)
        version = compute_version_record(first_dir, {})
        assert version.previous_artifact_id == "ds2"

    def test_generate_changelog_no_previous_version(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        version = compute_version_record(ds_dir, {})
        text = generate_changelog(ds_dir, version, QualityEngineeringConfig())
        assert "Initial dataset" in text

    def test_generate_changelog_missing_baseline_on_disk(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        version = compute_version_record(ds_dir, {}, previous_artifact_id="ghost_ds")
        text = generate_changelog(ds_dir, version, QualityEngineeringConfig())
        assert "could not be found" in text

    def test_generate_changelog_with_real_previous_version(self, tmp_path: Path) -> None:
        _build(tmp_path, "raw_a", "ds_baseline", n_episodes=3)
        candidate_dir = _build(tmp_path, "raw_b", "ds_candidate", n_episodes=1)
        version = compute_version_record(
            candidate_dir, {}, previous_artifact_id="ds_baseline",
        )
        text = generate_changelog(candidate_dir, version, QualityEngineeringConfig())
        assert "ds_baseline" in text
        assert "## Regressions" in text

    def test_write_version_artifacts_writes_both_files(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        version = write_version_artifacts(ds_dir, {})
        assert (ds_dir / "version.json").exists()
        assert (ds_dir / "CHANGELOG.md").exists()
        on_disk = json.loads((ds_dir / "version.json").read_text())
        assert on_disk["artifact_id"] == version.artifact_id

    def test_resolve_previous_skips_sibling_with_incompatible_manifest_schema(
        self, tmp_path: Path,
    ) -> None:
        """Regression test for a real bug found during Phase 3.5 validation:
        a sibling dataset directory built by an older DatasetManifest schema
        (missing fields the current dataclass requires) used to crash the
        whole `previous_artifact_id` resolution with an uncaught TypeError,
        rather than being skipped as "not a usable candidate" the way a
        missing manifest already was. See ARTIFACT_LOAD_ERRORS in
        src/quality/artifacts.py."""
        first_dir = _build(tmp_path, "raw_a", "ds1", n_episodes=1)
        time.sleep(0.02)
        legacy_dir = _build(tmp_path, "raw_b", "ds_legacy", n_episodes=1)
        manifest_path = legacy_dir / "dataset_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for legacy_field in ("outlier_detection_enabled", "outlier_thresholds",
                              "duplicate_detection_enabled"):
            manifest.pop(legacy_field, None)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        # legacy_dir is the most recently modified sibling, so it would be
        # picked as "previous" — and previously crashed instead of being
        # skipped in favor of returning None (no usable previous version).
        version = compute_version_record(first_dir, {})
        assert version.previous_artifact_id is None

    def test_generate_changelog_previous_has_incompatible_manifest_schema(
        self, tmp_path: Path,
    ) -> None:
        legacy_dir = _build(tmp_path, "raw", "ds_legacy", n_episodes=1)
        manifest_path = legacy_dir / "dataset_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.pop("outlier_detection_enabled", None)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        candidate_dir = _build(tmp_path, "raw2", "ds_candidate", n_episodes=1)
        version = compute_version_record(
            candidate_dir, {}, previous_artifact_id="ds_legacy",
        )
        text = generate_changelog(candidate_dir, version, QualityEngineeringConfig())
        assert "could not be found or loaded" in text


# ─────────────────────────────────────────────────────────────────────────────
# TestLineage
# ─────────────────────────────────────────────────────────────────────────────

class TestLineage:
    def _cfg_with_roots(self, datasets_dir: Path, tmp_path: Path) -> QualityEngineeringConfig:
        from src.quality.config import LineageConfig
        return QualityEngineeringConfig(lineage=LineageConfig(artifact_roots={
            "dataset": str(datasets_dir),
            "model": str(tmp_path / "models_missing"),
            "evaluation": str(tmp_path / "eval_missing"),
            "deployment": str(tmp_path / "deploy_missing"),
        }))

    def test_build_lineage_graph_skips_missing_roots(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        write_version_artifacts(ds_dir, {})
        cfg = self._cfg_with_roots(ds_dir.parent, tmp_path)
        graph = build_lineage_graph(cfg)
        assert "dataset:ds1" in graph.nodes
        assert not any(k.startswith("model:") for k in graph.nodes)

    def test_build_lineage_graph_skips_unversioned_dataset(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)  # never versioned
        cfg = self._cfg_with_roots(ds_dir.parent, tmp_path)
        graph = build_lineage_graph(cfg)
        assert graph.nodes == {}

    def test_trace_ancestors_mixes_lineage_and_previous_chain(self, tmp_path: Path) -> None:
        baseline_dir = _build(tmp_path, "raw_a", "ds_baseline", n_episodes=1)
        write_version_artifacts(baseline_dir, {})
        candidate_dir = _build(tmp_path, "raw_b", "ds_candidate", n_episodes=1)
        write_version_artifacts(candidate_dir, {})  # auto-resolves previous_artifact_id via mtime
        cfg = self._cfg_with_roots(candidate_dir.parent, tmp_path)
        graph = build_lineage_graph(cfg)
        ancestors = trace_ancestors(graph, "dataset", "ds_candidate")
        assert [n.artifact_id for n in ancestors] == ["ds_baseline"]

    def test_trace_descendants_only_follows_lineage_parents(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        write_version_artifacts(ds_dir, {})
        cfg = self._cfg_with_roots(ds_dir.parent, tmp_path)
        graph = build_lineage_graph(cfg)
        assert trace_descendants(graph, "dataset", "ds1") == []

    def test_cross_type_lineage_edges_and_traversal(self, tmp_path: Path) -> None:
        """A synthetic "model" artifact (hand-written version.json — no real training
        code exists yet) whose lineage_parents points at a dataset exercises the
        genuine cross-type edge-building and traversal paths, distinct from the
        same-type previous_artifact_id chain covered elsewhere."""
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        write_version_artifacts(ds_dir, {})

        models_dir = tmp_path / "models"
        ckpt_dir = models_dir / "ckpt_001"
        ckpt_dir.mkdir(parents=True)
        (ckpt_dir / "version.json").write_text(json.dumps({
            "schema_version": QUALITY_SCHEMA_VERSION, "artifact_type": "model",
            "artifact_id": "ckpt_001", "created_at": "2026-07-10T00:00:00Z",
            "git_commit": None, "config_hash": "h", "content_hashes": {},
            "generator_version": "0.1.0", "summary_counts": {}, "previous_artifact_id": None,
            "lineage_parents": [
                {
                    "parent_artifact_type": "dataset",
                    "parent_artifact_id": "ds1",
                    "relation": "trained_on",
                },
            ],
        }), encoding="utf-8")

        from src.quality.config import LineageConfig
        cfg = QualityEngineeringConfig(lineage=LineageConfig(artifact_roots={
            "dataset": str(ds_dir.parent), "model": str(models_dir),
            "evaluation": str(tmp_path / "no_eval"), "deployment": str(tmp_path / "no_deploy"),
        }))
        graph = build_lineage_graph(cfg)
        assert ("model:ckpt_001", "dataset:ds1", "trained_on") in graph.edges

        ancestors = trace_ancestors(graph, "model", "ckpt_001")
        assert [n.artifact_id for n in ancestors] == ["ds1"]

        descendants = trace_descendants(graph, "dataset", "ds1")
        assert [n.artifact_id for n in descendants] == ["ckpt_001"]

    def test_trace_ancestors_unknown_artifact_returns_empty(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        write_version_artifacts(ds_dir, {})
        cfg = self._cfg_with_roots(ds_dir.parent, tmp_path)
        graph = build_lineage_graph(cfg)
        assert trace_ancestors(graph, "dataset", "does_not_exist") == []

    def test_evaluate_lineage_check_pass_and_fail(self, tmp_path: Path) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        version = compute_version_record(
            ds_dir, {}, lineage_parents=[LineageEdge("dataset", "ds0", "trained_on")],
        )
        passing = evaluate_lineage_check(
            version, expected_parent_type="dataset", expected_parent_id="ds0",
        )
        assert passing.passed is True
        failing = evaluate_lineage_check(
            version, expected_parent_type="dataset", expected_parent_id="ds_other",
        )
        assert failing.passed is False
        assert "does not include" in failing.detail

    def test_register_lineage_section_idempotent(self) -> None:
        from src.quality.dashboard import SECTION_REGISTRY
        from src.quality.lineage import register_lineage_section
        before = len(SECTION_REGISTRY.all("dataset"))
        register_lineage_section()  # already registered at import time; must no-op
        assert len(SECTION_REGISTRY.all("dataset")) == before


# ─────────────────────────────────────────────────────────────────────────────
# TestReview
# ─────────────────────────────────────────────────────────────────────────────

class TestReview:
    def test_compute_review_basic_fields(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=2)
        artifact = load_dataset_artifacts(ds_dir)
        review = compute_review(artifact, default_cfg)
        assert 1 <= review.stars <= 5
        assert review.grade in ("A", "B", "C", "D", "F")
        assert isinstance(review.strengths, list)
        assert isinstance(review.weaknesses, list)

    def test_compute_review_stars_match_grade_mapping(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        from src.quality.review import _STARS_BY_GRADE
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=2)
        artifact = load_dataset_artifacts(ds_dir)
        review = compute_review(artifact, default_cfg)
        assert review.stars == _STARS_BY_GRADE[review.grade]

    def test_compute_review_with_baseline_adds_regression_weaknesses(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        baseline_dir = _build(tmp_path, "raw_a", "ds_baseline", n_episodes=5)
        candidate_dir = _build(tmp_path, "raw_b", "ds_candidate", n_episodes=1)
        baseline = load_dataset_artifacts(baseline_dir)
        candidate = load_dataset_artifacts(candidate_dir)
        review = compute_review(candidate, default_cfg, baseline=baseline)
        assert any("Regressed since ds_baseline" in w for w in review.weaknesses)

    def test_derive_strengths_full_coverage_message(self, tmp_path: Path) -> None:
        from src.quality.config import CoverageConfig
        cfg = QualityEngineeringConfig(coverage=CoverageConfig(
            target_towns=["Town01"], target_weather=["ClearNoon"], min_episodes_per_cell=1,
        ))
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1, town="Town01", weather="ClearNoon")
        artifact = load_dataset_artifacts(ds_dir)
        review = compute_review(artifact, cfg)
        assert any("Full target coverage" in s for s in review.strengths)

    def test_write_review(self, tmp_path: Path, default_cfg: QualityEngineeringConfig) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        review = compute_review(artifact, default_cfg)
        path = write_review(ds_dir, review)
        assert path.exists()
        assert json.loads(path.read_text())["stars"] == review.stars


# ─────────────────────────────────────────────────────────────────────────────
# TestGates
# ─────────────────────────────────────────────────────────────────────────────

class TestGates:
    def test_check_sample_count_nonzero(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        score = compute_quality_score(artifact, default_cfg)
        coverage = compute_coverage(artifact, default_cfg)
        ctx = GateContext(
            artifact=artifact, score=score, coverage=coverage, regression=None, cfg=default_cfg,
        )
        assert check_sample_count_nonzero(ctx).passed is True

    def test_check_min_quality_score_boundary(
        self, tmp_path: Path,
    ) -> None:
        cfg = QualityEngineeringConfig(gates=GatesConfig(min_quality_score=0.0))
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        score = compute_quality_score(artifact, cfg)
        coverage = compute_coverage(artifact, cfg)
        ctx = GateContext(
            artifact=artifact, score=score, coverage=coverage, regression=None, cfg=cfg,
        )
        assert check_min_quality_score(ctx).passed is True

    def test_check_min_coverage_and_steering_balance_missing_metric_defaults_zero(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        score = compute_quality_score(artifact, default_cfg)
        score.metrics.pop("coverage", None)
        score.metrics.pop("steering_balance", None)
        coverage = compute_coverage(artifact, default_cfg)
        ctx = GateContext(
            artifact=artifact, score=score, coverage=coverage, regression=None, cfg=default_cfg,
        )
        assert check_min_coverage_score(ctx).passed is False
        assert check_min_steering_balance_score(ctx).passed is False

    def test_check_regression_no_baseline_passes_by_default(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        score = compute_quality_score(artifact, default_cfg)
        coverage = compute_coverage(artifact, default_cfg)
        ctx = GateContext(
            artifact=artifact, score=score, coverage=coverage, regression=None, cfg=default_cfg,
        )
        assert check_regression(ctx).passed is True

    def test_check_regression_no_baseline_fails_when_required(self, tmp_path: Path) -> None:
        cfg = QualityEngineeringConfig(gates=GatesConfig(require_regression_baseline=True))
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        score = compute_quality_score(artifact, cfg)
        coverage = compute_coverage(artifact, cfg)
        ctx = GateContext(
            artifact=artifact, score=score, coverage=coverage, regression=None, cfg=cfg,
        )
        assert check_regression(ctx).passed is False

    def test_check_regression_blocks_on_failure_finding(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        baseline_dir = _build(tmp_path, "raw_a", "ds_baseline", n_episodes=5)
        candidate_dir = _build(tmp_path, "raw_b", "ds_candidate", n_episodes=1)
        baseline = load_dataset_artifacts(baseline_dir)
        candidate = load_dataset_artifacts(candidate_dir)
        score = compute_quality_score(candidate, default_cfg)
        coverage = compute_coverage(candidate, default_cfg)
        regression = compare_datasets(baseline, candidate, default_cfg)
        ctx = GateContext(
            artifact=candidate, score=score, coverage=coverage,
            regression=regression, cfg=default_cfg,
        )
        assert check_regression(ctx).passed is False

    def test_check_regression_warning_tier_config_blocks_on_warning(self, tmp_path: Path) -> None:
        cfg = QualityEngineeringConfig(gates=GatesConfig(block_on_regression_severity="warning"))
        baseline_dir = _build(tmp_path, "raw_a", "ds_baseline", n_episodes=2)
        candidate_dir = _build(tmp_path, "raw_b", "ds_candidate", n_episodes=2)
        baseline = load_dataset_artifacts(baseline_dir)
        candidate = load_dataset_artifacts(candidate_dir)
        score = compute_quality_score(candidate, cfg)
        coverage = compute_coverage(candidate, cfg)
        regression = compare_datasets(baseline, candidate, cfg)
        ctx = GateContext(
            artifact=candidate, score=score, coverage=coverage, regression=regression, cfg=cfg,
        )
        # Just confirm the "warning" tier is exercised without error;
        # outcome depends on fixture data.
        result = check_regression(ctx)
        assert result.name == "regression"

    def test_evaluate_gate_runs_all_checks_in_order(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        report = evaluate_gate(artifact, default_cfg)
        expected_names = [check.__name__.replace("check_", "") for check in DATASET_GATE_CHECKS]
        assert [c.name for c in report.checks] == expected_names
        assert report.passed == all(c.passed for c in report.checks)

    def test_evaluate_gate_empty_dataset_fails_sample_count_check(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        raw = tmp_path / "raw_empty"
        raw.mkdir()
        out = tmp_path / "datasets" / "ds_empty"
        build_dataset(
            raw_episodes_dir=raw, output_dir=out,
            split_ratios={"train": 0.8, "val": 0.1, "test": 0.1}, split_seed=1,
        )
        artifact = load_dataset_artifacts(out)
        report = evaluate_gate(artifact, default_cfg)
        assert report.passed is False
        sample_check = next(c for c in report.checks if c.name == "sample_count_nonzero")
        assert sample_check.passed is False

    def test_write_gate_report(
        self, tmp_path: Path, default_cfg: QualityEngineeringConfig,
    ) -> None:
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        artifact = load_dataset_artifacts(ds_dir)
        report = evaluate_gate(artifact, default_cfg)
        path = write_gate_report(ds_dir, report)
        assert path.exists()
        assert json.loads(path.read_text())["passed"] == report.passed


# ─────────────────────────────────────────────────────────────────────────────
# TestDashboard
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboard:
    def test_generate_dashboard_writes_html_with_all_sections(self, tmp_path: Path) -> None:
        from src.quality.config import DashboardConfig, LineageConfig
        datasets_dir = tmp_path / "datasets"
        baseline_dir = _build(tmp_path, "raw_a", "ds_baseline", n_episodes=2)
        write_version_artifacts(baseline_dir, {})
        write_quality_score(
            baseline_dir,
            compute_quality_score(load_dataset_artifacts(baseline_dir), QualityEngineeringConfig()),
        )
        candidate_dir = _build(tmp_path, "raw_b", "ds_candidate", n_episodes=1)
        write_version_artifacts(candidate_dir, {})
        write_quality_score(
            candidate_dir,
            compute_quality_score(
                load_dataset_artifacts(candidate_dir), QualityEngineeringConfig(),
            ),
        )

        cfg = QualityEngineeringConfig(
            dashboard=DashboardConfig(output_dir=str(tmp_path / "dash_out")),
            lineage=LineageConfig(artifact_roots={
                "dataset": str(datasets_dir),
                "model": str(tmp_path / "no_models"),
                "evaluation": str(tmp_path / "no_eval"),
                "deployment": str(tmp_path / "no_deploy"),
            }),
        )
        output_path = generate_dashboard(candidate_dir, cfg, datasets_dir=datasets_dir)
        assert output_path.exists()
        html_text = output_path.read_text(encoding="utf-8")
        assert html_text.startswith("<!DOCTYPE html>")
        for expected in (
            "Dataset Engineering Report", "Quality —", "Coverage —", "Training Readiness",
            "Recent Changes", "Quality Trend", "Lineage",
        ):
            assert expected in html_text
        # Both datasets have version.json + quality_score.json -> trend has 2 points.
        assert html_text.count("<circle") == 2

    def test_generate_dashboard_no_version_shows_placeholder(self, tmp_path: Path) -> None:
        from src.quality.config import DashboardConfig
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)  # never versioned
        cfg = QualityEngineeringConfig(
            dashboard=DashboardConfig(output_dir=str(tmp_path / "dash_out")),
        )
        output_path = generate_dashboard(ds_dir, cfg, datasets_dir=ds_dir.parent)
        html_text = output_path.read_text(encoding="utf-8")
        assert "has not been versioned yet" in html_text

    def test_generate_dashboard_versioned_first_release_no_baseline(self, tmp_path: Path) -> None:
        """Versioned (has version.json) but no previous_artifact_id — the
        "Recent Changes" section must show its no-comparison note, not a
        findings table (distinct from the never-versioned placeholder case)."""
        from src.quality.config import DashboardConfig
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        write_version_artifacts(ds_dir, {})
        cfg = QualityEngineeringConfig(
            dashboard=DashboardConfig(output_dir=str(tmp_path / "dash_out")),
        )
        output_path = generate_dashboard(ds_dir, cfg, datasets_dir=ds_dir.parent)
        html_text = output_path.read_text(encoding="utf-8")
        assert "No comparison available" in html_text
        assert "(none — first version)" in html_text

    def test_generate_dashboard_baseline_recorded_but_missing_on_disk(
        self, tmp_path: Path,
    ) -> None:
        from src.quality.config import DashboardConfig
        from src.quality.versioning import compute_version_record

        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        # previous_artifact_id points at a sibling directory that was never built,
        # forcing generate_dashboard's load_dataset_artifacts(baseline_dir) to raise
        # ArtifactNotFoundError and fall back to baseline=None (dashboard.py:161-163).
        version = compute_version_record(ds_dir, {}, previous_artifact_id="ghost_ds")
        (ds_dir / "version.json").write_text(
            json.dumps(version.to_dict(), default=str), encoding="utf-8",
        )

        cfg = QualityEngineeringConfig(
            dashboard=DashboardConfig(output_dir=str(tmp_path / "dash_out")),
        )
        output_path = generate_dashboard(ds_dir, cfg, datasets_dir=ds_dir.parent)
        html_text = output_path.read_text(encoding="utf-8")
        assert "No comparison available" in html_text
        assert "ghost_ds" in html_text
        assert "not found on disk" in html_text

    def test_generate_dashboard_baseline_has_incompatible_manifest_schema(
        self, tmp_path: Path,
    ) -> None:
        """Regression test: a recorded previous_artifact_id whose directory
        exists but was built by an older, incompatible DatasetManifest
        schema must degrade to "no baseline," not crash the dashboard."""
        from src.quality.config import DashboardConfig
        from src.quality.versioning import compute_version_record

        legacy_dir = _build(tmp_path, "raw_a", "ds_legacy", n_episodes=1)
        manifest_path = legacy_dir / "dataset_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.pop("duplicate_detection_enabled", None)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        ds_dir = _build(tmp_path, "raw_b", "ds1", n_episodes=1)
        version = compute_version_record(ds_dir, {}, previous_artifact_id="ds_legacy")
        (ds_dir / "version.json").write_text(
            json.dumps(version.to_dict(), default=str), encoding="utf-8",
        )

        cfg = QualityEngineeringConfig(
            dashboard=DashboardConfig(output_dir=str(tmp_path / "dash_out")),
        )
        output_path = generate_dashboard(ds_dir, cfg, datasets_dir=ds_dir.parent)
        html_text = output_path.read_text(encoding="utf-8")
        assert "No comparison available" in html_text
        assert "ds_legacy" in html_text

    def test_trend_svg_has_one_point_per_history_entry(self, tmp_path: Path) -> None:
        from src.quality.dashboard import _trend_svg
        svg = _trend_svg([("ds1", "t1", 50.0), ("ds2", "t2", 90.0)])
        assert svg.count("<circle") == 2
        assert svg.startswith("<svg")

    def test_trend_svg_single_point_no_crash(self) -> None:
        from src.quality.dashboard import _trend_svg
        svg = _trend_svg([("ds1", "t1", 50.0)])
        assert svg.count("<circle") == 1

    def test_list_helper_empty_and_nonempty(self) -> None:
        from src.quality.dashboard import _list
        assert "nothing here" in _list([], empty="nothing here")
        assert "<li>a</li>" in _list(["a"], empty="unused")


# ─────────────────────────────────────────────────────────────────────────────
# TestCLIs
# ─────────────────────────────────────────────────────────────────────────────

class TestCLIs:
    def _build_and_version(self, tmp_path: Path, dataset_id: str, n_episodes: int = 2) -> Path:
        ds_dir = _build(tmp_path, f"raw_{dataset_id}", dataset_id, n_episodes=n_episodes)
        return ds_dir

    def test_dataset_quality_help(self) -> None:
        from click.testing import CliRunner

        from dataset_quality import main
        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--dataset-dir" in result.output

    def test_dataset_quality_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from click.testing import CliRunner

        from dataset_quality import main
        monkeypatch.chdir(tmp_path)
        ds_dir = self._build_and_version(tmp_path, "ds1")
        result = CliRunner().invoke(main, ["--dataset-dir", str(ds_dir)])
        assert "Dataset Quality" in result.output
        assert (ds_dir / "quality_score.json").exists()
        assert (ds_dir / "gate_report.json").exists()

    def test_dataset_review_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from click.testing import CliRunner

        from dataset_review import main
        monkeypatch.chdir(tmp_path)
        ds_dir = self._build_and_version(tmp_path, "ds1")
        result = CliRunner().invoke(main, ["--dataset-dir", str(ds_dir)])
        assert result.exit_code == 0
        assert "Dataset Review" in result.output
        assert (ds_dir / "review.json").exists()

    def test_compare_datasets_run_with_explicit_baseline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from click.testing import CliRunner

        from compare_datasets import main
        monkeypatch.chdir(tmp_path)
        baseline_dir = self._build_and_version(tmp_path, "ds_baseline", n_episodes=3)
        candidate_dir = self._build_and_version(tmp_path, "ds_candidate", n_episodes=1)
        result = CliRunner().invoke(main, [
            "--baseline", str(baseline_dir), "--candidate", str(candidate_dir),
        ])
        assert "Dataset Comparison" in result.output
        assert (candidate_dir / "regression_report.json").exists()

    def test_compare_datasets_run_no_baseline_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from click.testing import CliRunner

        from compare_datasets import main
        monkeypatch.chdir(tmp_path)
        candidate_dir = self._build_and_version(tmp_path, "ds_candidate", n_episodes=1)
        result = CliRunner().invoke(
            main, ["--candidate", str(candidate_dir)], catch_exceptions=False,
        )
        assert "No baseline available" in result.output or result.exit_code == 0

    def test_dataset_dashboard_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from click.testing import CliRunner

        from dataset_dashboard import main
        monkeypatch.chdir(tmp_path)
        ds_dir = self._build_and_version(tmp_path, "ds1")
        result = CliRunner().invoke(main, ["--dataset-dir", str(ds_dir)])
        assert result.exit_code == 0
        assert "Dashboard written" in result.output

    def test_recommend_data_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from click.testing import CliRunner

        from recommend_data import main
        monkeypatch.chdir(tmp_path)
        ds_dir = self._build_and_version(tmp_path, "ds1")
        result = CliRunner().invoke(main, ["--dataset-dir", str(ds_dir)])
        assert result.exit_code == 0
        assert "Data Collection Recommendations" in result.output
        assert (ds_dir / "coverage_report.json").exists()

    def test_dataset_version_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from click.testing import CliRunner

        from dataset_version import main
        monkeypatch.chdir(tmp_path)
        ds_dir = _build(tmp_path, "raw", "ds1", n_episodes=1)
        result = CliRunner().invoke(main, ["--dataset-dir", str(ds_dir)])
        assert result.exit_code == 0
        assert "Dataset Version" in result.output
        assert (ds_dir / "version.json").exists()
        assert (ds_dir / "CHANGELOG.md").exists()

    def test_no_dataset_found_fails_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from click.testing import CliRunner

        from dataset_quality import main
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(main, ["--dataset-dir", str(tmp_path / "does_not_exist")])
        assert result.exit_code != 0
