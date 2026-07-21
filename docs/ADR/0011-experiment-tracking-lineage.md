# ADR 0011 — Experiment Tracking & Lineage

**Date:** 2026-07-20 (second revision pass, same day)
**Status:** Proposed
**Deciders:** AI Engineering Team

---

## Context

ADR-0006 (as revised, "Artifact Versioning") gives every artifact —
dataset, and later model checkpoint, evaluation run, deployment package —
a `VersionRecord` with a `previous_artifact_id` pointer to the prior
version of *the same* artifact. That answers "what changed since the last
dataset build." It does not answer the question that spans the whole
loop: **given a trained checkpoint, which exact dataset version trained
it, under which config, and which evaluation runs and deployment packages
descended from it?** That is a cross-artifact-type question —
provenance running *forward* from a dataset through training, evaluation,
and deployment — and nothing in ADR-0004–0010 answers it, because every
prior ADR deliberately scoped itself to one artifact type at a time
(datasets) or one artifact against its own immediate predecessor.

This ADR adds the minimum mechanism needed to answer it: a
**derivation edge** on every `VersionRecord` (`lineage_parents`,
introduced in the ADR-0006 revision) and one new module,
`src/quality/lineage.py`, that reconstructs the full graph on demand from
`version.json` files already on disk. It does not add a "training run"
concept, an experiment database, or anything Phase 4 hasn't asked for yet
— it adds the graph structure Phase 4–6 will need the day they start
writing their own `version.json` files, so that day requires zero new
schema design.

---

## Decisions

### 1. Lineage is a directed acyclic graph over artifact versions, distinct from — and orthogonal to — same-type version history

**Decision:** Two different edges exist on `VersionRecord`, and they are
never conflated:

- **`previous_artifact_id`** (ADR-0006) — "the prior version of *this same
  artifact*." `dataset_v(n)`'s previous is `dataset_v(n-1)`. This is what
  `regression.py` (ADR-0007) diffs against by default.
- **`lineage_parents: list[LineageEdge]`** (this ADR) — "the artifacts
  *this artifact was derived from*," which may be a different artifact
  type entirely. A model checkpoint's lineage parent is the dataset
  version it trained on. An evaluation run's lineage parent is the
  checkpoint it evaluated. A deployment package's lineage parent is the
  checkpoint it was exported from.

```python
@dataclass
class LineageEdge:
    parent_artifact_type: str    # "dataset" | "model" | "evaluation" | "deployment"
    parent_artifact_id: str
    relation: str                # human-readable label, e.g. "trained_on", "evaluated", "exported_from"
```

**Rationale:**
- Conflating the two would make "what's the previous version of this
  dataset" and "what did this checkpoint train on" the same query with two
  different meanings depending on artifact type — exactly the kind of
  ambiguity ADR-0004's "one reason to change per module" principle exists
  to prevent. Keeping them as two explicit, separately-named fields means
  `regression.py` only ever walks `previous_artifact_id` (same-type
  history) and `lineage.py` only ever walks `lineage_parents`
  (cross-type derivation) — neither module needs to guess which kind of
  edge it's looking at.
- A DAG (not a general graph) is sufficient because nothing in this
  pipeline's derivation chain is cyclic: a dataset is never derived from a
  checkpoint, a checkpoint is never derived from a deployment package. The
  natural direction (data → model → evaluation → deployment) is exactly
  the Collect → Train → Compare → Deploy loop from ADR-0004's opening
  diagram, made explicit as edges instead of prose.

**Consequences:** A future artifact type that genuinely needs a cycle
(none currently anticipated) would require revisiting this decision — not
expected, called out for completeness.

### 2. The graph is reconstructed on demand from `version.json` files; nothing is persisted as a second source of truth

**Decision:** `src/quality/lineage.py::build_lineage_graph(cfg)` scans
every directory under each configured `artifact_roots` path (Decision 3),
reads every `version.json` it finds via `artifacts.py`, and assembles an
in-memory `LineageGraph`. No lineage database, index file, or cache is
written to disk.

