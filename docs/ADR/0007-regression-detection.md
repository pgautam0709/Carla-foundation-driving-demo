# ADR 0007 — Regression Detection

**Date:** 2026-07-20 (revised same day — see Revision Note)
**Status:** Proposed
**Deciders:** AI Engineering Team

> **Revision note:** `RegressionReport`'s identity fields are renamed
> `baseline_artifact_id` / `candidate_artifact_id` (from
> `baseline_dataset_id` / `candidate_dataset_id`), matching ADR-0006's
> revised `VersionRecord.artifact_id`. `compare_datasets()` remains the
> dataset-specific entry point (it diffs dataset-only dimensions like
> towns/weather/routes — Decision 3), built on top of the same
> artifact-generic comparator `compare_metric_snapshots()` already
> described in Decision 5/ADR-0010 §3 — this revision only makes that
> split explicit in the naming, it does not change the comparison logic.

---

## Context

Every dataset build is independent and versioned (ADR-0006), but nothing
today compares two builds. A reviewer collecting more episodes and
rebuilding has no automated way to know whether the new dataset is
actually better, worse, or just different — did coverage improve, did the
duplicate rate creep up, did an entire town disappear because of a typo in
`--raw-episodes-dir`? This ADR defines `src/quality/regression.py`, the
one comparator both `CHANGELOG.md` generation (ADR-0006) and the
training gate (ADR-0004 extension point 3) build on.

---

## Decisions

### 1. Comparison is baseline-vs-candidate, always in that order, never symmetric

**Decision:** `compare_datasets(baseline_dir, candidate_dir, cfg) ->
RegressionReport` always treats its two arguments asymmetrically:
*baseline* is "what we compare against," *candidate* is "what we are
evaluating." All deltas are reported as `candidate - baseline`
(or `candidate / baseline` for ratios).

**Rationale:** A symmetric diff ("these two datasets differ in N ways")
cannot answer "did this get better or worse," which is the actual question
a reviewer rebuilding a dataset is asking. An explicit direction makes
"improvement" and "regression" well-defined instead of requiring the
reader to infer which side is newer.

**Consequences:** Every finding in `RegressionReport` has an unambiguous
sign; `versioning.py`'s changelog generator (ADR-0006 Decision 5) can
directly label a finding "Improved" or "Regression" without additional
logic.

### 2. Default baseline selection reuses the existing "most recent" convention; always overridable

