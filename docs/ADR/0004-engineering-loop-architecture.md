# ADR 0004 — Phase 3.5 Engineering Loop Architecture

**Date:** 2026-07-20 (revised same day — see Revision Note)
**Status:** Proposed
**Deciders:** AI Engineering Team

> **Revision note:** A second design pass generalized three things this
> ADR originally specified as dataset-only: (1) `DatasetArtifacts` is now
> the dataset-specific subtype of a generic `Artifact` envelope every
> artifact type shares; (2) the metric registry is one process-wide,
> **category-based** `MetricRegistry` instead of a per-artifact-type
> global constant (no more `DATASET_METRIC_REGISTRY`, and — per ADR-0010
> — no future `MODEL_METRIC_REGISTRY` either); (3) a new ADR-0011
> formalizes cross-artifact-type lineage, sitting alongside this ADR's
> other four extension points as a fifth. All decisions below are updated
> in place to reflect this; nothing about the "ten single-responsibility
> modules, file-in/file-out, one-way dependency on `src/data/`" shape
> changed.

---

## Context

Phases 0–3b built a linear pipeline: **Collect → Validate → Build Dataset →
(Train, not yet built)**. Each dataset build already produces a manifest,
quality report, and statistics file, but nothing *consumes* those artifacts
across builds. There is no scoring, no comparison between builds, no
standing record of what changed, no automated collection guidance, and no
gate stopping a future trainer from running against a bad dataset. Phase 4
(Behavioural Cloning) is the first consumer waiting on this.

Phase 3.5 does not add another pipeline stage. It adds an **engineering
loop** that wraps the existing dataset-engineering output and turns it into
a standing, queryable record:

```
Collect → Validate → Evaluate → Review → Improve → Train → Compare → Deploy → Monitor → Collect more
                         ▲                                      │
                         └──────────── Phase 3.5 lives here ────┘
```

Today only the **Evaluate** and **Review** stages (dataset-scoped) are in
scope. Train/Compare/Deploy/Monitor stages belong to Phases 4–6 and must be
able to plug into the same loop without redesigning it — see ADR-0010.

---

## Decisions

### 1. A new top-level package, `src/quality/`, owns the entire engineering loop

**Decision:** All Phase 3.5 code lives in a new `src/quality/` package,
parallel to `src/data/`, `src/simulation/`, `src/models/`, etc. Nothing in
`src/data/` is modified except one additive schema field (Decision 6).

**Rationale:**
- `src/data/` owns *producing* a dataset (Phase 3a/3b). `src/quality/` owns
  *judging, comparing, and reporting on* datasets that already exist. These
  are different responsibilities: the builder answers "what did this build
  produce," the quality engine answers "is what it produced any good, and
  what changed." Mixing them would make `dataset_builder.py` grow a second,
  unrelated job.
- A dedicated package gives Phase 4/5/6 an unambiguous place to add
  `model_metrics.py`, `simulation_metrics.py`, etc. (ADR-0010) without
  hunting through `src/data/` for where "quality" logic lives.
- Matches the existing convention: one package per pipeline concern
  (`sensors/`, `agents/`, `data/`, `simulation/`).

**Consequences:** `src/quality/` depends on `src/data/dataset_schemas.py`
and reads the files `dataset_builder.py` writes; `src/data/` has no
dependency back on `src/quality/`. The dependency arrow points one way, so
Phase 3a/3b remain independently testable and usable with zero knowledge
that Phase 3.5 exists.

### 2. Module boundaries mirror the ten deliverables, each with one responsibility

**Decision:** `src/quality/` is split into single-responsibility modules,
not one large "quality.py":

