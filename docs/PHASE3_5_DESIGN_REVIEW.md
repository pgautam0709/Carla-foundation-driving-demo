# Phase 3.5 — Design Review

**Companion to:** `docs/ADR/0004`–`0010`, `docs/ARCHITECTURE_REVIEW.md`
**Purpose:** Full pre-implementation design presentation — read this before
any `src/quality/` code is written.
**Status:** Design complete, one additional duplication caught during this
review (Finding D, below) and folded in before implementation starts.

---

## 1. ADR-0004 — Engineering Loop Architecture

**Executive summary.** Phases 0–3b are a linear pipeline that stops at "a
dataset got built." Nothing today scores that dataset, compares it to the
last one, or stops a future trainer from running against a bad build.
ADR-0004 introduces a new package, `src/quality/`, that sits *after*
`src/data/` in the dependency graph — it only reads what
`dataset_builder.py` already writes, never the other way around — and
turns those files into a standing, queryable engineering record. It
does not add a pipeline stage; it adds a loop around the existing
Evaluate/Review point in Collect → Validate → Evaluate → Review → Improve
→ Train → Compare → Deploy → Monitor.

Ten single-responsibility modules replace what could have been one large
`quality.py`. Six of them compute something (`artifacts`, `metrics`,
`dataset_metrics`, `scoring`, `versioning`, `regression`, `coverage` —
seven, not six); three are composition-only (`review`, `gates`,
`dashboard`) and contain no scoring math or comparison logic of their own
— they only format what the computing modules already returned. This
split is what makes ADR-0010's "future phases plug in without redesign"
claim true: a new metric, a new dashboard section, or a new gate check is
always an addition to a registry or a list, never a change to control
flow.

Two things were deliberately *not* built: no database, no background
service, no network port. Every module is a pure function of on-disk
files, matching the repository's existing "components communicate via
files" philosophy (`docs/ARCHITECTURE.md`). This is what keeps the whole
phase portable across macOS/Linux/Windows/Docker/CI with zero new runtime
dependencies.

**Problem being solved.** There is no automated answer to "is this
dataset trustworthy," "what changed since the last build," or "should
training be allowed to start" — a human has to open four or five JSON
files and do arithmetic by hand.

**Alternatives considered.**
- *One large `src/quality/quality.py` module.* Rejected — would grow a
  second, unrelated job onto whichever function got there first, and
  gives Phase 4+ no clear seam to extend (see ADR-0004 Decision 2).
- *A live dashboard service / database-backed quality store.* Rejected —
  breaks the file-based philosophy that lets every other component in
  this repo be inspected with a text editor, and is the kind of
  infrastructure this repo has deliberately avoided since Phase 0 (see
  ADR-0004 Decision 3).
- *Folding quality logic into `src/data/dataset_builder.py` directly.*
  Rejected — conflates "produce a dataset" with "judge a dataset that
  already exists"; also would have made `src/data/` depend on
  scoring/versioning logic that Phase 4+ needs independent of any single
  build.

**Final design decision.** New `src/quality/` package, ten modules, file-
in/file-out only, one-way dependency on `src/data/`, four named extension
points (metric registry, dashboard section list, gate-check list,
regression comparator) that ADR-0010 proves are sufficient for Phases
4–6.

