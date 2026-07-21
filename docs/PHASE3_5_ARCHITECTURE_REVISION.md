# Phase 3.5 — Architecture Revision (Final)

**Supersedes:** interface signatures, module tables, and dependency
diagrams in `docs/PHASE3_5_DESIGN_REVIEW.md` §§1–3, 7, 8, 9 — everything
else in that document (ADR-0007/0008's content-level decisions, the
per-ADR executive summaries not touched below, risks, Phase 4 fit not
otherwise revised) still holds.
**Companion to:** `docs/ADR/0004`, `0005`, `0006` (renamed), `0007`,
`0009`, `0010` (all amended in place with revision notes), and new
`docs/ADR/0011`. `docs/ARCHITECTURE_REVIEW.md` has a "Revision 2" addendum
covering this pass.
**Status:** Design final. No code has been written yet — this is still
pre-implementation.

This document presents the four requested enhancements in full: what
problem each solves, what was considered and rejected, the final
interfaces, and how they change the repository tree, dependency graph,
and implementation order already presented. One additional duplication —
introduced by this revision's own draft — was caught and fixed before
finalizing (§5).

---

## 1. Generalize `DatasetArtifacts` → `Artifact`

**Executive summary.** The original design gave `src/quality/artifacts.py`
one dataclass, `DatasetArtifacts`, shaped entirely around what a dataset
build produces (manifest, episode index, sample index, stats, quality
report). Every module downstream of it (`versioning.py`, and now
`lineage.py`) needs to operate on artifacts *generically* — a model
checkpoint, an evaluation run, a deployment package — without knowing
dataset-specific field names. The fix is a two-tier type: a minimal
`Artifact` base every artifact type shares (identity, type tag,
directory, timestamp, commit), and `DatasetArtifact` — renamed from
`DatasetArtifacts`, singular to match `Artifact` — as the dataset-specific
subtype carrying today's full parsed content.

**Problem being solved.** `versioning.py` and `lineage.py` need a common
contract to operate on across artifact types that don't exist yet
(model/evaluation/deployment); a dataset-only dataclass cannot serve that
role without every generic module special-casing datasets today and
requiring a rewrite the day a second artifact type appears.

**Alternatives considered.**
- *Keep `DatasetArtifacts` dataset-only; give each future artifact type
  its own, unrelated dataclass.* Rejected — `versioning.py` and
  `lineage.py` would then need either a union type checked with `isinstance`
  everywhere, or four near-identical parallel implementations (one per
  artifact type) of hashing and lineage-graph logic. Exactly the
  duplication this whole phase exists to avoid.
- *One large `Artifact` dataclass with every field every artifact type
  might ever need (optional/`None` for the ones that don't apply).*
  Rejected — this is the classic "wide record with many nullable fields"
  anti-pattern; it also means `scoring.py`'s dataset metrics would receive
  an object with a dozen irrelevant `None` fields instead of the fully-
  typed `DatasetArtifact` they actually need.
- *A `Protocol` instead of a base dataclass* (structural typing, no
  inheritance). Considered; rejected because `Artifact`'s fields
  (`artifact_id`, `artifact_type`, `artifact_dir`, `created_at`,
  `git_commit`) are genuinely shared *data*, not just a shared shape two
  otherwise-unrelated classes happen to satisfy — inheritance communicates
  "is-a" correctly here, and dataclass inheritance is already used
  elsewhere in this codebase's style (plain dataclasses throughout
  `src/data/dataset_schemas.py`).

**Final design decision.**
```python
# src/quality/schemas.py
@dataclass
class Artifact:
    artifact_id: str
    artifact_type: str        # "dataset" today; "model" | "evaluation" | "deployment" later
    artifact_dir: Path
    created_at: str | None
    git_commit: str | None

# src/quality/artifacts.py
@dataclass
class DatasetArtifact(Artifact):
    manifest: DatasetManifest
    episodes: list[EpisodeIndexEntry]
    stats: DatasetStatistics
    quality_report: QualityReport
    samples: list[SampleRecord] | None   # loaded only when requested

def load_dataset_artifacts(dataset_dir: Path, *, load_samples: bool = False) -> DatasetArtifact: ...
def load_artifact_envelope(artifact_dir: Path) -> Artifact: ...   # generic — reads only version.json's identity fields
```

