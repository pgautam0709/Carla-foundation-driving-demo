# Phase 3.5 Architecture Review

**Date:** 2026-07-20
**Reviewed:** ADR-0004 through ADR-0010 (`docs/ADR/0004`–`0010`)
**Status:** Design approved for implementation, with two corrections made
during this review (folded back into ADR-0004/0005/0006/0007 before this
document was written, not left as follow-up work)

This is the mandatory critical review required before implementation
begins. It is organized around the seven questions specified for this
phase.

---

## 1. Is any module unnecessary?

No module in the ADR-0004 table (`artifacts.py`, `metrics.py`,
`dataset_metrics.py`, `scoring.py`, `versioning.py`, `regression.py`,
`coverage.py`, `review.py`, `gates.py`, `dashboard.py`) can be merged into
another without violating single-responsibility or creating a hidden
dependency cycle:

- `review.py` and `gates.py` both consume `scoring.py` + `regression.py` +
  `coverage.py` output, but they answer different questions (a *narrative*
  for a human vs. a *pass/fail verdict* for an automated caller like a
  future `train.py`) and have different callers (`make review` /
  `make quality`'s human output, vs. a script that must exit non-zero).
  Merging them would force every future gate-check consumer to also parse
  human-readable star ratings.
- `dashboard.py` could theoretically inline `review.py`'s narrative
  generation, but ADR-0009 Decision 3 already draws this exact boundary
  ("the dashboard never computes anything") specifically so a dashboard bug
  and a scoring bug are never the same bug.

One module was initially *missing*, not unnecessary: `artifacts.py`. The
original ADR-0004 draft had `scoring.py`, `versioning.py`, `regression.py`,
`coverage.py`, and `dashboard.py` each independently reading and parsing
`dataset_manifest.json` / `episodes_index.jsonl` / `stats.json` /
`quality_report.json`. That is five near-identical file-reading
implementations for the same five files — caught during this review and
corrected by introducing `artifacts.py` as the single load/parse layer
(now reflected in ADR-0004 Decision 2's module table and Decision 2's
rationale). This is the first of the two corrections referenced above.

## 2. Is anything duplicated?

Two duplications were found and corrected before this document was
finalized:

1. **File-parsing logic**, five times over — fixed by `artifacts.py`
   (Question 1, above).
2. **Content-hashing logic** — the original ADR-0006 draft specified a new
   `hash_json_file()` helper with its own canonicalize-then-hash
   implementation, duplicating what `compute_route_hash()`
   (`src/data/episode.py`) already does. Corrected: `artifacts.py` now
   exposes `hash_content()`, which reuses `compute_route_hash()`'s
   canonicalization strategy (sorted-key JSON, SHA-256) and differs only
   in not truncating the digest — a deliberate, documented difference
   (full digest for provenance verification vs. 8-char prefix for a
   filename-embeddable label), not an accidental reimplementation. See
   ADR-0006 Decision 2.

No other duplication was found. In particular:
- `scoring.py`'s `coverage` metric calls `coverage.py::compute_coverage()`
  rather than re-deriving cell coverage (ADR-0005 Decision 1 consequence,
  added during this review pass).
- `versioning.py`'s changelog generator calls `regression.py` rather than
  diffing datasets a second way (ADR-0006 Decision 5 — this was correct in
  the original draft).
- The six new `make` targets share zero business logic with each other in
  their CLI layer — each calls exactly one `src/quality/` function
  (ADR-0004 Decision 4), so there is no CLI-level duplication to find.

## 3. Are responsibilities clean?

Yes, verified against a simple test: for each of the ten deliverables in
the brief, exactly one module owns the decision logic, and every other
module that touches the same concern is provably composition-only
(reads that module's dataclass output, does not recompute).

| Deliverable | Owning module | Composition-only consumers |
|---|---|---|
| Quality Engine | `scoring.py` (+ `dataset_metrics.py`) | `review.py`, `gates.py`, `dashboard.py` |
| Dataset Versioning | `versioning.py` | `dashboard.py` |
| Regression Detection | `regression.py` | `versioning.py` (changelog), `gates.py`, `dashboard.py` |
| Engineering Review | `review.py` | `dashboard.py` |
| Coverage Planner | `coverage.py` | `scoring.py` (coverage metric), `review.py`, `dashboard.py` |
| Engineering Dashboard | `dashboard.py` | — (leaf; nothing consumes its output programmatically) |
| Validation Gates | `gates.py` | `dashboard.py`, future `scripts/train.py` (Phase 4) |
| Metrics Framework | `metrics.py` | `dataset_metrics.py` today; `model_metrics.py`/etc. later (ADR-0010) |

No module appears twice in the "owning" column. This table is the
concrete evidence for ADR-0004 Decision 2's claim, not just an assertion.

## 4. Can future phases extend this?

Yes — this is what ADR-0010 exists to demonstrate, walked through
per-phase (Phase 4: BC/Diffusion/Foundation/VLA all map to the same
`model_metrics.py` + gate-check + dashboard-section pattern; Phase 5:
simulation metrics plus reuse of `regression.py`'s comparator for
eval-run-vs-eval-run diffing; Phase 6: deployment metrics with the
identical pattern again). The review's own contribution here is
confirming the negative: nothing found while drafting ADR-0004–0009
required a fifth extension point beyond the four listed in ADR-0004's
"Extension Points" section — every future-phase integration in ADR-0010
maps onto `MetricRegistry.register()`, the dashboard section list, the
`GateCheck` list, or `regression.py`'s comparator, with no new mechanism
invented for any of the three future phases.

## 5. Can components be reused?

Reuse of *existing* (Phase 3a/3b) code was checked module by module:

- `artifacts.py` reuses `src.data.dataset_io.read_jsonl_records` and the
  existing `src.data.dataset_schemas` dataclasses instead of introducing
  parallel ones.
- `versioning.py`'s hashing reuses `compute_route_hash()`'s
  canonicalization approach (Question 2).
- `coverage.py`'s "most recently built dataset" resolution and
  `compare_datasets.py`'s default-baseline resolution both reuse the
  existing "most recent by mtime" convention already established by
  `inspect_dataset.py` / the Makefile's `EPISODE_DIR`/`DATASET_DIR`
  defaults, rather than inventing a second "which one is latest" rule
  (ADR-0006 Decision 4, ADR-0007 Decision 2).
- `dataset_splits.py`'s existing "normalize weights, don't require them to
  sum to 1" convention is explicitly reused by `scoring.py`'s weight
  handling (ADR-0005 Decision 2) instead of writing a second normalization
  rule.
- `EpisodeValidator` is not touched or forked — `scoring.py`'s `metadata`
  metric reads its already-computed pass/fail counts from
  `quality_report.json` (via `artifacts.py`), continuing ADR-0002 Decision
  6's precedent of one validator, reused everywhere.

No case was found where a new module reimplements something Phase 0–3b
already provides.

## 6. Does configuration own behavior?

Every threshold, weight, target, and path introduced across ADR-0004–0009
lives under a new `quality_engineering:` section in `config/default.yaml`
(scoring weights and grade bands — ADR-0005; regression warning/failure
thresholds — ADR-0007; coverage target matrix and minimums — ADR-0008;
dashboard output path and trend window — ADR-0009; gate pass/fail
thresholds — ADR-0004). Every `src/quality/` public function takes its
configuration as an explicit dataclass parameter rather than reading
`config/default.yaml` internally (ADR-0004 Decision 5), matching the
existing `OutlierThresholds` precedent from Phase 3b. This was checked
against every ADR's own "no magic numbers" claims and found consistent —
the one near-miss (the `duplicates` metric needing a field
`quality_report.json` didn't yet expose) was a data-availability gap, not
a hardcoded-number gap, and is fixed per Question 1/2 above.

## 7. Are abstractions correct?

Three abstractions carry the whole design and were each stress-tested
against a concrete future case, not just accepted on their stated merits:

- **`MetricRegistry`** — stress-tested against Phase 5's simulation
  metrics (route completion, collision rate, speed, jerk): these are nothing
  like dataset metrics in what they measure, but identical in shape (a
  named value normalized/reported with a detail string) — the abstraction
  holds.
- **`DashboardSection` list** — stress-tested against "what if a future
  section needs data no existing `DashboardContext` field carries" (e.g. a
  model checkpoint path): the context is a plain dataclass a future phase
  can extend by adding a field, or a section can load its own extra data
  inside its `render()` — the mechanism doesn't need to anticipate every
  future data shape today, only that sections are pluggable.
- **`RegressionFinding`/comparator** — stress-tested against Phase 5's
  reuse case (comparing two eval runs, not two datasets): the comparator's
  actual contract is "two named metric snapshots + configurable per-
  dimension thresholds," which is already dataset-shape-agnostic in the
  ADR-0007 Decision 5 design — confirmed correct rather than assumed.

One abstraction was deliberately **not** built, and that is itself a
reviewed decision: a general n-dimensional coverage target (town × weather
× route × time-of-day × ...) was considered and rejected in ADR-0008
Decision 1 in favor of a fixed two-dimensional town × weather matrix,
because CARLA weather presets and towns are both closed, enumerable sets
today, while route names are not yet standardized. Building a fully
generic N-dimensional coverage engine now would be speculative generality
for dimensions nothing in this repository can populate consistently yet —
consistent with ADR-0010 Decision 5's explicit principle of not building
ahead of a validated need.

---

## Outstanding items carried into implementation (not blocking, tracked here so they aren't lost)

1. **Route-based coverage** is reported informationally but not part of
   the gated target matrix (ADR-0008 Decision 1) — a natural, isolated
   follow-up once route naming is standardized; requires no changes to
   `coverage.py`'s cell-ranking mechanism, only a config/data change.
2. **Historical datasets** (built before this phase) will show as
   "unversioned" in the dashboard and are excluded from the quality-trend
   chart until `make version` is run against them retroactively — this is
   the expected, documented behavior (ADR-0006 Decision 1 consequence),
   not a bug to fix during implementation.
3. **`duplicate_sample_count` and `weather` require one dataset rebuild**
   to appear in existing datasets' artifacts — `data/processed/datasets/`
   currently has two builds (`dataset_20260708_031851`, `dry_run_dataset`)
   that predate this schema version; implementation should confirm
   `inspect_dataset.py` and `build_dataset.py` still handle a rebuild
   cleanly and that old datasets without the new fields don't crash any
   Phase 3.5 reader (`artifacts.py` must default missing fields, not
   require them).

---

## Verdict

Design approved. Implementation may proceed against ADR-0004 through
ADR-0010 as corrected in this review.

---

## Revision 2 (2026-07-20, same day) — Artifact generalization, category-based registries, ADR-0011

A second design pass, requested before implementation began, generalized
four things the first pass had scoped to datasets only:

1. `DatasetArtifacts` → `Artifact` (generic envelope) + `DatasetArtifact`
   (dataset-specific subtype).
2. ADR-0006 "Dataset Versioning" → "Artifact Versioning" (renamed file,
   `dataset_id`/`previous_dataset_id` → `artifact_id`/
   `previous_artifact_id`, fixed hash/count fields → named
   `content_hashes`/`summary_counts` dicts, new `lineage_parents` field).
3. `DATASET_METRIC_REGISTRY` → one process-wide, category-based
   `METRIC_REGISTRY`.
4. New ADR-0011 (Experiment Tracking & Lineage) — cross-artifact-type
   derivation graph.

Re-running the same seven review questions against this revision:

- **Unnecessary module?** No — `lineage.py` and `registry.py` (below)
  both satisfy the same test as the first pass: each has exactly one
  reason to change, and each is called from more than one place.
- **Duplicated?** One real duplication was introduced *by this revision
  itself* and caught before finalizing: `MetricRegistry` and a newly
  drafted `SectionRegistry` (for dashboard sections, needed once section
  registration also became category-based) were two classes with an
  identical shape. Fixed by extracting `src/quality/registry.py`'s
  generic `CategoryRegistry[T]`, used by both — see ADR-0004 Decision 6b.
  This is the same pattern as the first review's Finding (missing
  `artifacts.py`): a second pass surfaced a duplication a first pass's
  own new content had created, and it was corrected in the same pass
  rather than deferred.
- **Clean responsibilities?** Yes, re-verified: `registry.py` knows
  nothing about metrics or dashboards specifically (pure container);
  `lineage.py` knows nothing about scoring, coverage, or regression (pure
  graph traversal over `VersionRecord`s already computed elsewhere).
- **Future phases can extend?** Strengthened, not just preserved — the
  fifth extension point (lineage edges) directly resolves a wart in the
  first pass, where ADR-0010 §2 described checkpoint provenance as an ad
  hoc field comparison. It is now a graph edge, which composes through
  multiple hops for free (Phase 6 checking back through a checkpoint to
  its dataset).
- **Components reused?** `CategoryRegistry[T]` reused twice already
  (metrics, dashboard sections) before a single line of implementation
  code exists — the strongest form of "designed for reuse" this phase can
  demonstrate pre-implementation.
- **Configuration owns behavior?** Unaffected — no new thresholds or
  weights were introduced by this revision; `quality_engineering.lineage.
  artifact_roots` is the one new config surface (ADR-0011 Decision 3),
  and it is a path mapping, not a tunable number.
- **Abstractions correct?** The `previous_artifact_id` vs.
  `lineage_parents` split (ADR-0011 Decision 1) was specifically
  stress-tested against the failure mode it exists to avoid — conflating
  "prior version of me" with "what I was derived from" — and confirmed
  they must stay separate fields, not a single overloaded pointer.

**Verdict:** Revision approved. Implementation may proceed against
ADR-0004 through ADR-0011 as corrected in this review and its second
pass.