| Module | Responsibility |
|---|---|
| `schemas.py` | Dataclasses shared across the package, including the generic `Artifact` envelope every artifact type extends (see Decision 6a) |
| `registry.py` | One generic `CategoryRegistry[T]` — register/query items by category string (see Decision 6b). Used by both `metrics.py` and `dashboard.py`'s section list — the single implementation behind every "register X under a category" extension point in this phase |
| `artifacts.py` | Loads `dataset_manifest.json` / `episodes_index.jsonl` / `samples_index.jsonl` / `stats.json` / `quality_report.json` into the existing `src.data.dataset_schemas` dataclasses, wrapped in a `DatasetArtifact` (the dataset-specific `Artifact` subtype). The **only** place in `src/quality/` that touches raw JSON/JSONL parsing. Also owns `hash_content()` and a generic `load_artifact_envelope()` used by `versioning.py`/`lineage.py` for any artifact type |
| `metrics.py` | The metric interface + `METRIC_REGISTRY = CategoryRegistry[Metric]()` (see Decision 6b) |
| `dataset_metrics.py` | Concrete dataset-level metrics, registered under category `"dataset"` into `metrics.py`'s shared registry |
| `scoring.py` | Combines metric results into one `QualityScore` (ADR-0005) |
| `versioning.py` | Artifact identity, hashing, changelog generation — generic across artifact types (ADR-0006) |
| `regression.py` | Artifact-to-artifact-of-the-same-type comparison (ADR-0007) |
| `coverage.py` | Gap analysis and collection recommendations (ADR-0008) — dataset-specific |
| `review.py` | Deterministic star rating / strengths / weaknesses, built from `scoring.py` + `coverage.py` output |
| `gates.py` | Pass/fail training gate, built from `scoring.py` + `regression.py` (+ optionally `lineage.py`) output |
| `dashboard.py` | Renders all of the above into one HTML artifact (ADR-0009) |
| `lineage.py` | Cross-artifact-type derivation graph, reconstructed from `version.json` files (ADR-0011) |

**Rationale:**
- Each module has exactly one reason to change, mirroring the Single
  Responsibility principle already implicit in `src/data/` (compare
  `dataset_alignment.py` vs. `dataset_splits.py` vs. `dataset_statistics.py`
  — three narrow modules instead of one wide one).
- `artifacts.py` exists specifically so `scoring.py`, `versioning.py`,
  `regression.py`, `coverage.py`, and `dashboard.py` do not each grow their
  own copy of "open this JSON file and parse it." It reuses
  `src.data.dataset_io.read_jsonl_records` for the two `.jsonl` files and
  reconstructs the existing `DatasetManifest` / `QualityReport` /
  `DatasetStatistics` / `EpisodeIndexEntry` / `SampleRecord` dataclasses
  from `src.data.dataset_schemas` — Phase 3.5 does not define a second,
  parallel set of schema classes for data Phase 3 already modeled.
- `review.py`, `gates.py`, and `dashboard.py` are **composition-only** — they
  read the output of `scoring.py` / `regression.py` / `coverage.py` and
  format it. They contain no scoring math or comparison logic of their own,
  so a threshold change is made in exactly one file regardless of which
  surface (CLI, dashboard, gate) displays it.
- This directly satisfies the "Metrics Framework" deliverable: `metrics.py`
  is the only place that knows how to register and run a metric; every
  other module consumes results through it rather than computing its own.

**Consequences:** Ten deliverables map to (approximately) ten files, which
keeps code review scoped and keeps future contributors from needing to read
the whole package to change one behavior.

### 3. Every Phase 3.5 module is a pure function of on-disk artifacts — no new database, no service, no daemon

**Decision:** All `src/quality/` functions take file paths (or already-parsed
dataclasses) as input and either return a dataclass or write a file. Nothing
in this phase runs as a background process, opens a network port, or
requires a database.

**Rationale:**
- Mirrors the repository's existing "components communicate via files"
  philosophy (`docs/ARCHITECTURE.md` — "Components communicate via files
  ... rather than shared in-process state"). Introducing a database or
  service here would be the first component in the whole repository to
  break that pattern, for a phase whose job is *reporting on* files that
  already exist.
- Keeps portability across macOS/Linux/Windows/Docker/CI, per the
  Engineering Principles — a file-based design has no install-time
  dependency beyond the Python standard library plus what Phase 3 already
  uses.
- Every artifact this phase produces (`quality_report_v2.json`,
  `version.json`, `regression_report.json`, `review.json`,
  `coverage_recommendations.json`, `dashboard.html`) is itself inspectable
  with a text editor or browser, consistent with "Definition of Done":
  a new engineer should be able to answer the trust questions without
  reverse-engineering a running service.

**Consequences:** Anything that wants a live, always-on view (e.g. a
CI dashboard server) is a thin wrapper that re-runs `dashboard.py` on a
schedule — not a redesign.

### 4. CLIs are thin; all logic lives in `src/quality/`

**Decision:** New `scripts/*.py` entry points (`dataset_quality.py`,
`dataset_review.py`, `compare_datasets.py`, `dataset_dashboard.py`,
`recommend_data.py`, `dataset_version.py`) parse arguments, load config,
call exactly one `src/quality/` function, and format output — identical to
the existing `build_dataset.py` / `inspect_dataset.py` pattern.

**Rationale:** Already the repository's convention (`docs/ARCHITECTURE.md`
directory reference: "scripts/ # CLI entry points (thin; business logic
lives in src/)"). No new pattern introduced.