**Dependency diagram (updated).**
```
src/quality/schemas.py::Artifact
        ▲
        │ extends
src/quality/artifacts.py::DatasetArtifact ◄── used by scoring.py, coverage.py, review.py (dataset-specific, fully typed)
        │
src/quality/artifacts.py::load_artifact_envelope() ──► Artifact (generic) ──► used by versioning.py, lineage.py
```

**Public interfaces.** Every dataset-specific function's signature is
**unchanged** except its parameter's type name
(`DatasetArtifacts` → `DatasetArtifact`) — `compute_quality_score()`,
`compute_coverage()`, etc. all still take a fully-typed dataset object,
not the generic base. Only `versioning.py` and `lineage.py`'s functions
are written against `Artifact`.

**Configuration additions.** None.

**Risks / trade-offs.**
- *Risk:* a future artifact type discovers it needs a field `Artifact`
  doesn't have (e.g. a checkpoint's parameter count). *Mitigation:* that
  field belongs on the future `ModelArtifact(Artifact)` subtype, exactly
  where `DatasetArtifact` already puts dataset-specific fields — not a
  reason to widen the shared base.
- *Trade-off:* two dataclasses to reason about instead of one, in
  exchange for `versioning.py`/`lineage.py` never needing to know a
  dataset's internal shape. Judged worth it — the whole point of ADR-0011
  is that lineage code must not import dataset-specific schema knowledge.

**Supports Phase 4/5/6 by:** letting `ModelArtifact`, `EvaluationArtifact`,
and `DeploymentArtifact` each extend `Artifact` the identical way
`DatasetArtifact` does today — the base contract every generic module
needs is proven now, with real dataset content behind it, not designed in
the abstract.

---

## 2. Generalize Dataset Versioning → Artifact Versioning

**Executive summary.** ADR-0006 is renamed (file: `0006-dataset-
versioning.md` → `0006-artifact-versioning.md`, ADR number unchanged) and
its `VersionRecord` generalized: `dataset_id`/`previous_dataset_id`
become `artifact_id`/`previous_artifact_id`; the three fixed hash fields
(`manifest_hash`, `statistics_hash`, `quality_report_hash`) become one
named `content_hashes: dict[str, str]`; the two fixed count fields
(`sample_count`, `episode_count`) become one named
`summary_counts: dict[str, int]`; and a new `lineage_parents:
list[LineageEdge]` field is added (populated by ADR-0011, defined there,
referenced here). Every original decision's *reasoning* is preserved —
this is a field-shape generalization, not a re-litigation of why
versioning works the way it does.

**Problem being solved.** A `VersionRecord` hardwired to
`dataset_id`/`sample_count`/`episode_count` cannot represent a model
checkpoint's version record (`epoch_count`, `parameter_count`) without
either a second, parallel dataclass (`ModelVersionRecord`, duplicating
every decision in ADR-0006 a second time) or a schema migration the day
Phase 4 needs it.

**Alternatives considered.**
- *A separate `VersionRecord` subclass per artifact type* (mirroring the
  `Artifact`/`DatasetArtifact` split in §1). Considered — rejected
  specifically for `VersionRecord`, unlike `Artifact` itself, because the
  fields that differ across artifact types (which files get hashed, which
  counts matter) are *data*, not *structure* — a `dict[str, str]` /
  `dict[str, int]` already captures "named, artifact-specific hash/count"
  without a new Python type per artifact type. Subclassing here would add
  a class per artifact type for zero behavioral gain, since nothing reads
  `content_hashes["manifest"]` through static typing anyway (it's always
  read by string key, dataset or not).
