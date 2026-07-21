# Regression Detection

Covers `src/quality/regression.py`. See ADR-0007 for the design rationale.

## Two layers: artifact-agnostic core, dataset-specific wrapper

`compare_metric_snapshots(baseline, candidate, cfg) -> list[RegressionFinding]`
is the artifact-agnostic core: given two flat `dict[str, float]` snapshots
(any artifact type could produce one), it compares four thresholded
dimensions — `sample_count`, `quality_score`, `duplicate_rate`,
`outlier_rate` — and returns one `RegressionFinding` per dimension.

`compare_datasets(baseline, candidate, cfg) -> RegressionReport` is the
dataset-specific wrapper `scripts/compare_datasets.py` and
`versioning.generate_changelog()` actually call. It:

1. Computes each dataset's `QualityScore` (via `scoring.compute_quality_score`).
2. Builds a numeric snapshot per dataset (`_numeric_snapshot()`) from the
   manifest, quality report, and score.
3. Calls `compare_metric_snapshots()` for the four numeric dimensions.
4. Adds categorical comparisons for `town`, `weather`, and `route`
   coverage (`_compare_categorical()`), and sub-metric comparisons for
   each of the six quality metrics individually (`_compare_submetrics()`).
5. Adds signal-mean comparisons — e.g. mean steering, mean speed — via
   `_compare_signal_means()`.
6. Returns a `RegressionReport` with `baseline_artifact_id`,
   `candidate_artifact_id`, and every `RegressionFinding`.

If `baseline` is `None` (no prior version to compare against), every
dimension is reported with severity `"informational"` rather than
skipped — a reviewer sees the candidate's own numbers even with nothing
to compare them to.

## Severity derivation

For numeric dimensions, `_badness(key, baseline, candidate) -> float`
computes a signed "how much worse" value per dimension (e.g. percent drop
for `sample_count`, point drop for `quality_score`), then
`_severity_for_badness()` maps it against the configured thresholds:

```
badness >= failure_threshold  → "failure"
badness >= warning_threshold  → "warning"
badness < 0                   → "improvement"
otherwise                     → "informational"
```

For categorical dimensions (town/weather/route),
`_compare_categorical()` applies dimension-specific rules: a
newly-appearing value is `"improvement"`, a value that disappears is
`"failure"` if `cfg.regression.failure_thresholds.town_or_weather_cell_lost`
is `True` (the default), otherwise `"warning"`; a changed count for an
existing value is `"informational"`.

## Thresholds

`cfg.regression.warning_thresholds` and `.failure_thresholds`
(`RegressionThresholds` in `config.py`), one pair of numbers per numeric
dimension plus the categorical `town_or_weather_cell_lost` flag:

| Dimension | Warning (default) | Failure (default) |
|-----------|-------------------|--------------------|
| `sample_count_drop_pct` | 10.0 | 40.0 |
| `quality_score_drop_pts` | 5.0 | 15.0 |
| `duplicate_rate_increase_pct` | 2.0 | 10.0 |
| `outlier_rate_increase_pct` | 2.0 | 10.0 |
| `town_or_weather_cell_lost` | — | `true` (any covered cell dropping to zero episodes is a failure) |

Every value is config-driven — nothing in `regression.py` hardcodes a
percentage.

## Output

`write_regression_report(dataset_dir, report, filename=REGRESSION_REPORT_FILENAME)`
writes `regression_report.json` into the **candidate's** directory
(`REGRESSION_REPORT_FILENAME = "regression_report.json"`). Both
`scripts/compare_datasets.py` and `versioning.write_version_artifacts()`
(via the changelog step) can trigger a comparison; only
`compare_datasets.py` persists the standalone JSON report — the
changelog step renders the same `RegressionReport` to Markdown instead.

## Relationship to gates

`gates.check_regression(ctx)` inspects `ctx.regression` (a
`RegressionReport | None` already computed by the caller — `gates.py`
does not call `compare_datasets()` itself) and fails the training gate if
the worst finding's severity is at or above
`cfg.gates.block_on_regression_severity` (`"failure"` by default;
settable to `"warning"` for a stricter gate). With no baseline, this
check passes unless `cfg.gates.require_regression_baseline` is `True` —
see `docs/QUALITY_SYSTEM.md`.

## CLI

```bash
make compare-data CANDIDATE=data/processed/datasets/<new> BASELINE=data/processed/datasets/<old>
# or, relying on the candidate's own recorded previous_artifact_id:
make compare-data CANDIDATE=data/processed/datasets/<new>
```

`scripts/compare_datasets.py` prints findings grouped by severity
(failure → warning → improvement → informational) and exits with status 1
if any `failure`-severity finding exists, so it can gate a CI step the
same way `make quality` does.