**Decision:** `scripts/compare_datasets.py` defaults `candidate` to the
most recently built dataset in `datasets_dir` and `baseline` to that
dataset's `version.json`'s `previous_artifact_id` (ADR-0006 Decision 4,
revised — the same-type version-history pointer, deliberately distinct
from ADR-0011's cross-type `lineage_parents`), if present. Both
`--baseline` and `--candidate` accept an explicit dataset directory to
override either side.

**Rationale:** Reuses the same-type version pointer already computed at
build time rather than re-deriving "which dataset came before this one" a
second way — one source of truth for dataset ordering (ADR-0004's "don't
duplicate existing functionality," applied within Phase 3.5 itself this
time). A `--baseline` override exists because a reviewer often wants to
compare against something other than "immediately previous" — e.g. the
last dataset that actually passed the training gate.

**Consequences:** If a dataset has no `previous_artifact_id` (the first
dataset ever built, or one produced by a batch job that never ran
`make version`), `compare_datasets.py` reports "no baseline available"
rather than guessing — the same explicit-absence handling as ADR-0006
Decision 5's empty-history changelog case.

### 3. Every comparison dimension maps to an existing field; nothing is recomputed from raw episodes

**Decision:** `RegressionReport` compares, pairwise, fields already present
in each dataset's `stats.json`, `quality_report.json`, and
`quality_score.json` (ADR-0005):

| Dimension | Source field(s) |
|---|---|
| Samples | `DatasetManifest.sample_count` |
| Episodes | `DatasetManifest.episode_count_included` |
| Steering distribution | `stats.json:steer` (`ValueStats`) + `steering_histogram` |
| Throttle / brake | `stats.json:throttle` / `brake` (`ValueStats`) |
| Towns | `stats.json:towns` (dict of town → episode count) |
| Weather | `stats.json:weather` (new field, symmetric to `towns` — ADR-0004 Decision 6) |
| Routes | `episodes_index.jsonl:route_name`, aggregated the same way as towns |
| Duplicates | `quality_report.json:duplicate_frame_groups` |
| Outliers | `quality_report.json:episodes_with_outliers` |
| Quality score | `quality_score.json:overall_score` + per-metric `metrics` |

**Rationale:** Every one of these fields is already computed and written
to disk by Phase 3a/3b or `scoring.py` (ADR-0005) — `regression.py` is
purely a file-reader and comparator, with zero dependency on raw episode
directories or frame files. This keeps `make compare-data` fast (no I/O
beyond a handful of small JSON files) regardless of dataset size.

**Consequences:** Comparing a pre-ADR-0004 dataset (missing `weather`) is
handled by reporting that dimension as "unavailable in baseline/candidate"
rather than a false "removed all weather coverage" finding.

### 4. Each dimension gets one of three severities: improvement, warning, or failure — decided by configurable thresholds, not by sign alone

**Decision:** `config/default.yaml` gains:

```yaml
quality_engineering:
  regression:
    warning_thresholds:            # candidate worse than baseline by more than this -> warning
      sample_count_drop_pct: 10.0
      quality_score_drop_pts: 5.0
      duplicate_rate_increase_pct: 2.0
      outlier_rate_increase_pct: 2.0
    failure_thresholds:             # candidate worse than baseline by more than this -> failure
      sample_count_drop_pct: 40.0
      quality_score_drop_pts: 15.0
      duplicate_rate_increase_pct: 10.0
      outlier_rate_increase_pct: 10.0
      town_or_weather_cell_lost: true   # any previously-covered town/weather cell now has 0 episodes
```

A dimension with no configured threshold (e.g. route diversity) is always
reported as **informational** (visible in the report, never gates
anything) until a threshold is added for it — thresholds are opt-in per
dimension, not implied by presence in the comparison table.

**Rationale:**
- A raw sign check ("candidate has fewer samples than baseline = bad") is
  too blunt — losing 2 samples out of 50,000 during a re-collection pass is
  noise, not a regression. Percent/point thresholds, config-owned exactly
  like ADR-0005's scoring thresholds, let a reviewer decide what magnitude
  of change is actually worth flagging, per the brief's "No magic numbers"
  requirement applied consistently across every ADR in this phase.
- Splitting into `warning_thresholds` / `failure_thresholds` (rather than
  one threshold with an implicit "double it for failure" rule) means the
  two severities are independently tunable — a team might want failures to
  be rare and warnings to be sensitive, or vice versa, and the config makes
  that an explicit choice rather than a derived one.
- `town_or_weather_cell_lost` is a boolean-style hard trigger (not a
  percentage) because losing all coverage of a previously-covered
  town/weather combination is categorically different from a proportional
  drop — it means the coverage planner's target matrix (ADR-0008) now has
  a hole that used to be filled.

**Consequences:** `gates.py` (ADR-0004) blocks training only on `failure`
severity by default (config-controlled — see ADR-0004 extension point 3),
so a `warning`-only regression report does not stop a rebuild from being
used, only flags it for human attention.

### 5. `RegressionReport` is one flat, ordered list of `RegressionFinding`, not a dimension-keyed dict

**Decision:**

```python
@dataclass
class RegressionFinding:
    dimension: str            # e.g. "sample_count", "quality_score.coverage", "town:Town10"
    baseline_value: Any
    candidate_value: Any
    delta: float | None       # candidate - baseline where numeric; None for non-numeric dims
    severity: str             # "improvement" | "warning" | "failure" | "informational"
    message: str

@dataclass
class RegressionReport:
    schema_version: str
    created_at: str
    artifact_type: str          # "dataset" today
    baseline_artifact_id: str | None
    candidate_artifact_id: str
    findings: list[RegressionFinding]
```

**Rationale:** Mirrors `QualityReport.issues` (`src/data/dataset_schemas.py`
— already a flat, ordered `list[QualityIssue]`) rather than introducing a
second convention for "list of graded findings" in the same repository. A
flat list is also trivially filterable (`[f for f in findings if
f.severity == "failure"]`) by every consumer — `gates.py`, `dashboard.py`,
and `versioning.py`'s changelog generator all need exactly that filter.

**Consequences:** Per-town and per-weather findings appear as individually
named dimensions (`"town:Town10"`, `"weather:Rain"`) rather than nested
under one "towns" finding — this makes "which specific town regressed"
directly greppable in the JSON output instead of requiring a second level
of parsing.

### 6. Regression detection is read-only and dataset-count-agnostic

**Decision:** `compare_datasets()` never modifies either dataset directory
and works identically whether comparing two large production datasets or
two `--dry-run` smoke-test datasets.

**Rationale:** Consistent with ADR-0004 Decision 7 (Phase 3.5 never mutates
build artifacts) and keeps `compare_datasets.py` usable inside
`make dataset-dry-run`-style smoke tests without special-casing dataset
size — the same code path that regression-tests a 50,000-sample production
rebuild is exercised by a 20-tick CI smoke test.