- *Keep three/two fixed fields, add three/two more for models, etc.,
  accumulating optional fields over time.* Rejected — same "wide nullable
  record" anti-pattern rejected in §1, and it means `VersionRecord`'s
  schema grows every time a new artifact type ships, instead of staying
  fixed forever.

**Final design decision.** (Full field list and rationale already fully
specified in the rewritten `docs/ADR/0006-artifact-versioning.md`
Decision 1 — reproduced here for completeness:)
```python
@dataclass
class VersionRecord:
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
    lineage_parents: list[LineageEdge]     # ADR-0011
```

**Dependency diagram (updated).**
```
artifacts.py::hash_content() ──► versioning.py::compute_version_record() ──► version.json
regression.py::compare_datasets() ──► versioning.py::generate_changelog() ──► CHANGELOG.md
lineage.py ◄── reads version.json's lineage_parents (does not write it — versioning.py does, at artifact-build time)
```

**Public interfaces.**
```python
# src/quality/versioning.py
def compute_version_record(
    artifact_dir: Path, artifact_type: str, cfg: QualityEngineeringConfig,
    *, previous_artifact_id: str | None = None,
    lineage_parents: list[LineageEdge] | None = None,
) -> VersionRecord: ...
def generate_changelog(artifact_dir: Path, version: VersionRecord, cfg: QualityEngineeringConfig) -> str: ...
def write_version_artifacts(
    artifact_dir: Path, artifact_type: str, cfg: QualityEngineeringConfig,
    **kwargs: Any,
) -> VersionRecord: ...
```
`scripts/dataset_version.py` (`make version`) calls
`write_version_artifacts(dataset_dir, artifact_type="dataset", cfg=cfg)` —
the one dataset-specific line in an otherwise fully generic call.

**Configuration additions.** Unchanged from the original ADR-0006 —
`quality_engineering.versioning.{changelog_filename, version_filename}` —
these are file names, not per-artifact-type, so no new config key was
needed.

**Risks / trade-offs.**
- *Risk:* `content_hashes`/`summary_counts` being open dicts (not typed
  fields) means a typo'd key (`"sample_cout"`) fails silently at read
  time instead of at the type checker. *Mitigation:* each artifact type's
  producer (`dataset_version.py` today) defines its exact key set as
  module-level constants (`CONTENT_HASH_KEYS = ("manifest", "statistics",
  "quality_report")`), asserted against in that module's own unit tests —
  the same "convention plus tests, not the type system" trade-off §1 and
  ADR-0004 Decision 6b already accept for category strings.
- *Trade-off:* every consumer of `version.json` now reads
  `content_hashes["manifest"]` instead of `.manifest_hash` — one extra
  level of dict indexing, in exchange for the schema never needing to
  change again when a new artifact type ships.

**Supports Phase 4/5/6 by:** giving a checkpoint's version record the
exact same shape a dataset's has today — `write_version_artifacts()` is
Phase 4's entire versioning implementation, zero new code.

---

## 3. Replace `DATASET_METRIC_REGISTRY` with a category-based `MetricRegistry`

**Executive summary.** Instead of one global constant per artifact type
(`DATASET_METRIC_REGISTRY` today, implying `MODEL_METRIC_REGISTRY`,
`SIMULATION_METRIC_REGISTRY`, `DEPLOYMENT_METRIC_REGISTRY` later), there
is now **one** `METRIC_REGISTRY`, and every metric registers into it
tagged with a category string (`"dataset"`, later `"model"`,
`"simulation"`, `"deployment"`). Consumers query `METRIC_REGISTRY.all
("dataset")` instead of importing a category-specific global.

**Problem being solved.** A new global constant per artifact type means
every future consumer of "all metrics" (today just `scoring.py`; in
principle a future cross-category quality overview) has to know about,
import, and iterate N separate objects instead of querying one.

**Alternatives considered.**
- *Keep the per-type global constant pattern, just name it consistently*
  (`DATASET_METRIC_REGISTRY`, `MODEL_METRIC_REGISTRY`, ...). Rejected —
  consistent naming doesn't solve the "N objects to know about" problem,
  it just makes the Nth one predictable to name.
