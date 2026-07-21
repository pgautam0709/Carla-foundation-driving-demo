# ADR 0005 — Dataset Quality Scoring Strategy

**Date:** 2026-07-20 (revised same day — see Revision Note)
**Status:** Proposed
**Deciders:** AI Engineering Team

> **Revision note:** ADR-0004's registry is now one process-wide,
> category-based `MetricRegistry` rather than a dataset-only global
> constant, and `DatasetArtifacts` is now `DatasetArtifact` (a subtype of
> the generic `Artifact`). Decision 1 below is updated to register under
> category `"dataset"`; nothing about the six metrics, their formulas, or
> their weighting changes.

---

## Context

`quality_report.json` (Phase 3b) already contains every fact needed to
judge a dataset: alignment/validity counts, outlier counts, duplicate-frame
groups, and (in `stats.json`) a steering histogram. What it does not have is
a single number or grade a human — or a future gate in `gates.py` — can act
on without reading the raw counts and doing arithmetic in their head. This
ADR defines how `src/quality/scoring.py` turns those existing facts into
the `Dataset Quality` report shown in the brief:

```
Overall: A
Synchronization: 100%   Coverage: 91%   Metadata: 100%
Outliers: 1%            Duplicates: 0%  Steering Balance: Good
```

---

## Decisions

### 1. Six metrics, each independently registered, each normalized to 0–100

**Decision:** `src/quality/dataset_metrics.py` registers exactly six
metrics under category `"dataset"` in the shared `METRIC_REGISTRY`
(ADR-0004 Decision 6b):

| Metric | What it measures | Formula (using existing quality_report.json / stats.json fields) |
|---|---|---|
| `synchronization` | Alignment integrity | `100 * (episodes_scanned - episodes_misaligned) / episodes_scanned` (100 if `episodes_scanned == 0`) |
| `coverage` | Diversity vs. the configured target matrix (ADR-0008) | `100 * cells_meeting_minimum / total_target_cells` |
| `metadata` | Structural validity | `100 * episodes_valid / episodes_scanned` (100 if `episodes_scanned == 0`) |
| `outliers` | Signal-quality cleanliness | `100 * (1 - episodes_with_outliers / episodes_included)` (100 if `episodes_included == 0`) |
| `duplicates` | Frame uniqueness | `100 * (1 - duplicate_sample_count / sample_count)` (100 if `sample_count == 0`), using `QualityReport.duplicate_sample_count` (ADR-0004 Decision 6) — the number of samples belonging to any duplicate group, not the group count |
| `steering_balance` | Class balance of the steering histogram | `100 * (1 - normalized_entropy_deficit)` — see Decision 3 |

**Rationale:**
- Each metric is a direct, explainable function of a field that already
  exists in `quality_report.json` / `stats.json` — none require re-reading
  raw episode data, so `make quality` stays fast (already-computed inputs)
  and the brief's "No magic numbers" requirement is satisfied by construction
  (every constant in the formulas above is a count already on disk, not a
  tuned coefficient).
- Six is the number of rows the brief's own example output shows. Adding a
  seventh metric later is a one-file change (`dataset_metrics.py` +
  `config/default.yaml` weight entry) because of the registry pattern
  (ADR-0004 Decision 2) — it is not a special case in `scoring.py`.
- Normalizing every metric to a common `0–100` scale is what makes a single
  weighted sum meaningful; mixing raw counts and percentages would make the
  weights uninterpretable.

**Consequences:** A dataset with `episodes_scanned == 0` (nothing built
yet) reports every metric as 100 except `coverage`, which is legitimately 0
(no target cells covered). `gates.py` (ADR-0004 extension point 3) checks
`sample_count > 0` separately before trusting a score at all — a 100 score
on an empty dataset must never look "ready."

`dataset_metrics.py`'s `CoverageMetric.compute()` calls
`src.quality.coverage.compute_coverage()` directly and derives its
percentage from that result — it does not recompute the target-matrix
logic itself, so ADR-0008's coverage definition and this metric's
`coverage` score can never drift apart from each other.

### 2. Weights and grade thresholds are 100% configuration-owned

**Decision:** `config/default.yaml` gains:

```yaml
quality_engineering:
  scoring:
    weights:
      synchronization: 0.25
      coverage: 0.20
      metadata: 0.20
      outliers: 0.15
      duplicates: 0.10
      steering_balance: 0.10
    grade_thresholds:      # inclusive lower bound -> letter
      A: 90.0
      B: 80.0
      C: 70.0
      D: 60.0
      # anything below the lowest threshold is "F"
    steering_balance_qualitative_thresholds:  # numeric score -> label
      Good: 80.0
      Fair: 60.0
      # below Fair -> "Poor"
```

`scoring.py::compute_quality_score()` takes a `ScoringConfig` dataclass
(parsed once from this section) as an explicit parameter — it never reads
`config/default.yaml` itself. Weights need not sum to 1.0; `scoring.py`
normalizes them, mirroring the existing `assign_splits()` pattern in
`dataset_splits.py`, which already normalizes `split_ratios` the same way.

**Rationale:**
- Every number a reviewer might reasonably want to retune (what counts as
  "A", how much coverage matters relative to duplicates) lives in YAML, per
  AGENTS.md §3 and the brief's explicit requirement.
