# Quality Scoring, Review, and Training Gates

Covers `src/quality/metrics.py`, `dataset_metrics.py`, `scoring.py`,
`coverage.py`, `review.py`, and `gates.py`. See ADR-0004, ADR-0005,
ADR-0008 for the design rationale behind each.

## The registry pattern

`src/quality/registry.py` defines one generic `CategoryRegistry[T]`, bound
to any `T` with a `.name: str` attribute (`_Named` Protocol). Two
independent instances of it exist:

- `METRIC_REGISTRY` in `metrics.py` — holds `Metric` subclasses, keyed by
  category (`"dataset"` today).
- `SECTION_REGISTRY` in `dashboard.py` — holds `DashboardSection`s, same
  pattern, same category.

Both let a new metric or dashboard section be added by calling
`.register(category, item)` from anywhere — including, as `lineage.py`
does for its own dashboard section, from a different module — without
editing a central switch statement. `dataset_metrics.py`'s
`register_dataset_metrics()` is the side-effecting call that populates
`METRIC_REGISTRY["dataset"]`; it must run once before scoring (importing
`src.quality.dataset_metrics` is enough — every entry point does this).

## The six dataset metrics

Each is a `Metric` subclass (`metrics.py`'s `abc.ABC`) implementing
`compute(artifact, cfg) -> MetricResult`, where `MetricResult` carries a
`raw_score: float` (0-100) and a human-readable `detail: str`.

| Metric | Class | What it measures |
|--------|-------|-------------------|
| Synchronization | `SynchronizationMetric` | Fraction of episodes that passed alignment (frames/controls/telemetry in step) without truncation |
| Coverage | `CoverageMetric` | Same `CoverageResult.coverage_pct` used by `coverage.py`, expressed as a metric |
| Metadata | `MetadataMetric` | Fraction of samples with complete, non-null metadata fields |
| Outliers | `OutlierMetric` | Inverse of the outlier rate from Phase 3b's `quality_report.json` (steering spikes, stuck-throttle) |
| Duplicates | `DuplicateMetric` | Inverse of the duplicate-sample rate from `quality_report.json` |
| Steering balance | `SteeringBalanceMetric` | Normalized Shannon entropy of the steering-angle histogram |

### Steering balance in detail

`SteeringBalanceMetric` reads the steering histogram Phase 3b already
computes (`stats.json`). Three cases:

- **No samples** (`total == 0`): score `100`, detail `"no samples"` — an
  empty dataset isn't "imbalanced," there's nothing to balance.
- **True single-bin histogram** (`len(histogram) <= 1`, i.e. the
  configured `steering_histogram_bins` collapses to one bin, or the
  histogram itself has one entry): score `0`, detail `"single-bin
  histogram (no balance possible)"`.
- **Otherwise**: normalized Shannon entropy over however many bins are
  populated. A dataset where every sample lands in one bin out of many
  configured also scores `0` here — via zero entropy, not the single-bin
  branch above — but the detail message distinguishes the two causes so a
  reviewer isn't confused about which is which.

`_qualitative_label()` maps the numeric score to `Good`/`Fair`/`Poor`
using `scoring.steering_balance_qualitative_thresholds` (default
`{Good: 80.0, Fair: 60.0}`), shown alongside the raw score in the
dashboard and CLI output.

## Combining metrics into a score

`scoring.compute_quality_score(artifact, cfg)`:

1. Calls every registered `"dataset"` metric's `.compute()`.
2. Combines results as a **weighted mean** (never a minimum — one weak
   metric shouldn't zero out an otherwise-strong dataset; see ADR-0005
   Decision 2), using `cfg.scoring.weights`, normalized internally so
   weights need not sum to 1.
3. Maps the combined 0-100 score to a letter grade via
   `_grade_for_score()` and `cfg.scoring.grade_thresholds`
   (`{A: 90, B: 80, C: 70, D: 60}`; anything below D's floor is `F`).

`write_quality_score()` persists the result to
`<dataset_dir>/quality_score.json`.

## Coverage matrix and recommendations

`coverage.compute_coverage(artifact, cfg)` builds the Cartesian product of
`cfg.coverage.target_towns × target_weather`, counts episodes per cell,
and marks a cell `met` once it reaches `min_episodes_per_cell`. A cell's
weather value is normalized to a fixed label set via `_weather_label()`
before matching, so config typos in casing don't silently create phantom
unmet cells.

`recommend_collection(coverage, cfg)` ranks unmet cells deterministically:
zero-count cells first, then fewest episodes, then alphabetical by
`(town, weather)` — so re-running against the same coverage report always
produces the same ranked list, capped at `cfg.coverage.max_recommendations`.

`write_coverage_report()` persists to `<dataset_dir>/coverage_report.json`.

## Review — the human-facing summary

`review.compute_review(artifact, cfg, *, baseline=None) -> ReviewReport`:

- **Stars**: derived from the letter grade via a fixed
  `_STARS_BY_GRADE = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}` mapping —
  reusing the existing grade thresholds rather than introducing a second,
  independently-tunable star cutoff (ADR-0005 Decision 4's "one place to
  tune" principle).
- **Strengths**: any metric with `raw_score >= cfg.review.strength_threshold`
  (default 80.0), plus a "full coverage" call-out if every target cell is met.
- **Weaknesses**: any metric with `raw_score < cfg.review.weakness_threshold`
  (default 50.0), plus a coverage-gap call-out if cells remain unmet.

`write_review()` persists to `<dataset_dir>/review.json`.

## Training-readiness gates

`gates.py` is the pass/fail layer training would consult before starting
a run (Phase 4's `train.py` is expected to check `gate_report.json`
before launching, though nothing in this phase writes that integration
code).

`GateContext` bundles everything a check needs: `artifact`, `score`,
`coverage`, `regression` (`RegressionReport | None`), `cfg`. Each check is
a plain function `GateCheck = Callable[[GateContext], GateCheckResult]`:

| Check | Fails when |
|-------|-----------|
| `check_sample_count_nonzero` | The dataset has zero samples |
| `check_min_quality_score` | Overall score below `cfg.gates.min_quality_score` |
| `check_min_coverage_score` | Coverage % below `cfg.gates.min_coverage_score` |
| `check_min_steering_balance_score` | Steering-balance metric below `cfg.gates.min_steering_balance_score` |
| `check_regression` | Worst regression finding's severity is at or above `cfg.gates.block_on_regression_severity` |

`DATASET_GATE_CHECKS: tuple[GateCheck, ...]` lists all five in the order
they're evaluated and reported. `check_regression` passes automatically
when there is no baseline to compare against, **unless**
`cfg.gates.require_regression_baseline` is `True` — off by default so the
very first dataset ever built doesn't fail the gate for lack of history.

`evaluate_gate(artifact, cfg, *, baseline=None) -> GateReport` runs every
check and sets `passed = all(c.passed for c in checks)`.
`write_gate_report()` persists to `<dataset_dir>/gate_report.json`.
`scripts/dataset_quality.py` exits with status 1 when the gate fails, so
it can be dropped into CI as a blocking step.