**Consequences:** Every `src/quality/` function is independently unit
testable without invoking Click or touching `sys.argv`, matching the
existing `dataset_builder.py` / CLI test split in
`tests/unit/test_dataset_engineering.py`.

### 5. Configuration owns every threshold, weight, and target — `src/quality/` contains no magic numbers

**Decision:** A new top-level `quality_engineering:` section is added to
`config/default.yaml` (detailed per-module in ADR-0005 through ADR-0009).
No module hardcodes a weight, threshold, grade boundary, coverage target,
or regression severity cutoff.

**Rationale:** Directly required by AGENTS.md §3 ("All tunable parameters
live in `config/`") and the brief's explicit "No magic numbers" / "Nothing
hardcoded" requirements. It also means a downstream team tuning quality
bars for a different ODD (operational design domain) never edits Python.

**Consequences:** Every `src/quality/` public function takes its thresholds
as explicit parameters (dataclasses, not raw dict lookups inside the
function body), so unit tests can exercise boundary behavior without
constructing a full config file — the same pattern
`OutlierThresholds` already established in `dataset_outliers.py`.

### 6. Two additive, backward-compatible fields are added to the existing dataset schema: `weather` and `duplicate_sample_count`

**Decision:**
- `EpisodeIndexEntry` gains one new field, `weather: str | None`,
  populated from `metadata.json`'s existing `weather_preset` field
  (already written by Phase 2, just not indexed by Phase 3a).
- `DatasetStatistics` gains one new field, `weather: dict[str, int]` —
  included-episode counts keyed by weather preset, computed in
  `compute_statistics()` the exact same way the existing `towns` field
  already is (same loop, one more dict). This is what lets
  `regression.py` (ADR-0007) diff weather coverage between two datasets
  by reading `stats.json` alone, symmetrically with how it already diffs
  `towns`.
- `QualityReport` gains one new field, `duplicate_sample_count: int` —
  the total number of samples belonging to any duplicate group (`sum(len(
  group.sample_ids) for group in duplicate_groups)`), computed in
  `dataset_builder.py`'s existing duplicate-group loop where
  `duplicate_frame_groups` (the group *count*) is already computed.

`DATASET_SCHEMA_VERSION` moves `1.0` → `1.1`. No other Phase 3 file
changes.

**Rationale:**
- Coverage planning (ADR-0008) and the quality/review examples in the
  brief ("Collect additional rainy Town10 episodes") require knowing which
  weather each episode was collected under. That data already exists in
  every episode's `metadata.json` — Phase 2 never dropped it, Phase 3a
  simply never indexed it, because Phase 3a's scope was structural quality
  (alignment/validity), not diversity.
- The `duplicates` metric (ADR-0005 Decision 1) needs "what fraction of
  *samples* are duplicated," not "how many duplicate *groups* exist" — a
  single group of 500 duplicated samples and 500 groups of 2 duplicated
  samples each currently produce the same `duplicate_frame_groups: 1` vs.
  `500` distinction, but neither tells `scoring.py` what fraction of the
  dataset is affected without re-reading every group's `sample_ids`, which
  `quality_report.json` truncates to a 5-item preview for readability
  (see `dataset_builder.py`'s duplicate-issue message). Recording the
  already-computed total once, structurally, is the caught-in-review fix
  — this was found while checking ADR-0005's `duplicates` formula against
  what `quality_report.json` actually persists today, precisely the kind
  of gap the Architecture Review step exists to catch (see
  `docs/ARCHITECTURE_REVIEW.md`).
- Both fields are additive: existing `episodes_index.jsonl` /
  `quality_report.json` files without them parse as `weather: null` /
  (missing key, defaulted to `0` by `artifacts.py`) in any consumer that
  doesn't require them, and every field before them is untouched.
  Rebuilding a dataset with the same inputs before and after this change
  produces identical files except for these two new fields.

**Consequences:** `src/data/dataset_builder.py` and
`src/data/dataset_statistics.py` get three small, additive changes in
total — reading `weather_preset` alongside `town`/`route_name` in
`_read_metadata` callers, tallying included episodes by weather the same
way they are already tallied by town, and summing `duplicate_groups`'
existing `sample_ids` lengths into the new `QualityReport` field — the
only edits this phase makes to Phase 3a/3b code. All three are called out
explicitly rather than silently bundled, per "Do not duplicate existing
functionality" — everything else needed for coverage planning, scoring,
and regression detection is new code in `src/quality/`, not a rewrite of
dataset engineering.

### 7. Dataset engineering artifacts are read-only inputs; Phase 3.5 never mutates a dataset build

**Decision:** Every `src/quality/` module only reads
`dataset_manifest.json`, `episodes_index.jsonl`, `samples_index.jsonl`,
`stats.json`, and `quality_report.json`. It never edits them in place.
Phase 3.5's own outputs (score, version record, regression report, review,
recommendations, dashboard) are written as new files alongside them.

**Rationale:** A dataset directory that already passed a training run is a
provenance record (ADR-0006). Silently rewriting `quality_report.json`
after the fact would undermine exactly the reproducibility guarantee
Phase 3a/3b built. This mirrors the existing precedent in
`dataset_builder.py` never calling `write_validation_status()` — mutating
another stage's output is deliberately left to that stage's own explicit
tool.

**Consequences:** Re-running `make quality` twice against the same
dataset produces byte-identical output (given identical config) — a
prerequisite for the hash-consistency tests required by the testing
section.

### 6a. `Artifact` is a generic envelope; `DatasetArtifacts` becomes `DatasetArtifact`, one concrete subtype among several planned ones

**Decision:** `src/quality/schemas.py` defines a minimal base:

```python
@dataclass
class Artifact:
    artifact_id: str
    artifact_type: str      # "dataset" today; "model" | "evaluation" | "deployment" later (ADR-0010/0011)
    artifact_dir: Path
    created_at: str | None
    git_commit: str | None
```

`artifacts.py`'s `DatasetArtifact` (renamed from the original
`DatasetArtifacts`, singular to match `Artifact`) extends it with the
dataset-specific parsed content:

```python
@dataclass
class DatasetArtifact(Artifact):
    manifest: DatasetManifest
    episodes: list[EpisodeIndexEntry]
    stats: DatasetStatistics
    quality_report: QualityReport
    samples: list[SampleRecord] | None   # loaded only if requested — can be large
```

**Rationale:** Every module that needs to work generically across
artifact types — `versioning.py` (ADR-0006, revised), `lineage.py`
(ADR-0011), and `gates.py`'s lineage-aware checks — needs a common shape
to operate on that does not require knowing a dataset's specific fields.
Building `DatasetArtifacts` as a dataset-only, non-extensible dataclass
(the original design) would have meant those modules either special-cased
datasets today and needed a rewrite for model artifacts tomorrow, or
`versioning.py`/`lineage.py` would have had to accept "any dict-like
thing" and lose type safety entirely. A thin base class with the fields
every artifact provably has (an ID, a type tag, a directory, a creation
time, an optional git commit) is the smallest common contract that makes
both goals possible at once — mirrors the same "don't build ahead of
validated need" discipline as ADR-0008's rejected N-dimensional coverage
matrix and ADR-0010 §5's "don't pre-build `model_metrics.py`": the base is
kept intentionally minimal, not expanded speculatively with fields only a
future artifact type might want.

**Consequences:** Every `src/quality/` function that today takes a
`DatasetArtifact` (`scoring.py`, `coverage.py`, `review.py`'s dataset-
specific parts) keeps that exact, fully-typed parameter — nothing about
dataset-specific code becomes less precise. Only the *shared*
infrastructure (`versioning.py`, `lineage.py`) is written against the
`Artifact` base, which is exactly the code that needs to be artifact-type-
agnostic.

### 6b. One generic `CategoryRegistry[T]` in `registry.py`, organized by category, backs both the metric registry and the dashboard's section registry — not two near-identical classes

**Decision:**

```python
# src/quality/registry.py
T = TypeVar("T")

class CategoryRegistry(Generic[T]):
    def register(self, category: str, item: T) -> None: ...
    def get(self, category: str, name: str) -> T: ...          # name = item's own .name/.title attribute
    def all(self, category: str | None = None) -> list[T]: ...  # None = every category
    def categories(self) -> list[str]: ...

# src/quality/metrics.py
METRIC_REGISTRY: CategoryRegistry[Metric] = CategoryRegistry()

# src/quality/dashboard.py
SECTION_REGISTRY: CategoryRegistry[DashboardSection] = CategoryRegistry()
```

`dataset_metrics.py` calls `METRIC_REGISTRY.register("dataset",
SynchronizationMetric())` (and five more) at import time; `scoring.py`
calls `METRIC_REGISTRY.all("dataset")` instead of iterating a
dataset-specific global constant. `Metric.compute()` takes the generic
`Artifact` base type; a category-scoped metric (e.g. every metric
registered under `"dataset"`) is written with the documented convention
that its category always receives the matching concrete subtype
(`DatasetArtifact`) — asserted with an `isinstance` narrowing check at the
top of `compute()`, not encoded as a second type parameter on
`CategoryRegistry` itself.

