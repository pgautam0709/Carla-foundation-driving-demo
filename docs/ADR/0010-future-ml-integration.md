# ADR 0010 — Future ML Integration

**Date:** 2026-07-20 (revised same day — see Revision Note)
**Status:** Proposed
**Deciders:** AI Engineering Team

> **Revision note:** ADR-0004 now names **five** extension points, not
> four — the fourth is registry/list *categories* rather than parallel
> globals (`METRIC_REGISTRY.register("model", ...)`, not a new
> `MODEL_METRIC_REGISTRY`; `SECTION_REGISTRY.register("model", ...)`, not
> a new `MODEL_SECTIONS` list), and the fifth is `lineage.py`'s
> derivation graph (ADR-0011). Every mention below of a "parallel"
> registry/list, or of a checkpoint's provenance as an ad hoc field
> comparison, is updated to the category- and lineage-based mechanisms
> those ADRs now define. No phase-by-phase conclusion changes — Phase
> 4/5/6 still integrate with zero new mechanisms invented — the
> mechanisms themselves are simply more precisely specified now.

---

## Context

Phase 3.5 is explicitly forbidden from implementing Behavioural Cloning,
Diffusion Policies, Foundation Models, RL, VLA, or closed-loop evaluation
(Non-Goals). Its job is to make sure that when Phase 4 (and 5, and 6)
*do* implement those, they extend the engineering loop built here instead
of inventing a parallel one. This ADR is the concrete proof-of-fit: for
each future capability, it names the exact extension point (from ADR-0004)
it plugs into, and confirms nothing in ADR-0004–0009 (and, as of this
revision, ADR-0011) needs to change shape to accommodate it.

---

## Decisions

### 1. The five extension points from ADR-0004 are the only integration surface any future phase needs

Restated concretely, with the mechanism each future phase actually calls:

| Extension point | Mechanism | Who calls it |
|---|---|---|
| `METRIC_REGISTRY.register(category, metric)` | Add a `Metric` subclass in a new module; register it under a new category (`"model"`, `"simulation"`, `"deployment"`) at the CLI entry point — same registry object every time | Phase 4/5/6 |
| `SECTION_REGISTRY.register(category, section)` | Append a `DashboardSection` under the same new category | Phase 4/5/6 |
| `GateCheck` list in `gates.py` | Append a named check returning pass/fail + reason | Phase 4/5/6 |
| `regression.py`'s comparator interface | Call `compare_metric_snapshots()` with two arbitrary metric dicts instead of two dataset artifacts | Phase 5 (eval-run comparison) |
| `lineage.py`'s `lineage_parents` edges | Populate `VersionRecord.lineage_parents` when writing a new artifact type's version record; `lineage.py` itself needs no change | Phase 4/5/6 |

No future phase needs a sixth extension point invented from scratch — this
was the explicit design goal of ADR-0004 Decision 2 (composition-only
modules) and is verified per-phase below.

### 2. Phase 4 — Behavioural Cloning / Diffusion Policies / Foundation Models / VLA (training-time models)

**Decision:** These all integrate identically, regardless of architecture,
because none of them change what a "model metric" *is* — only what
produces it.

- **New module:** `src/quality/model_metrics.py`, registering metrics like
  `validation_loss`, `steering_mae`, `checkpoint_convergence` under
  category `"model"` in the same `METRIC_REGISTRY`
  `dataset_metrics.py` already registers `"dataset"` into.
- **New dashboard section:** a `ModelMetricsSection` registered under
  category `"model"` in `SECTION_REGISTRY` (ADR-0009 Decision 2, revised),
  reading a new `training_report.json` the same way existing sections read
  `quality_score.json`.
- **New gate check, formalized as a lineage check:** `gates.py` gains a
  check that walks the checkpoint's `VersionRecord.lineage_parents`
  (ADR-0011) and passes only if it contains the dataset artifact about to
  be evaluated — the exact `evaluate_lineage_check()` helper ADR-0011
  Decision 5 already implements and unit-tests, activated here by
  registering it into Phase 4's gate-check list. This is a direct
  formalization of "checkpoint's recorded `dataset_id` and `config_hash`
  match," expressed as a graph edge instead of a hand-written field
  comparison, so it composes with deeper chains (Phase 6 checking back
  through a checkpoint to its dataset, for instance) for free.
- **`scripts/train.py` (Phase 4) calls `evaluate_training_gate()` before
  starting a training run** — the exact function `gates.py` already
  exposes for this purpose (ADR-0004 extension point 3) — and refuses to
  start if the gate reports failure, satisfying the brief's "Validation
  Gates ... If any fail, training must stop" requirement without Phase 4
  needing to reimplement gate logic.
- **Checkpoint provenance:** `scripts/train.py` calls
  `versioning.py::write_version_artifacts(checkpoint_dir,
  artifact_type="model", lineage_parents=[LineageEdge("dataset",
  dataset_artifact_id, "trained_on")], cfg)` when a training run
  finishes — the identical function `make version` already calls for
  datasets, just with `artifact_type="model"` and one lineage edge back
  to the dataset it trained on. No new versioning code.