- *A fully generic, type-parameterized-by-artifact-subtype registry*
  (compile-time-checked that `"dataset"` metrics only ever receive
  `DatasetArtifact`). Rejected as unnecessary complexity for this
  project's scale — see ADR-0004 Decision 6b's rationale; the isinstance-
  narrowing-by-convention approach was chosen instead, validated by unit
  tests per category.

**Final design decision.** `src/quality/registry.py`'s
`CategoryRegistry[T]` (§5 below) instantiated once as
`METRIC_REGISTRY: CategoryRegistry[Metric]` in `metrics.py`.
`dataset_metrics.py` registers all six dataset metrics under `"dataset"`
at import time; `scoring.py` queries `METRIC_REGISTRY.all("dataset")`.

**Dependency diagram (updated).**
```
src/quality/registry.py::CategoryRegistry[T]
        │
        ├──► src/quality/metrics.py::METRIC_REGISTRY = CategoryRegistry[Metric]()
        │            ▲
        │            │ registers under "dataset"
        │      src/quality/dataset_metrics.py
        │            │
        │            ▼ scoring.py queries METRIC_REGISTRY.all("dataset")
        │
        └──► src/quality/dashboard.py::SECTION_REGISTRY = CategoryRegistry[DashboardSection]()
                     ▲
                     │ registers under "dataset"
               dashboard.py itself (6 sections) + lineage.py (Lineage section, §4)
```

**Public interfaces.**
```python
# src/quality/registry.py
T = TypeVar("T")

class CategoryRegistry(Generic[T]):
    def register(self, category: str, item: T) -> None: ...
    def get(self, category: str, name: str) -> T: ...
    def all(self, category: str | None = None) -> list[T]: ...
    def categories(self) -> list[str]: ...

# src/quality/metrics.py
METRIC_REGISTRY: CategoryRegistry[Metric] = CategoryRegistry()

class Metric(Protocol):
    name: str
    def compute(self, artifact: Artifact, cfg: QualityEngineeringConfig) -> MetricResult: ...
```

**Configuration additions.** None — categories are code-level strings, not
config.

**Risks / trade-offs.** Identical to those already documented in
ADR-0004 Decision 6b (isinstance-narrowing convention, validated by tests
not the type checker) — no new risk introduced by generalizing further to
`CategoryRegistry[T]`; if anything, sharing one implementation across two
consumers means a bug fixed once (e.g. a category-ordering issue) is
fixed for both metrics and dashboard sections simultaneously.

**Supports Phase 4/5/6 by:** letting `model_metrics.py`,
`simulation_metrics.py`, and `deployment_metrics.py` register into the
*same* `METRIC_REGISTRY` under new categories — no new registry class, no
new global constant, ever again for this concern.

---

## 4. ADR-0011 — Experiment Tracking & Lineage

**Executive summary.** New module, `src/quality/lineage.py`, reconstructs
a directed acyclic graph of artifact derivation from `version.json`'s new
`lineage_parents` field (§2) — answering "which dataset trained this
checkpoint, which checkpoint produced this evaluation run, which eval run
led to this deployment package" by walking edges, not by manually
cross-referencing IDs. This is kept strictly distinct from
`previous_artifact_id` (§2's same-type version history,
already-existing) — the two answer different questions and are never
conflated (ADR-0011 Decision 1). The graph is rebuilt on demand from disk
every time, exactly like every other Phase 3.5 artifact — no database, no
persisted graph file, consistent with ADR-0004 Decision 3.

**Problem being solved.** Nothing in ADR-0004–0010 answers a
cross-artifact-type provenance question — "what produced this, and what
did it in turn produce" — only same-type history (regression, changelog)
and same-artifact scoring/coverage.