**Rationale:** Directly continues ADR-0004 Decision 3 (no database, no
service) and reuses the exact justification ADR-0006 Decision 5 already
gave for generating `CHANGELOG.md` from `regression.py` output instead of
hand-maintaining it: a derived view that could drift from the artifacts it
describes is worse than recomputing it each time, especially since
`version.json` files are small and scanning them is cheap (the same
scale argument ADR-0007 Decision 3 already made for regression
comparison — a handful of small JSON files, not raw frame data).

**Rejected alternative — a dedicated experiment-tracking service
(MLflow/Weights & Biases style):** rejected for the same reason ADR-0009
rejected a live dashboard server: it would be the first piece of
infrastructure in this repository requiring a running process or external
account, for a capability (answering "what trained on what") that a
file scan already answers in milliseconds at this project's scale. If a
future phase outgrows file-scan-based lineage (e.g. thousands of
artifacts), swapping `lineage.py`'s implementation for a real tracking
backend is possible without changing `VersionRecord`'s schema — the edges
this ADR defines are the portable part.

**Consequences:** `make dashboard`'s new Lineage section (Decision 4) is
only as fresh as the last time it was generated, identical to every other
dashboard section (ADR-0009 Decision "regenerate on demand" trade-off) —
no new staleness risk beyond what already exists.

### 3. Artifact roots are configured once, in advance, even for artifact types that don't exist yet

**Decision:**

```yaml
quality_engineering:
  lineage:
    artifact_roots:
      dataset: "data/processed/datasets"        # exists today (Phase 3a)
      model: "outputs/training"                  # Phase 4 — directory already named in config/default.yaml's training.output_dir
      evaluation: "outputs/evaluation"            # Phase 5 — new path, reserved now
      deployment: "outputs/deployment"             # Phase 6 — new path, reserved now
```

`build_lineage_graph()` silently skips any root that doesn't exist yet
(e.g. `outputs/evaluation` before Phase 5 ships) rather than erroring.

**Rationale:**
- `model`'s root is not a new path invention — it is the exact value
  already at `training.output_dir` in `config/default.yaml`, cross-
  referenced rather than duplicated as a second config key with the same
  value (avoids the two-config-keys-drifting-apart problem on sight).
  `evaluation` and `deployment` are genuinely new paths reserved now so
  Phase 5/6 do not need to touch `quality_engineering.lineage` at all —
  only start writing `version.json` files into directories this phase
  already declared.
- Skipping missing roots (rather than requiring all four to exist) is
  what lets `make dashboard`'s Lineage section render correctly today,
  with just dataset nodes, exactly as it will after Phase 4 adds model
  nodes — no "not implemented yet" special case needed in `lineage.py`
  itself.

**Consequences:** A typo'd root path is indistinguishable from "this phase
hasn't started yet" (both silently produce zero nodes for that type) —
acceptable, since `docs/QUALITY_SYSTEM.md` documents the expected roots
and a reviewer inspecting the dashboard's Lineage section for a type with
zero nodes has an obvious, single thing to check.

### 4. `lineage.py` exposes ancestor and descendant traversal; the dashboard gets a Lineage section built entirely from it

**Decision:**

```python
@dataclass
class LineageNode:
    artifact_type: str
    artifact_id: str
    artifact_dir: Path
    version: VersionRecord

@dataclass
class LineageGraph:
    nodes: dict[str, LineageNode]     # keyed "{artifact_type}:{artifact_id}"
    edges: list[tuple[str, str, str]]  # (child_key, parent_key, relation)

def build_lineage_graph(cfg: QualityEngineeringConfig) -> LineageGraph: ...
def trace_ancestors(graph: LineageGraph, artifact_type: str, artifact_id: str) -> list[LineageNode]: ...
def trace_descendants(graph: LineageGraph, artifact_type: str, artifact_id: str) -> list[LineageNode]: ...
```