**Why no redesign is needed:** A BC-CNN, a diffusion policy, and a
foundation model all eventually produce a training/eval loss curve and a
checkpoint — they differ in architecture, not in "what shape of metric do
they report." `model_metrics.py` and `training_report.json` are agnostic
to which of the four produced them; the registry and gate mechanisms do
not know or care which model architecture is in use.

### 3. Phase 5 — Evaluation & Explainability (closed-loop simulation)

**Decision:**
- **New module:** `src/quality/simulation_metrics.py`, registering
  `route_completion`, `collision_rate`, `avg_speed_kmh`, `jerk` — the exact
  metrics already named in `docs/PHASES.md`'s Phase 5 section — under
  category `"simulation"` in the same `METRIC_REGISTRY`.
- **Regression reuse:** comparing "trained model A's eval run" against
  "trained model B's eval run" is the same operation as comparing two
  datasets (ADR-0007) — both are "compare two named snapshots of metric
  values" — so Phase 5 calls `regression.py::compare_metric_snapshots()`
  with two `dict[str, float]` metric snapshots (one per eval run) instead
  of two dataset artifacts. The severity/threshold mechanics (config-owned
  warning/failure thresholds, ADR-0007 Decision 4) are unchanged; only the
  config section name differs (`quality_engineering.regression.model_eval`
  alongside today's `quality_engineering.regression` for datasets).
- **New dashboard section:** `SimulationMetricsSection`, registered under
  category `"simulation"`, same pattern as Decision 2.
- **Lineage:** an evaluation run's `version.json` records
  `lineage_parents=[LineageEdge("model", checkpoint_artifact_id,
  "evaluated")]` — `lineage.py`'s ancestor traversal (ADR-0011 Decision 4)
  then walks eval run → checkpoint → dataset in one call, with no
  Phase-5-specific code in `lineage.py` itself.

**Why no redesign is needed:** `regression.py`'s core comparator (ADR-0007
Decision 5's flat `list[RegressionFinding]`) was deliberately built as "diff
two named metric snapshots with configurable per-dimension thresholds," not
as "diff two dataset directories" — the dataset-specific field list
(Decision 3 of ADR-0007) is the *caller's* input, not something baked into
the comparison algorithm itself.

### 4. Phase 6 — Deployment Packaging (ONNX/TensorRT)

**Decision:**
- **New module:** `src/quality/deployment_metrics.py`, registering
  `onnx_parity_max_diff`, `tensorrt_latency_ms`, `inference_fps` — the
  exact metrics named in `docs/PHASES.md`'s Phase 6 success criteria —
  under category `"deployment"` in the same `METRIC_REGISTRY`.
- **New gate check:** a `deployment_readiness` gate mirroring the training
  gate's shape (pass/fail + reasons) but checked before packaging rather
  than before training — its own named `GateCheck` list (`gates.py`
  supports more than one named list, e.g. `TRAINING_GATE_CHECKS` and
  `DEPLOYMENT_GATE_CHECKS`, both built from the identical `GateCheck`
  interface — a second list, not a second interface).
- **New dashboard section:** `DeploymentMetricsSection`, registered under
  category `"deployment"`.
- **Lineage:** a deployment package's `lineage_parents` points back to the
  checkpoint it was exported from, completing the full
  dataset → model → evaluation → deployment chain `lineage.py` can walk
  end to end (ADR-0011 Decision 4).

**Why no redesign is needed:** Same registry (different category), same
section-registry mechanism (different category), same gate-check
interface (a second named list), same lineage-edge mechanism as Phases 4
and 5 — deployment metrics are not qualitatively different from model or
simulation metrics for the purposes of this framework; they are just
another named, thresholded, dashboarded, lineage-linked quantity.

### 5. What Phase 3.5 deliberately does *not* build, so Phase 4+ isn't blocked by scope it doesn't need yet

**Decision:** `src/quality/model_metrics.py`,
`simulation_metrics.py`, and `deployment_metrics.py` are **not** created in
this phase — only the registry and section-list mechanisms they will
register into are. `training_report.json`'s schema is not defined here
either; Phase 4 owns that schema the same way `src/data/dataset_schemas.py`
is owned by Phase 3, not Phase 2.

**Rationale:** Defining `model_metrics.py` now, before a single model
exists, would mean guessing at what a training report actually needs to
contain — precisely the kind of premature, unvalidated design ADR-0003
Decision 2 already warned against for near-duplicate detection ("a
capability nobody has asked for yet"). The extension points are proven
sufficient by this ADR's per-phase walkthroughs; the concrete metrics
files are each future phase's own first PR, using a pattern that is
already fully worked out and tested by the time they need it.

**Consequences:** Phase 4's "Definition of Done" for its own quality-system
integration is small and mechanical: one new metrics module (registered
under a new category), one dashboard section (registered under the same
category), one gate check, one lineage edge on its `VersionRecord` — all
following the exact shape `dataset_metrics.py` / the `"dataset"`-category
dashboard sections / existing `GateCheck`s / ADR-0011's `LineageEdge`
already demonstrate in this phase's implementation and tests.