**Alternatives considered.**
- *A dedicated experiment-tracking service (MLflow/W&B-style).* Rejected
  — the first piece of infrastructure in this repository requiring a
  running process or external account, for a capability a file scan
  already answers in milliseconds at this project's scale (ADR-0011
  Decision 2's Rejected Alternative). Swapping in a real backend later
  remains possible without changing `VersionRecord`'s schema — the edges
  this ADR defines are the portable part.
- *A persisted lineage index file, updated incrementally.* Rejected for
  the same reason ADR-0006 Decision 5 rejected a hand-maintained
  changelog: a second source of truth that can drift from the
  `version.json` files it summarizes.
- *Folding `lineage_parents` into `previous_artifact_id` as a list instead
  of a new field* (i.e., "previous" becomes multi-valued and
  cross-type). Rejected — conflates two genuinely different relationships
  under one name, which is precisely the ambiguity ADR-0011 Decision 1
  exists to avoid; `regression.py` would then need to filter same-type
  entries out of a mixed list on every call instead of reading one
  unambiguous field.

**Final design decision.** (Full detail in `docs/ADR/0011-experiment-
tracking-lineage.md`.) `LineageEdge` on `VersionRecord.lineage_parents`;
`src/quality/lineage.py::build_lineage_graph()` /
`trace_ancestors()` / `trace_descendants()`; a new `LineageSection`
registered into `SECTION_REGISTRY` under `"dataset"`; artifact-root paths
declared in config now for all four artifact types even though only
`dataset` exists yet; a lineage-aware `GateCheck` implemented and tested
now, activated by Phase 4 later.

**Dependency diagram.**
```
version.json (any artifact type) ──read by──► lineage.py::build_lineage_graph()
        │                                              │
        │ (written by versioning.py at build time)     ▼
        │                                        LineageGraph
        │                                         │        │
        │                                         ▼        ▼
        │                               trace_ancestors  trace_descendants
        │                                         │        │
        │                                         ▼        ▼
        └── consumed by ──► gates.py (lineage check)   dashboard.py (LineageSection)
```

**Public interfaces.**
```python
# src/quality/lineage.py
@dataclass
class LineageNode:
    artifact_type: str
    artifact_id: str
    artifact_dir: Path
    version: VersionRecord

@dataclass
class LineageGraph:
    nodes: dict[str, LineageNode]      # keyed "{artifact_type}:{artifact_id}"
    edges: list[tuple[str, str, str]]  # (child_key, parent_key, relation)

def build_lineage_graph(cfg: QualityEngineeringConfig) -> LineageGraph: ...
def trace_ancestors(graph: LineageGraph, artifact_type: str, artifact_id: str) -> list[LineageNode]: ...
def trace_descendants(graph: LineageGraph, artifact_type: str, artifact_id: str) -> list[LineageNode]: ...
def evaluate_lineage_check(
    graph: LineageGraph, artifact_type: str, artifact_id: str,
    expected_parent_type: str, expected_parent_id: str,
) -> GateCheckResult: ...
```

**Configuration additions.**
```yaml
quality_engineering:
  lineage:
    artifact_roots:
      dataset: "data/processed/datasets"
      model: "outputs/training"        # = training.output_dir, cross-referenced not duplicated
      evaluation: "outputs/evaluation"  # reserved for Phase 5
      deployment: "outputs/deployment"  # reserved for Phase 6
```

**Risks / trade-offs.**
- *Risk:* scanning every artifact root on every `build_lineage_graph()`
  call could get slow as artifact counts grow. *Mitigation:* not a
  concern at this project's scale (dozens, not millions, of artifacts);
  documented in `docs/ARCHITECTURE_DECISIONS.md` as a known scaling limit
  matching ADR-0011 Decision 2's own stated fallback (swap the
  implementation, not the schema, if this is ever outgrown).