**Rationale:**
- The first pass at this decision (this section's original draft) gave
  metrics their own `MetricRegistry` class and, separately, gave the
  dashboard its own `SectionRegistry` class (ADR-0009 Decision 2) — two
  classes, same shape (`register(category, item)`, `all(category)`),
  written twice within the same design pass. Caught during this revision
  and fixed by extracting the one generic implementation both actually
  need: `CategoryRegistry[T]`. This is the same category of fix as
  `artifacts.py` itself (originally caught in the first architecture
  review, `docs/ARCHITECTURE_REVIEW.md` Question 1/2) — a module existing
  specifically so a piece of logic is implemented once, not twice, in the
  same phase.
- The original per-artifact-type global (`DATASET_METRIC_REGISTRY =
  MetricRegistry()`) implied a second, parallel `MODEL_METRIC_REGISTRY`
  constant the day Phase 4 needed model metrics, and a third and fourth
  for Phase 5/6 — four registries, each independently iterated by
  whatever consumed them, instead of one thing to import and query. A
  single category-based registry means `scoring.py`, `dashboard.py`, and
  any future consumer query "give me every metric in category X" against
  one object, and a new category is a new string, not a new global.
- A fully generic, type-parameterized-by-artifact-subtype registry
  (`CategoryRegistry[Metric, DatasetArtifact]`, checked by the type
  system per category) was considered and rejected as unnecessary
  complexity for this project's scale — the isinstance-narrowing
  convention is the same simplicity-over-genericity trade-off
  `src/utils/config.py` already makes with `ConfigDict = dict[str, Any]`
  rather than a fully generic config schema type. Category strings are
  validated by a small fixed set of unit tests (one per registered
  category asserting every metric/section in it accepts the right
  artifact subtype), not by the type checker — a deliberate, documented
  trade-off, not an oversight.

**Consequences:** `registry.py` has no knowledge of how many categories
exist, or of metrics vs. dashboard sections at all — it is pure container
logic. `categories()` simply reflects whatever has been registered by the
time it's called, which is exactly what lets Phase 4/5/6 add `"model"` /
`"simulation"` / `"deployment"` categories to *both* `METRIC_REGISTRY` and
`SECTION_REGISTRY` with zero change to `registry.py`, `metrics.py`, or
`dashboard.py` (ADR-0010, revised). Any third future consumer that needs
"register X under a category" (not currently anticipated) reuses
`CategoryRegistry[T]` a third time rather than writing a third copy.

---

## Extension Points (for ADR-0010 and ADR-0011 to build on)

1. **`src/quality/metrics.py`'s `METRIC_REGISTRY` (a `CategoryRegistry[Metric]`,
   `registry.py` — Decision 6b)** — any module can call
   `METRIC_REGISTRY.register(category, metric)` at import time.
   `dataset_metrics.py` does this today under category `"dataset"`; a
   future `model_metrics.py` / `simulation_metrics.py` /
   `deployment_metrics.py` registers under `"model"` / `"simulation"` /
   `"deployment"` into the *same* registry object, with no change to
   `scoring.py`, `gates.py`, or `dashboard.py`.
2. **`src/quality/dashboard.py`'s `SECTION_REGISTRY` (the same
   `CategoryRegistry[T]`, instantiated as `CategoryRegistry[DashboardSection]`)**
   — the dashboard is built from every section registered under an
   artifact type's category, not a fixed template (ADR-0009). Future
   phases register a section under a new category; they never edit
   existing ones.
3. **`src/quality/gates.py`'s check list** — the training gate is a list of
   named `GateCheck` callables returning pass/fail + reason. Phase 4 adds
   model-readiness checks (e.g. "checkpoint's lineage traces back to this
   dataset version" — ADR-0011 Decision 5) to the same list without
   changing the gate's control flow.
4. **`regression.py`'s comparator interface** — compares two artifacts of
   the same type today (datasets). The same interface accepts two
   arbitrary named metric snapshots, so Phase 5 can reuse it to compare
   two evaluation runs instead of two datasets (ADR-0010).
5. **`src/quality/lineage.py`'s derivation graph** (ADR-0011) — any
   artifact type's `VersionRecord` can declare `lineage_parents` pointing
   at artifacts of a *different* type it was derived from. Phase 4's
   checkpoints, Phase 5's evaluation runs, and Phase 6's deployment
   packages all populate this the same way; `lineage.py`'s graph-building
   and traversal logic does not change per artifact type.