A new dashboard section, `LineageSection`, is registered by `lineage.py`
itself into `SECTION_REGISTRY` under category `"dataset"` (ADR-0009
Decision 2/6's `CategoryRegistry`-backed section registry — this ADR is
itself the first concrete proof of that mechanism being used by a module
other than `dashboard.py`, one phase ahead of Phase 4 actually needing
it), and renders `trace_ancestors` / `trace_descendants` for the artifact
the dashboard was generated for.

**Rationale:** Mirrors every other Phase 3.5 module's composition
discipline (ADR-0004 Decision 2) — `lineage.py` computes the graph and
traversals; the dashboard section only formats them. Exposing both
directions (ancestors and descendants) matters because the useful
question changes depending on which artifact a reviewer is looking at:
starting from a dataset, "what got trained on this" is a descendants
query; starting from a deployment package, "what dataset ultimately
produced this" is an ancestors query.

**Consequences:** Today, with only dataset artifacts existing,
`trace_descendants()` always returns an empty list (nothing has trained on
anything yet) and `trace_ancestors()` returns, at most, the
`previous_artifact_id` chain of earlier dataset versions re-expressed as
lineage nodes for display consistency — both are correct, non-error,
literally-empty-because-true results, not special-cased away.

### 5. A lineage-aware gate check formalizes what ADR-0010 §2 described informally

**Decision:** `gates.py` gains a lineage-aware check available to any
future consumer that needs it: given a candidate artifact and an expected
parent (e.g. "the checkpoint about to be evaluated must have trained on
*this* dataset version"), the check walks `lineage_parents` and passes
only if the expected parent's `(artifact_type, artifact_id)` is present.

**Rationale:** ADR-0010 §2 already anticipated this exact need
("checkpoint's recorded `dataset_id` and `config_hash` match the dataset
about to be evaluated") but described it as an ad hoc field comparison.
Formalizing it as a lineage-edge check means the same check generalizes
immediately to deeper chains Phase 4 didn't originally ask about — e.g. a
Phase 6 deployment-readiness gate can check "does this package's lineage
trace back to a dataset that passed its training gate," which a flat
field comparison on `VersionRecord` alone could not express without
walking multiple hops by hand.

**Consequences:** This check is not registered into `gates.py`'s active
check list in this phase (there is nothing to check yet — no models
exist) — it is implemented and unit-tested against synthetic lineage
fixtures now, so Phase 4 activates it by registering it, rather than
writing it from scratch.

---

## Implementation Note (accepted without a further architecture revision)

`lineage.py` stays under `src/quality/` for Phase 3.5. It is arguably
infrastructure rather than a "quality" concern — `training/` and
`evaluation/` (Phase 4/5) will want to read and write lineage without
depending on the rest of the quality package (scoring, coverage, review).
No restructuring is done now, on the "don't build ahead of validated
need" principle already applied throughout this design (ADR-0008's
rejected N-dimensional coverage matrix, ADR-0010 §5's undefined
`model_metrics.py`): the module's internal API (`build_lineage_graph`,
`trace_ancestors`, `trace_descendants`, `evaluate_lineage_check`) already
takes only `Artifact`/`VersionRecord` and `QualityEngineeringConfig` — no
scoring, coverage, or review types — so it has no actual coupling to the
rest of `src/quality/` today, only a package-path one. If Phase 4 finds
`training/` needs to depend on it directly, move `lineage.py` (and
`artifacts.py`, `registry.py`, `versioning.py`, `schemas.py`'s `Artifact`
base, which have the same property) into a shared package at that point —
a file move plus import-path update, not a rewrite, since none of these
four modules import anything scoring/coverage/review-specific.

## Non-Goals (explicit, matching the brief's Non-Goals section)

This ADR does **not** introduce: a training-run tracking database, an
experiment comparison UI, hyperparameter sweep tracking, or any MLflow/
W&B-style service. Those remain out of scope for the same reason
Behavioural Cloning itself is out of scope for this phase — they are
Phase 4+ concerns that this ADR only makes sure Phase 4+ won't have to
redesign the provenance graph to get.