**Module dependency diagram** (within `src/quality/`; arrows read "depends
on"):

```
schemas.py  ◄────────────────────────────────────────────┐
   ▲                                                      │
   │                                                      │
config.py ──► artifacts.py ──► metrics.py ──► dataset_metrics.py
   │              │                                  │        │
   │              │                                  ▼        │
   │              │                             scoring.py     │
   │              │                                  │         │
   │              ├──────────────► coverage.py ◄─────┘         │
   │              │                    │                       │
   │              └──────────────► regression.py                │
   │                                   │                       │
   │                                   ▼                       │
   │                             versioning.py                 │
   │                                                            │
   └──► review.py ◄── scoring.py, coverage.py, regression.py ──┘
   └──► gates.py  ◄── scoring.py, coverage.py, regression.py
   └──► dashboard.py ◄── scoring.py, versioning.py, regression.py, coverage.py, gates.py
```

`src/data/dataset_schemas.py`, `dataset_io.py`, and `episode.py` sit
"below" all of `src/quality/` — every module above may import from them;
none of them imports from `src/quality/`.

**Public interfaces (package-level).**
```python
# src/quality/artifacts.py
@dataclass
class DatasetArtifacts:
    dataset_dir: Path
    manifest: DatasetManifest
    episodes: list[EpisodeIndexEntry]
    stats: DatasetStatistics
    quality_report: QualityReport
    samples: list[SampleRecord] | None   # loaded only if requested — can be large

def load_dataset_artifacts(dataset_dir: Path, *, load_samples: bool = False) -> DatasetArtifacts: ...
def hash_content(obj: Any) -> str: ...                              # full SHA-256 hex digest
def resolve_latest_dataset_dir(datasets_dir: Path) -> Path | None: ...  # shared "most recent" resolver

# src/quality/config.py
def load_quality_config(cfg: ConfigDict) -> QualityEngineeringConfig: ...  # parses quality_engineering: section

# src/quality/metrics.py
class Metric(Protocol):
    name: str
    def compute(self, artifacts: DatasetArtifacts, cfg: QualityEngineeringConfig) -> MetricResult: ...

class MetricRegistry:
    def register(self, metric: Metric) -> None: ...
    def all(self) -> list[Metric]: ...

DATASET_METRIC_REGISTRY: MetricRegistry
```

**Configuration additions.** Top-level `quality_engineering:` key in
`config/default.yaml`, sub-keyed per module (`scoring`, `coverage`,
`regression`, `gates`, `dashboard`, `versioning`) — full contents are in
ADR-0005 through ADR-0009; §9 below shows the merged block as it will
actually appear in the file.

**Risks / trade-offs.**
- *Risk:* a one-way dependency is only as good as code review enforcing
  it — nothing in `mypy`/`ruff` config currently blocks `src/data/`
  importing `src/quality/`. *Mitigation:* call this out explicitly in
  `AGENTS.md`'s Phase 3.5 section (planned doc update, §10 below) the same
  way existing phase boundaries are documented there today.
- *Trade-off:* ten small modules is more files to navigate than one big
  one, in exchange for every file having exactly one reason to change.
  Judged worth it given this repo's existing bias toward narrow modules
  (`dataset_alignment.py` vs. `dataset_splits.py` vs.
  `dataset_statistics.py` already set this precedent in Phase 3a).

**Supports Phase 4 / future foundation models by:** giving BC-CNN,
Diffusion, Foundation Models, RL, and VLA training code exactly one
integration contract — register a metric, append a gate check, append a
dashboard section — regardless of which architecture Phase 4 ends up
picking. The architecture is intentionally blind to *what* produces a
metric value, only that it has a name, a score, and a detail string.

---

## 2. ADR-0005 — Quality Scoring Strategy

**Executive summary.** `quality_report.json` and `stats.json` already
contain every fact needed to grade a dataset; nothing turns those facts
into the single letter grade + six-metric breakdown shown in the brief.
`scoring.py` computes six independently-registered metrics
(`synchronization`, `coverage`, `metadata`, `outliers`, `duplicates`,
`steering_balance`), each normalized to 0–100 from fields Phase 3a/3b
already compute, then combines them via a **configurable weighted mean**
(not a weakest-link minimum, which was considered and rejected because it
would let one low-weight metric veto the whole grade regardless of its
configured weight) into an overall score and letter grade.

Every weight and grade boundary lives in
`quality_engineering.scoring` — nothing in `dataset_metrics.py` or
`scoring.py` hardcodes what counts as an "A." The `steering_balance`
metric specifically uses normalized Shannon entropy of the existing
steering histogram rather than a fixed target shape, because asserting
"steering should look like X" would bake in an assumption about what
"good" driving data looks like that this repository has no basis to make.

**Problem being solved.** No single, explainable, comparable number
exists for "how good is this dataset," and no sub-metric breakdown exists
to explain *why* a given grade was assigned.

**Alternatives considered.**
- *Weakest-link (minimum of six metrics) scoring* — rejected; collapses
  the six-metric breakdown into effectively one metric and makes weights
  meaningless (see ADR-0005 Decision 4).
- *A fixed steering-histogram shape check ("must have ≥X% near-zero
  bins")* — rejected; encodes an assumption about correct route
  composition this repo has no evidence for. Entropy-vs-uniform is
  shape-agnostic (Decision 3).
- *Hashing the whole `config/default.yaml` into the scoring config
  fingerprint* — considered as part of versioning (ADR-0006), not
  scoring, and rejected there for the same reason: sections unrelated to
  dataset building would cause false "changed" signals.

**Final design decision.** Six registered metrics, config-owned weights
(normalized, need not sum to 1 — reusing `dataset_splits.py`'s existing
normalization convention), config-owned grade thresholds, weighted-mean
overall score, full breakdown persisted to `quality_score.json`.

**Module dependency diagram.**
```
dataset_metrics.py ──registers into──► metrics.py::DATASET_METRIC_REGISTRY
        │                                        ▲
        │ (CoverageMetric calls)                 │ (scoring.py reads)
        ▼                                        │
   coverage.py                              scoring.py ──writes──► quality_score.json
```

**Public interfaces.**
```python
# src/quality/dataset_metrics.py
class SynchronizationMetric: ...
class CoverageMetric: ...          # delegates to coverage.compute_coverage()
class MetadataMetric: ...
class OutlierMetric: ...
class DuplicateMetric: ...
class SteeringBalanceMetric: ...
def register_dataset_metrics(registry: MetricRegistry = DATASET_METRIC_REGISTRY) -> None: ...

# src/quality/scoring.py
def compute_quality_score(
    artifacts: DatasetArtifacts, cfg: QualityEngineeringConfig,
) -> QualityScore: ...
def write_quality_score(dataset_dir: Path, score: QualityScore) -> None: ...
```

**Configuration additions.**
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
    grade_thresholds: {A: 90.0, B: 80.0, C: 70.0, D: 60.0}
    steering_balance_qualitative_thresholds: {Good: 80.0, Fair: 60.0}
```

**Risks / trade-offs.**
- *Risk:* entropy-based steering balance is less intuitive to a first-time
  reader than a fixed rule. *Mitigation:* `detail` string on the metric
  result always includes the plain-language bin distribution, not just
  the entropy number — `docs/QUALITY_SYSTEM.md` documents the formula with
  a worked example.
- *Trade-off:* a weighted mean can produce a "B" dataset where one metric
  is genuinely poor (e.g. duplicates at 40%) if other metrics are high
  enough. This is why `gates.py` (ADR-0004 extension point 3) supports
  hard per-metric floors *independent* of the blended grade — the overall
  score answers "how good in aggregate," a gate answers "is this one thing
  acceptable," deliberately not conflated (ADR-0005 Decision 4
  consequence).

**Supports Phase 4 by:** giving `model_metrics.py` (Phase 4) the exact
same `Metric` contract to implement — a future "is this checkpoint
converged" score is graded, weighted, and thresholded with the identical
machinery, not a bespoke training-specific scoring path.

---

## 3. ADR-0006 — Dataset Versioning

**Executive summary.** Phase 3a already solved "don't clobber the last
build" (every build gets its own `datasets_dir/<dataset_id>/`). What's
missing is treating each build like a software release: a content-hashed
identity, a lineage pointer to what came before it, and a generated
changelog. `versioning.py` adds exactly one new file per dataset,
`version.json`, plus a generated `CHANGELOG.md` — it does not rename or
restructure `dataset_id` or the existing manifest.

Hashes (`config_hash`, `manifest_hash`, `statistics_hash`,
`quality_report_hash`) are computed over **canonicalized content**
(sorted-key JSON, full SHA-256 digest), reusing the exact canonicalization
strategy `compute_route_hash()` already established in Phase 2 — but
without that function's 8-character truncation, since these hashes exist
for provenance verification, not for compact filenames. `config_hash`
deliberately covers only `dataset_engineering:` + `quality_engineering:`
— not the whole config file — so it only changes when something that
actually affects the dataset changes.

**Problem being solved.** No way to answer "which exact commit and config
produced this dataset," "what changed since the last build," or "can this
be reproduced" without manual cross-referencing.

**Alternatives considered.**
- *Renaming/restructuring `dataset_id` into a `v1`/`v2`/`v3` scheme* —
  rejected; `dataset_id` is already a stable identity Phase 3a callers
  rely on (timestamped or explicit `--dataset-id`); replacing it would
  break existing behavior for no benefit. `version.json` adds release
  semantics *on top of* the existing identity instead.
- *Hashing raw file bytes instead of canonicalized content* — rejected;
  produces false "changed" signals from re-indentation or line-ending
  differences (Decision 2).
- *A full lineage DAG (branches/merges between datasets)* — rejected;
  every dataset in this repo is built from the same growing episode pool,
  so a single `previous_dataset_id` pointer (walkable backward) is
  sufficient — no branch/merge concept exists for this data (Decision 4).
- *A hand-maintained `CHANGELOG.md`* — rejected; drifts from reality by
  construction and can't be backfilled. Generated from `regression.py`'s
  comparison instead (Decision 5).

**Final design decision.** `version.json` alongside existing dataset
artifacts; full-digest canonical-content hashes; single backward
`previous_dataset_id` pointer defaulting to "most recent dataset before
this one" (same convention `inspect_dataset.py` already uses);
`CHANGELOG.md` generated entirely from `regression.py`, never hand-edited.

**Module dependency diagram.**
```
artifacts.py::hash_content() ──► versioning.py ──► version.json
regression.py::compare_datasets() ──► versioning.py::generate_changelog() ──► CHANGELOG.md
src.data.episode.get_git_commit() ──► versioning.py
```

**Public interfaces.**
```python
# src/quality/versioning.py
def compute_version_record(
    dataset_dir: Path, cfg: QualityEngineeringConfig,
    previous_dataset_id: str | None = None,   # None = auto-resolve
) -> VersionRecord: ...
def generate_changelog(
    dataset_dir: Path, version: VersionRecord, cfg: QualityEngineeringConfig,
) -> str: ...    # markdown text; caller writes CHANGELOG.md
def write_version_artifacts(dataset_dir: Path, cfg: QualityEngineeringConfig) -> VersionRecord: ...
```

**Configuration additions.**
```yaml
quality_engineering:
  versioning:
    changelog_filename: "CHANGELOG.md"
    version_filename: "version.json"
```

**Risks / trade-offs.**
- *Risk:* `previous_dataset_id` resolved by wall-clock "most recent" can
  be wrong if datasets are rebuilt out of chronological order.
  *Mitigation:* always overridable (`--previous-dataset-id` on
  `make version`); documented explicitly as an edge case in
  `docs/DATASET_VERSIONING.md` (Decision 4 consequence).
- *Trade-off:* versioning is a separate, explicit step (`make version`),
  not automatic at build time — means a freshly-built dataset has no
  `version.json` until a reviewer runs it. Deliberate: keeps
  `build_dataset()` fast and CARLA/DB-free, and lets `config_hash` be
  recomputed independently after a scoring-config change without
  rebuilding data (Decision 6).

**Supports Phase 4 by:** giving a future checkpoint a `dataset_id` +
`config_hash` + `manifest_hash` to record at training time — Phase 4's
gate check ("does this checkpoint's recorded dataset version match what
we're about to evaluate against") is a direct read of fields this ADR
already defines, no new provenance format needed (ADR-0010 Decision 2).

---

## 4. ADR-0007 — Regression Detection

**Executive summary.** Two dataset builds can be compared today only by
opening both `stats.json` files side by side. `regression.py` formalizes
this into `compare_datasets(baseline_dir, candidate_dir, cfg) ->
RegressionReport`: an always-directional (`candidate` vs. `baseline`,
never symmetric) diff across ten dimensions (samples, episodes, steering,
throttle, brake, towns, weather, routes, duplicates, outliers, quality
score), where every dimension's finding is classified as `improvement`,
`warning`, `failure`, or `informational` using **independently
configurable** warning/failure thresholds per dimension — not a blanket
"worse than baseline = bad" rule, since a two-sample drop out of 50,000 is
noise, not a regression.

Every comparison reads only already-computed fields
(`stats.json`/`quality_report.json`/`quality_score.json`) — nothing here
re-scans raw episode or frame data, which is what keeps `make
compare-data` fast regardless of dataset size.

**Problem being solved.** No automated way to know whether a rebuilt
dataset actually got better, and no severity classification to decide
whether a change is worth blocking on.

**Alternatives considered.**
- *Symmetric "these two datasets differ" diff* — rejected; can't answer
  "did this get better or worse," which is the actual question a reviewer
  is asking (Decision 1).
- *A single global threshold applied to every dimension* — rejected; a 2%
  duplicate-rate increase and a 2% sample-count drop are not equally
  serious. Per-dimension, config-owned thresholds instead (Decision 4).
- *Sign-only severity (any negative delta = regression)* — rejected as too
  noisy; magnitude-aware thresholds chosen instead.
- *Nested dimension-keyed report (`{"towns": {...}, "steering": {...}}`)*
  — rejected in favor of a flat `list[RegressionFinding]`, mirroring the
  existing `QualityReport.issues` convention already used in Phase 3b, so
  every consumer filters the same shape one way (`severity == "failure"`).

**Final design decision.** Directional comparator, ten fixed dimensions
sourced from existing artifacts, per-dimension configurable
warning/failure thresholds (with a `town_or_weather_cell_lost` hard
trigger), flat ordered `RegressionFinding` list.

**Module dependency diagram.**
```
artifacts.py::load_dataset_artifacts() ──► regression.py::compare_datasets() ──► RegressionReport
                                                    │                                  │
                                                    ├──► versioning.py (changelog)     │
                                                    ├──► gates.py (block on failure)   │
                                                    └──► dashboard.py ◄────────────────┘
```

**Public interfaces.**
```python
# src/quality/regression.py
def compare_datasets(
    baseline: DatasetArtifacts | None, candidate: DatasetArtifacts, cfg: QualityEngineeringConfig,
) -> RegressionReport: ...
def compare_metric_snapshots(       # reused by Phase 5 for eval-run comparison (ADR-0010 §3)
    baseline: dict[str, float] | None, candidate: dict[str, float],
    thresholds: RegressionThresholds,
) -> list[RegressionFinding]: ...
```

**Configuration additions.**
```yaml
quality_engineering:
  regression:
    warning_thresholds:
      sample_count_drop_pct: 10.0
      quality_score_drop_pts: 5.0
      duplicate_rate_increase_pct: 2.0
      outlier_rate_increase_pct: 2.0
    failure_thresholds:
      sample_count_drop_pct: 40.0
      quality_score_drop_pts: 15.0
      duplicate_rate_increase_pct: 10.0
      outlier_rate_increase_pct: 10.0
      town_or_weather_cell_lost: true
```

**Risks / trade-offs.**
- *Risk:* ten fixed dimensions may miss a regression type not yet
  anticipated (e.g. a specific route disappearing). *Mitigation:* routes
  are already reported as an `informational` dimension (visible, not
  gated) — promoting it to a thresholded dimension later is a config-only
  change (Decision 3 consequence).
- *Trade-off:* comparator is deliberately not statistical (no significance
  testing) — a design choice consistent with this repo's "no magic
  numbers, but also no unexplainable statistics" bias; thresholds are
  plain percentages a non-statistician reviewer can reason about.

**Supports Phase 4/5 by:** `compare_metric_snapshots()` is the literal
reuse point ADR-0010 §3 names for Phase 5 eval-run-vs-eval-run comparison
— same function, different caller, zero new comparator code needed.

---

## 5. ADR-0008 — Coverage Planning

**Executive summary.** `stats.json` already reports episode counts per
town, but nothing says what counts as "enough," or which conditions are
completely missing. `coverage.py` defines coverage against a
**configured target matrix** — the Cartesian product of
`quality_engineering.coverage.target_towns × target_weather` — counts
included episodes per `(town, weather)` cell from the (newly indexed)
`weather` field, and flags each cell `met` once it has at least
`min_episodes_per_cell` episodes. `recommend_collection()` then ranks
unmet cells deterministically (zero-coverage cells first, then fewest
existing episodes, then alphabetical) — no randomness, because two runs
against the same inputs must produce the same recommendation list.

Route diversity and per-split (train/val/test) town/weather coverage are
reported informationally alongside the gated matrix, but are not
themselves targets — route names aren't yet a closed, standardized set the
way CARLA towns and weather presets are, so gating on them would imply a
precision the data doesn't support yet.

**Problem being solved.** No definition of "enough diversity," and no
automated, non-random guidance for what to collect next.

**Alternatives considered.**
- *Inferring targets from the data itself ("target = whatever's been
  collected so far")* — rejected; makes coverage trivially 100% the
  moment collection variety stops, defeating the purpose.
- *A three-dimensional town × weather × route target matrix* — rejected
  for now; route names are free text with no enumerable set today
  (Decision 1's Rejected Alternative) — deferred as a config-only
  follow-up once route naming is standardized.
- *Weighted/optimization-based recommendation ranking* — rejected in favor
  of simple, deterministic sort keys; a solver would add complexity and
  a new dependency for a ranking problem simple sorting already solves
  exactly (Decision 2).

**Final design decision.** Config-declared town × weather target matrix
with a per-cell minimum; deterministic zero-coverage-first ranking capped
at `max_recommendations`; route/split coverage reported but not gated;
read-only — never triggers a collection run itself.

**Module dependency diagram.**
```
artifacts.py::load_dataset_artifacts() ──► coverage.py::compute_coverage() ──► CoverageResult
                                                        │
                                                        ▼
                                          coverage.py::recommend_collection() ──► list[CoverageRecommendation]
                                                        │            │              │
                                                        ▼            ▼              ▼
                                          dataset_metrics.py    review.py     dashboard.py
                                          (CoverageMetric)
```

**Public interfaces.**
```python
# src/quality/coverage.py
def compute_coverage(artifacts: DatasetArtifacts, cfg: QualityEngineeringConfig) -> CoverageResult: ...
def recommend_collection(
    coverage: CoverageResult, cfg: QualityEngineeringConfig,
) -> list[CoverageRecommendation]: ...
```

**Configuration additions.**
```yaml
quality_engineering:
  coverage:
    target_towns: ["Town01", "Town02", "Town03", "Town04", "Town05", "Town10"]
    target_weather: ["ClearNoon", "CloudyNoon", "WetNoon", "HardRainNoon", "ClearSunset", "ClearNight"]
    min_episodes_per_cell: 3
    max_recommendations: 5
```

**Risks / trade-offs.**
- *Risk:* target matrix values (towns/weather presets) must be kept in
  sync with what CARLA/Phase 2 actually support — a typo'd town name in
  config silently makes a cell permanently unmet. *Mitigation:*
  `docs/QUALITY_SYSTEM.md` cross-references the exact preset names
  `expert_collection`/`simulation.weather` already use elsewhere in
  `config/default.yaml`, and a unit test asserts the default target list
  only contains values that also appear in existing collected-episode
  fixtures used across the test suite.
- *Trade-off:* 36 cells (6 towns × 6 weather) is a lot of coverage to
  reach in a small demo project — this is intentionally the *target*, not
  an expectation that it's fully met today; `coverage_pct` is expected to
  be low initially and improve over time, which is exactly what the
  quality-trend dashboard section is for.

**Supports Phase 4/5/6 by:** nothing directly — coverage is a Phase 3.5-
only concept (dataset diversity). It indirectly supports Phase 4 by
feeding the `coverage` quality metric and gate check that decide whether a
dataset is diverse enough to train on.

---

## 6. ADR-0009 — Engineering Dashboard

**Executive summary.** By the time scoring, versioning, regression, and
coverage all exist, a reviewer still has four separate JSON files to
cross-reference. `dashboard.py` renders all of them into **one
self-contained static HTML file** — inline CSS, inline hand-generated SVG
for the quality-trend chart, zero CDN/JS-framework dependency — so it
opens correctly with no network access on any OS. The page is built from
an **ordered list of pluggable sections**
(`Header`, `Quality`, `Coverage`, `Validation Gate`, `Recent Changes`,
`Quality Trend`), each a `(title, order, render(context) -> html)` triple
— this is the literal mechanism that satisfies "future phases add Model/
Inference/Simulation/Deployment Metrics sections without architectural
changes" (ADR-0010).

Every section is composition-only: it formats a dataclass another module
already computed. This boundary is restated explicitly in ADR-0009 (not
just inherited from ADR-0004) because the dashboard is the module most
tempted to accumulate its own logic, being the most visible surface.

**Problem being solved.** No single human-facing view answers "is this
trustworthy," "what changed," "is quality improving," and "is training
allowed" together, without manual JSON inspection.

**Alternatives considered.**
- *A running dashboard server (Flask/FastAPI + live refresh)* — rejected;
  first component in the repo to break the file-based philosophy, for a
  reporting-only phase (ADR-0004 Decision 3 restated here specifically for
  the dashboard, the most tempting place to break it).
- *A third-party charting library (Plotly/Chart.js via CDN)* — rejected;
  would be the single heaviest dependency in a deliberately dependency-
  light codebase (same trade-off ADR-0003 already made for duplicate
  detection), and breaks on an air-gapped machine. Hand-rolled inline SVG
  polyline instead.
- *A fixed six-section template hardcoded into `generate_dashboard()`* —
  rejected; directly contradicts the "no redesign for future sections"
  requirement. Pluggable section list instead.

**Final design decision.** One static HTML file, pluggable ordered
section list, inline hand-generated SVG trend chart with a plain-table
fallback, composition-only sections, "Future Training Readiness" section
renders every configured gate check verbatim (not just a pass/fail
badge).

**Module dependency diagram.**
```
scoring.py, versioning.py, regression.py, coverage.py, gates.py
        │  (all read via artifacts.py)
        ▼
dashboard.py::DashboardContext ──► [Header, Quality, Coverage, Validation,
                                     Recent Changes, Quality Trend] sections
        │
        ▼
   dataset_dashboard.html
```

**Public interfaces.**
```python
# src/quality/dashboard.py
@dataclass
class DashboardSection:
    title: str
    order: int
    render: Callable[["DashboardContext"], str]

DATASET_SECTIONS: list[DashboardSection]

def build_dashboard_context(dataset_dir: Path, cfg: QualityEngineeringConfig) -> DashboardContext: ...
def generate_dashboard(dataset_dir: Path, cfg: QualityEngineeringConfig, output_path: Path | None = None) -> Path: ...
```

**Configuration additions.**
```yaml
quality_engineering:
  dashboard:
    output_dir: "outputs/dashboard"
    trend_window: 10       # max historical datasets shown in the trend chart
```

**Risks / trade-offs.**
- *Risk:* hand-rolled SVG is more code to maintain than an off-the-shelf
  chart. *Mitigation:* scope is deliberately minimal — one series, no
  interactivity — kept small enough that golden-output tests can assert
  its structure directly.
- *Trade-off:* "regenerate on demand" (no live refresh) means a stale
  dashboard is possible if a reviewer forgets to re-run `make dashboard`.
  Accepted because it matches every other artifact in this repo (nothing
  auto-regenerates); `created_at` is stamped prominently in the header so
  staleness is at least visible.

**Supports Phase 4/5/6 by:** being literally where their metrics become
visible to a human — `ModelMetricsSection` / `SimulationMetricsSection` /
`DeploymentMetricsSection` append to the same list this ADR defines
(ADR-0010 §§2–4).

---

## 7. ADR-0010 — Future ML Integration

**Executive summary.** This ADR is the proof, not a new design — it walks
Phase 4 (BC/Diffusion/Foundation Models/VLA), Phase 5 (closed-loop
evaluation), and Phase 6 (deployment packaging) each through the same four
extension points named in ADR-0004, and confirms none of them need a fifth
mechanism invented from scratch. All four training-time model families in
Phase 4 integrate *identically* — a `model_metrics.py`, a dashboard
section, and one gate check checking dataset/checkpoint provenance match
(reusing `VersionRecord` fields ADR-0006 already defines) — because none
of them change what a "model metric" *is*, only what produces it.

Phase 5 specifically reuses `regression.py`'s comparator
(`compare_metric_snapshots`) to diff two evaluation runs the same way
Phase 3.5 diffs two datasets — confirming the comparator's contract
("two named metric snapshots + configurable thresholds") was built
dataset-shape-agnostic from the start, not just dataset-shaped by
coincidence.

Deliberately **not** built in this phase: `model_metrics.py`,
`simulation_metrics.py`, `deployment_metrics.py` themselves, or
`training_report.json`'s schema. Only the registry/list/interface
mechanisms they will plug into. Defining those files now, before a model
exists, would mean guessing at their contents — the same premature-design
trap ADR-0003 already flagged and avoided for near-duplicate detection.

**Problem being solved.** Preventing Phase 4/5/6 from inventing a parallel
scoring/dashboard/gating system instead of extending this one.

**Alternatives considered.** None distinct from ADR-0004's own
alternatives — this ADR is verification, not a new decision surface. The
one explicit non-choice: pre-building `model_metrics.py` etc. now, as
scaffolding — rejected as speculative design against requirements that
don't exist yet (§5).

**Final design decision.** No new code beyond what ADR-0004–0009 already
specify; this ADR is the acceptance test confirming that specification is
sufficient, walked through per future phase.

**Module dependency diagram.** N/A — no new modules in this ADR. Future
diagram (Phase 4, illustrative only, not built now):
```
src/quality/model_metrics.py ──registers into──► metrics.py::MODEL_METRIC_REGISTRY (new, parallel to DATASET_METRIC_REGISTRY)
src/quality/gates.py ──gains──► "checkpoint provenance matches dataset version" check
src/quality/dashboard.py ──gains──► ModelMetricsSection appended to a phase-agnostic section list
```

**Public interfaces.** None added now. Phase 4 will add:
```python
# src/quality/model_metrics.py (Phase 4, not this phase)
class ValidationLossMetric: ...     # implements the same Metric protocol from ADR-0004
def register_model_metrics(registry: MetricRegistry) -> None: ...
```

**Configuration additions.** None now — Phase 4 adds
`quality_engineering.model_gates` / `.regression.model_eval` sections
following the exact pattern this phase establishes.

**Risks / trade-offs.**
- *Risk:* an extension point that looks sufficient on paper can still
  turn out cramped once a real Phase 4 model exists. *Mitigation:* every
  interface in ADR-0004–0009 (`Metric`, `DashboardSection`, `GateCheck`,
  the regression comparator) takes a context object, not a fixed
  parameter list — a future phase can extend the context dataclass with
  new fields without changing the interface signature itself.
- *Trade-off:* deliberately leaves `training_report.json`'s schema
  undefined — Phase 4 has more up-front design work of its own to do
  before touching `src/quality/`, rather than inheriting a schema guessed
  at today.

**Supports Phase 4/5/6 by:** definition — this is the ADR whose entire
purpose is showing the fit. See per-module "Supports Phase 4..." lines in
§§1–6 above for the specific mechanism each future phase uses.

---

## 8. Repository Tree — Every New File

```
carla-foundation-driving-demo/
├── config/
│   └── default.yaml                          [MODIFIED — +quality_engineering: section]
├── docs/
│   ├── ADR/
│   │   ├── 0004-engineering-loop-architecture.md      [DONE]
│   │   ├── 0005-quality-scoring-strategy.md           [DONE]
│   │   ├── 0006-dataset-versioning.md                 [DONE]
│   │   ├── 0007-regression-detection.md               [DONE]
│   │   ├── 0008-coverage-planning.md                  [DONE]
│   │   ├── 0009-engineering-dashboard.md               [DONE]
│   │   └── 0010-future-ml-integration.md               [DONE]
│   ├── ARCHITECTURE_REVIEW.md                          [DONE]
│   ├── PHASE3_5_DESIGN_REVIEW.md                       [DONE — this file]
│   ├── ENGINEERING_LOOPS.md                            [NEW — implementation phase]
│   ├── QUALITY_SYSTEM.md                               [NEW]
│   ├── DATASET_VERSIONING.md                           [NEW]
│   ├── REGRESSION_DETECTION.md                         [NEW]
│   ├── ARCHITECTURE_DECISIONS.md                       [NEW — index over ADR 0001-0010]
│   ├── PHASE3_5_IMPLEMENTATION_REPORT.md                [NEW — written last, after self-review]
│   ├── PHASES.md                                        [MODIFIED — Phase 3.5 section]
├── README.md                                            [MODIFIED — commands + phase table]
├── .agents/AGENTS.md                                    [MODIFIED — Phase 3.5 awareness note]
├── Makefile                                             [MODIFIED — 6 new targets]
├── src/
│   ├── data/
│   │   ├── dataset_schemas.py                 [MODIFIED — +weather, +duplicate_sample_count, schema 1.0→1.1]
│   │   ├── dataset_builder.py                 [MODIFIED — populate the 2 new episode/report fields]
│   │   └── dataset_statistics.py              [MODIFIED — +weather tally]
│   └── quality/                                [NEW package]
│       ├── __init__.py
│       ├── schemas.py
│       ├── config.py
│       ├── artifacts.py
│       ├── metrics.py
│       ├── dataset_metrics.py
│       ├── scoring.py
│       ├── versioning.py
│       ├── regression.py
│       ├── coverage.py
│       ├── review.py
│       ├── gates.py
│       └── dashboard.py
├── scripts/
│   ├── inspect_dataset.py                     [MODIFIED — reuse artifacts.resolve_latest_dataset_dir, see Finding D]
│   ├── _format.py                             [NEW — shared ok()/warn()/fail() for the 6 new CLIs, see Finding D]
│   ├── dataset_quality.py                     [NEW — make quality]
│   ├── dataset_review.py                      [NEW — make review]
│   ├── compare_datasets.py                    [NEW — make compare-data]
│   ├── dataset_dashboard.py                   [NEW — make dashboard]
│   ├── recommend_data.py                      [NEW — make recommend-data]
│   └── dataset_version.py                     [NEW — make version]
└── tests/unit/
    ├── test_dataset_engineering.py            [MODIFIED — extend fixtures for weather + duplicate_sample_count fields]
    └── test_quality_engineering.py            [NEW — one file, many test classes, mirrors test_dataset_engineering.py's convention]
```

## 9. Dependency Graph — All New Modules Together

```
config/default.yaml (quality_engineering:)
        │
        ▼
src/quality/config.py
        │
        ▼
src/quality/schemas.py ◄── (no deps; pure dataclasses)
        │
        ▼
src/quality/artifacts.py ──uses──► src.data.dataset_schemas, dataset_io, episode.compute_route_hash (pattern reuse)
        │
        ├──► src/quality/metrics.py
        │           │
        │           ▼
        │     src/quality/coverage.py
        │           │
        │           ▼
        │     src/quality/dataset_metrics.py (registers CoverageMetric + 5 others)
        │           │
        │           ▼
        │     src/quality/scoring.py ──writes──► quality_score.json
        │
        ├──► src/quality/regression.py ──writes──► regression_report.json
        │           │
        │           ▼
        │     src/quality/versioning.py ──writes──► version.json, CHANGELOG.md
        │
        └──► (scoring.py + coverage.py + regression.py) ──► src/quality/review.py ──writes──► review.json
                                                       └──► src/quality/gates.py ──writes──► gate_report.json
                                                                     │
                    (scoring, versioning, regression, coverage, gates) │
                                                                     ▼
                                                        src/quality/dashboard.py ──writes──► dashboard.html

scripts/dataset_quality.py, dataset_review.py, compare_datasets.py,
dataset_dashboard.py, recommend_data.py, dataset_version.py
        │  each calls exactly one src/quality/ function + scripts/_format.py
        ▼
   stdout + one artifact file per command
```

## 10. Existing Files Modified (full list, with reason)

| File | Change | Why |
|---|---|---|
| `config/default.yaml` | + `quality_engineering:` section | ADR-0005–0009 config ownership |
| `src/data/dataset_schemas.py` | + `EpisodeIndexEntry.weather`, + `DatasetStatistics.weather`, + `QualityReport.duplicate_sample_count`; `DATASET_SCHEMA_VERSION` 1.0→1.1 | ADR-0004 Decision 6 — needed for coverage + duplicates metric, cannot be derived from existing fields |
| `src/data/dataset_builder.py` | Read `weather_preset` in `_read_metadata` usage; sum `duplicate_groups`' sample counts into the new report field | Populates the two additive fields above |
| `src/data/dataset_statistics.py` | Tally included episodes by weather, mirroring the existing `towns` tally | Populates `DatasetStatistics.weather` |
| `scripts/inspect_dataset.py` | Replace private `_resolve_latest_dataset_dir` with `src.quality.artifacts.resolve_latest_dataset_dir` | Finding D (below) — pure DRY refactor, no behavior change, covered by existing `TestInspectDatasetCLI` |
| `docs/PHASES.md` | Add Phase 3.5 section + update status table | Required by AGENTS.md §8 ("PHASES.md success criteria must be kept up to date") |
| `README.md` | Add 6 new `make` commands to Developer Commands | Keep quick-start accurate |
| `.agents/AGENTS.md` | Add "Phase 3.5 (current)" line to §1 Project Phase Awareness | Same convention already used for Phases 0–3 |
| `Makefile` | + `quality review compare-data dashboard recommend-data version` targets + `.PHONY` update | Deliverable #9 |
| `tests/unit/test_dataset_engineering.py` | Extend `_make_metadata`/`_write_episode` fixtures to set `weather_preset`, and add assertions that `episodes_index.jsonl` / `quality_report.json` carry the two new fields | Keep Phase 3 tests exercising the (now slightly larger) schema; **no existing test's expected behavior changes**, only fixtures gain a field and new assertions are added |

No other existing file is touched. In particular: `src/simulation/`,
`src/agents/`, `src/sensors/`, `src/evaluation/`, `src/models/`,
`src/training/`, `scripts/collect_expert_episode.py`,
`scripts/validate_episode.py`, `scripts/build_dataset.py`'s CLI surface
(only its dependency, `dataset_schemas.py`, changes underneath it), and
every `config/profiles/*.yaml` file are unmodified.

## 11. Duplicated Responsibilities / Abstractions Still Present

One was found and fixed during **this** presentation pass (not caught in
the earlier per-ADR review):

**Finding D — "most recent dataset directory" resolution, and
`_ok`/`_warn`/`_fail`-style CLI formatting, were about to be duplicated
six more times.** `scripts/inspect_dataset.py` already has a private
`_resolve_latest_dataset_dir()`. The six new CLIs
(`dataset_quality.py`, `dataset_review.py`, `compare_datasets.py`,
`dataset_dashboard.py`, `recommend_data.py`, `dataset_version.py`) all
need the identical "default to the most recent dataset" behavior, and all
six want the same colored `[ OK ]` / `[WARN]` / `[FAIL]` console
formatting `build_dataset.py` and `inspect_dataset.py` each currently
define locally. Left as originally drafted, this would have been seven
copies of the resolver and eight copies of the formatter across the
repository.

**Fix, folded into the design before implementation:**
- `resolve_latest_dataset_dir()` moves into `src/quality/artifacts.py`
  (already the module that owns "load things about a dataset directory" —
  ADR-0004 Decision 2) and `scripts/inspect_dataset.py` is updated to call
  it instead of its own private copy (§10 table, above) — a
  behavior-preserving refactor, safety-netted by the existing
  `TestInspectDatasetCLI` tests.
- A new `scripts/_format.py` (underscore-prefixed — a shared helper, not a
  CLI entry point) holds `ok()` / `warn()` / `fail()`, used by the six new
  scripts. `build_dataset.py` and `inspect_dataset.py`'s own local copies
  are left untouched — refactoring two-line functions in Phase 3 files
  that are otherwise not being touched by this phase is judged not worth
  the (small) risk of an unrelated diff, versus the six *new* files, which
  should not be written duplicated on day one.

No other duplication was found in this pass. Re-checked specifically:
metric weight normalization (reuses `dataset_splits.py`'s convention, not
duplicated — ADR-0005 Decision 2); hashing (reuses
`compute_route_hash()`'s canonicalization, not duplicated — ADR-0006
Decision 2); coverage computation (`scoring.py`'s `CoverageMetric` calls
`coverage.py`, does not reimplement — ADR-0005 Decision 1 consequence);
regression comparison (`versioning.py`'s changelog calls `regression.py`,
does not reimplement — ADR-0006 Decision 5).

## 12. Estimated Implementation Order

Derived directly from the dependency graph in §9 — each step only depends
on steps above it:

1. **Schema additions** — `src/data/dataset_schemas.py`,
   `dataset_builder.py`, `dataset_statistics.py` (the 3 additive fields).
   Extend `test_dataset_engineering.py` fixtures alongside this step so
   Phase 3 tests keep passing with the wider schema immediately.
2. **`src/quality/schemas.py`, `config.py`, `artifacts.py`** — no
   dependencies on the rest of the package; unlocks everything else.
   Includes moving `resolve_latest_dataset_dir` here and updating
   `inspect_dataset.py` (Finding D).
3. **`src/quality/metrics.py`, `coverage.py`** — coverage has no
   dependency on scoring, so it can be built and tested standalone before
   `dataset_metrics.py` needs to call it.
4. **`src/quality/dataset_metrics.py`, `scoring.py`** — the six metrics
   plus the weighted-mean/grade logic; first point at which `make quality`
   is functional end-to-end.
5. **`src/quality/regression.py`** — depends only on `artifacts.py`
   (already built in step 2); independent of scoring.
6. **`src/quality/versioning.py`** — depends on `regression.py` (step 5)
   for changelog generation.
7. **`src/quality/review.py`, `gates.py`** — both depend on
   scoring + coverage + regression (steps 4–5), built together since they
   share the same three inputs.
8. **`src/quality/dashboard.py`** — depends on everything above; built
   last among the `src/quality/` modules.
9. **`scripts/_format.py`, then the six new CLI scripts** — thin wrappers,
   built once their one underlying function each exists.
10. **`config/default.yaml`, `Makefile`** — wire in the
    `quality_engineering:` section and the six `make` targets (can happen
    incrementally alongside steps 4–9 as each config sub-section is
    needed, rather than as one big-bang edit).
11. **`tests/unit/test_quality_engineering.py`** — written alongside each
    module in steps 2–9, not deferred to the end; a final pass adds the
    CLI tests, hash-consistency tests, and golden-output tests once every
    module exists, and confirms the ≥95% coverage bar.
12. **Documentation** (`docs/ENGINEERING_LOOPS.md`,
    `QUALITY_SYSTEM.md`, `DATASET_VERSIONING.md`,
    `REGRESSION_DETECTION.md`, `ARCHITECTURE_DECISIONS.md`) plus
    `docs/PHASES.md` / `README.md` / `AGENTS.md` updates.
13. **Full validation suite** (`make lint`, `make type-check`, `make
    test`) and the **Phase 3.5 Implementation Report**
    (`docs/PHASE3_5_IMPLEMENTATION_REPORT.md`), written last, per the
    brief's mandatory Final Self-Review.