- *Trade-off:* `lineage.py` is fully built now even though, until Phase 4
  ships, it only ever has dataset nodes with no cross-type edges to show —
  an intentional exception to ADR-0010 §5's "don't pre-build modules
  nothing has asked for yet" principle, justified because the *shape* of
  the derivation graph (dataset → model → evaluation → deployment) is
  already fully specified by `docs/PHASES.md`'s existing roadmap, unlike
  `model_metrics.py`'s contents, which genuinely cannot be known before a
  model architecture is chosen.

**Supports Phase 4/5/6 by:** being the literal mechanism ADR-0010 now
points to for checkpoint/eval/deployment provenance — every "does this
model's data match" gate check in ADR-0010 §§2–4 is a call to
`evaluate_lineage_check()`, not new code.

---

## 5. Duplication Caught During This Revision

**Finding E — `MetricRegistry` and a newly-needed `SectionRegistry` were
about to be two classes with an identical shape.** Generalizing metric
registration to be category-based (enhancement 3) meant dashboard section
registration needed the same treatment for consistency (a dashboard
section is exactly as artifact-type-scoped as a metric — ADR-0009's
original flat `DATASET_SECTIONS` list would have been the same "one flat
list, ad hoc parallel list later" problem enhancement 3 fixes for
metrics). Drafting both independently produced two classes:
`register(category, item)` / `all(category)`, written twice.

**Fix:** extracted `src/quality/registry.py::CategoryRegistry[T]` — one
generic implementation, instantiated twice
(`METRIC_REGISTRY: CategoryRegistry[Metric]`,
`SECTION_REGISTRY: CategoryRegistry[DashboardSection]`). See §3 and
ADR-0004 Decision 6b for the full rationale, and
`docs/ARCHITECTURE_REVIEW.md`'s "Revision 2" addendum for the review
verdict.