- Reusing the "don't require ratios to sum to 1" convention from
  `dataset_splits.py` avoids introducing a second, inconsistent rule for
  "how do weights normalize" elsewhere in the same repository.
- A dataclass parameter (not a raw `dict`) means `mypy --strict` catches a
  missing weight at the call site, and unit tests can construct a
  `ScoringConfig` inline without a YAML fixture file — matching
  `OutlierThresholds`' existing role in `dataset_outliers.py`.

**Consequences:** Two datasets scored under different `scoring:` config
(e.g. after a threshold change) are not directly comparable by grade alone
— `versioning.py` records the `config_hash` (ADR-0006) specifically so a
later reviewer can tell whether a grade change reflects the data or the
rubric.

### 3. Steering balance uses normalized histogram entropy, not a fixed bin-count rule

**Decision:** `steering_balance`'s raw score is derived from the Shannon
entropy of the existing `steering_histogram` bins (from `stats.json`,
already computed by Phase 3b — no new computation over raw samples):

```
H = -Σ p_i * log(p_i)   for each bin i with p_i = count_i / sample_count, p_i > 0
H_max = log(bin_count)                          # entropy of a perfectly uniform histogram
raw_score = 100 * H / H_max                      # 100 = perfectly uniform, 0 = all mass in one bin
```

**Rationale:**
- A single fixed rule like "at least N% of samples must be near-zero
  steer" would bake in an assumption about what a *good* driving dataset
  looks like — real routes are straight more often than they turn, so a
  uniform histogram is not actually the target, only the mathematically
  convenient reference point for "how concentrated is this distribution."
  Entropy relative to the maximum-possible entropy for the configured bin
  count is a distribution-shape-agnostic way to say "how balanced is this,"
  without asserting a specific target shape — that judgment is left to the
  human reading the review (`review.py`, ADR-0004), not encoded as a pass/
  fail rule.
- Reuses `stats.json`'s `steering_histogram_bins` config value that Phase
  3b already introduced — no second histogram is computed, and the
  "informational only, never resamples" rule from ADR-0003 Decision 1
  continues to hold: this metric reads the histogram, it never changes how
  the histogram (or any sampling) is computed.

**Consequences:** A dataset collected entirely on a straight highway scores
low on `steering_balance` even if every episode is otherwise pristine —
this is by design (it is genuinely narrow steering coverage) and shows up
as a named weakness in `review.py`'s output rather than a silent low
overall grade with no explanation.

### 4. The overall score is a weighted arithmetic mean, not a weakest-link (minimum) score

**Decision:** `overall = Σ (weight_i * metric_i) / Σ weight_i`.

**Rationale:**
- A minimum-of-metrics approach was considered (and rejected) because a
  single low-weight metric (e.g. `duplicates` at 10% weight) would then
  cap the entire grade regardless of how good the other five metrics are,
  effectively giving every metric emergency-veto power irrespective of its
  configured weight — that contradicts the point of having configurable
  weights at all.
- A weighted mean keeps `gates.py` (ADR-0004) and `weights` config mutually
  consistent: the same weights that determine the letter grade also
  determine training-gate pass/fail, so there is exactly one place
  ("what matters, and how much") a reviewer needs to tune.

**Rejected alternative — weakest-link minimum:** kept as a documented
non-choice because it is the natural first idea for a "quality gate," but
it does not compose with configurable weights (see above) and it collapses
the six-metric breakdown back into effectively one metric (whichever is
currently lowest), defeating the purpose of reporting all six.

**Consequences:** A gate that specifically wants to hard-block on one metric
(e.g. never train on data with `synchronization < 100`) does so as its own
named check in `gates.py`, independent of the blended overall score — the
overall score answers "how good is this dataset in aggregate," a gate check
answers "is this one specific thing acceptable," and the two are
deliberately not conflated into a single number.

### 5. `QualityScore` records every sub-metric and the exact config used to produce it

**Decision:** `scoring.py` returns (and `dataset_quality.py` writes to
`quality_score.json` alongside the dataset's other artifacts):

```python
@dataclass
class QualityScore:
    schema_version: str
    created_at: str
    dataset_id: str
    overall_score: float          # 0-100
    grade: str                    # "A".."F"
    metrics: dict[str, MetricResult]   # name -> {raw_score, weight, detail}
    weights_used: dict[str, float]
    grade_thresholds_used: dict[str, float]
```

**Rationale:** A grade with no visible sub-scores is not actionable — the
brief's own example output shows the breakdown, not just "A". Recording
`weights_used` / `grade_thresholds_used` inline (not just relying on
`versioning.py`'s `config_hash`) means `quality_score.json` is
self-explaining without cross-referencing the config history, satisfying
"a new engineer should be able to answer 'is this dataset trustworthy'
without manually inspecting JSON files" — a single readable JSON file (or
`make quality`'s formatted stdout) already answers it.

**Consequences:** `quality_score.json` grows by one line whenever a metric
is added, but is never restructured — `metrics` is an open dict precisely
so new metrics do not require a schema migration to appear (only a schema
_version_ bump if a field's meaning changes, per existing Phase 3 schema
versioning convention).