No other duplication was found reviewing this revision's changes against
the rest of the design: `versioning.py`'s generalization reuses
`artifacts.py::hash_content()` unchanged (§2); `lineage.py` reuses
`artifacts.py::load_artifact_envelope()` rather than re-parsing
`version.json` itself; `regression.py`'s `baseline_artifact_id`/
`candidate_artifact_id` rename (ADR-0007's revision note) required no new
logic, only a field rename.

---

## 6. Updated Repository Tree

Only the `src/quality/` section changes from the previously presented
tree; everything under `config/`, existing `src/data/`, `scripts/`, and
`tests/` is unchanged by this revision.

```
src/quality/
├── __init__.py
├── schemas.py              [Artifact base + all other shared dataclasses — Enhancement 1]
├── registry.py              [NEW — CategoryRegistry[T] — Enhancement 3 / Finding E]
├── config.py
├── artifacts.py             [DatasetArtifact (renamed) + hash_content() + load_artifact_envelope() — Enhancement 1]
├── metrics.py                [METRIC_REGISTRY: CategoryRegistry[Metric] — Enhancement 3]
├── dataset_metrics.py
├── scoring.py
├── versioning.py             [generic across artifact types — Enhancement 2]
├── regression.py             [artifact_id-named fields — Enhancement 2 ripple]
├── coverage.py
├── review.py
├── gates.py                  [+ lineage-aware GateCheck — Enhancement 4]
├── dashboard.py              [SECTION_REGISTRY: CategoryRegistry[DashboardSection] — Enhancement 3]
└── lineage.py                 [NEW — Enhancement 4 / ADR-0011]
```

Net change from the previous tree: **+2 files** (`registry.py`,
`lineage.py`), 0 files removed, `artifacts.py`/`schemas.py`/
`versioning.py`/`regression.py`/`dashboard.py`/`metrics.py` internally
revised (interfaces changed, file count unchanged).

## 7. Updated Cross-Module Dependency Graph

```
config/default.yaml (quality_engineering:, incl. new lineage.artifact_roots)
        │
        ▼
src/quality/config.py
        │
        ▼
src/quality/schemas.py  ◄── Artifact base (Enhancement 1)
        │
        ▼
src/quality/registry.py  ◄── CategoryRegistry[T] (Enhancement 3, no deps within src/quality)
        │
        ▼
src/quality/artifacts.py ──uses──► src.data.dataset_schemas, dataset_io, episode.compute_route_hash
        │
        ├──► src/quality/metrics.py (METRIC_REGISTRY = CategoryRegistry[Metric]())
        │           │
        │           ▼
        │     src/quality/coverage.py
        │           │
        │           ▼
        │     src/quality/dataset_metrics.py (registers under "dataset")
        │           │
        │           ▼
        │     src/quality/scoring.py ──writes──► quality_score.json
        │
        ├──► src/quality/regression.py ──writes──► regression_report.json
        │           │
        │           ▼
        │     src/quality/versioning.py ──writes──► version.json (generic), CHANGELOG.md
        │           │
        │           ▼
        │     src/quality/lineage.py ──reads version.json across artifact_roots──► LineageGraph
        │
        └──► (scoring + coverage + regression + lineage) ──► review.py ──writes──► review.json
                                                        └──► gates.py ──writes──► gate_report.json
                                                                     │
        (scoring, versioning, regression, coverage, gates, lineage) │
                                                                     ▼
                                              dashboard.py (SECTION_REGISTRY = CategoryRegistry[DashboardSection]())
                                                                     │
                                                                     ▼
                                                          dataset_dashboard.html
```

## 8. Updated Implementation Order

Steps 1–4 are unchanged from the original design review. `registry.py`
now precedes both `metrics.py` and `dashboard.py`; `lineage.py` is
inserted after `versioning.py`, before `review.py`/`gates.py` (both may
optionally consume it).

1. Schema additions (`weather`, `duplicate_sample_count`, `weather` tally
   — unchanged from the original design review, Enhancement 1/2 do not
   touch `src/data/`).
2. `src/quality/schemas.py` (incl. `Artifact` base), `config.py`.
3. `src/quality/registry.py` — build and unit-test `CategoryRegistry[T]`
   in isolation before anything consumes it.
4. `src/quality/artifacts.py` (`DatasetArtifact`, `hash_content()`,
   `load_artifact_envelope()`, `resolve_latest_dataset_dir()` — the
   Finding D refactor from the first design review still applies
   unchanged).
5. `src/quality/metrics.py`, `coverage.py`, `dataset_metrics.py`,
   `scoring.py` — `metrics.py` now just instantiates
   `CategoryRegistry[Metric]()` rather than implementing its own class.
6. `src/quality/regression.py` (with the `artifact_id`-named fields).
7. `src/quality/versioning.py` (generic `VersionRecord`,
   `content_hashes`/`summary_counts`, `lineage_parents` parameter accepted
   but populated with an empty list until step 8 exists).
8. `src/quality/lineage.py` — `LineageEdge`, `build_lineage_graph()`,
   traversal functions, `evaluate_lineage_check()` (implemented and
   tested now, not activated in any gate list until Phase 4).
9. `src/quality/review.py`, `gates.py` — `gates.py` includes the lineage
   check function but does not register it into the active dataset
   training-gate list (nothing to check yet, per ADR-0011 Decision 5's
   consequence).
10. `src/quality/dashboard.py` — `SECTION_REGISTRY` instantiated from
    `registry.py`; seven sections registered under `"dataset"`, including
    the new `LineageSection`.
11. `scripts/_format.py`, six new CLI scripts (unchanged from the
    original design review).
12. `config/default.yaml` (`quality_engineering:` section, now including
    `lineage.artifact_roots`), `Makefile`.
13. `tests/unit/test_quality_engineering.py` — written alongside steps
    2–10; final pass adds `TestRegistry` (generic `CategoryRegistry`
    behavior, exercised via both a `Metric` and a `DashboardSection`),
    `TestLineage` (graph construction, ancestor/descendant traversal,
    missing-root handling), plus the previously-planned hash-consistency
    and golden-output tests.
14. Documentation, `docs/PHASES.md`/`README.md`/`AGENTS.md` updates, full
    validation suite, Phase 3.5 Implementation Report — unchanged from
    the original design review's steps 12–13.
