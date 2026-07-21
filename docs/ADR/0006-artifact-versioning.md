# ADR 0006 — Artifact Versioning

**Date:** 2026-07-20 (revised same day — see Revision Note)
**Status:** Accepted (revised)
**Deciders:** AI Engineering Team

> **Revision note:** This ADR was originally titled "Dataset Versioning"
> and scoped `VersionRecord` to datasets only (`dataset_id`,
> `previous_dataset_id`, three fixed hash fields, two fixed count fields).
> A second design pass generalized it to **artifact versioning**, covering
> any engineering artifact this loop tracks — dataset today; model
> checkpoint, evaluation run, and deployment package from Phase 4 onward —
> because every one of those needs the identical release-record shape
> (identity, hashes, counts, lineage), and building it dataset-only now
> would have meant redesigning this exact ADR the day Phase 4 needed a
> checkpoint version record. The renamed fields below
> (`dataset_id`→`artifact_id`, `previous_dataset_id`→
> `previous_artifact_id`) and the two generalized container fields
> (`content_hashes`, `summary_counts`, replacing three/two fixed fields)
> are the only structural changes; every decision's *rationale* from the
> original version still holds and is preserved below, updated to
> artifact-neutral language. No dataset has a `version.json` yet (this
> ADR predates implementation), so this revision has zero migration cost.

---

## Context

ADR-0002 Decision 8 already solved "don't clobber the previous build" for
datasets specifically — every `build_dataset()` call writes to its own
`datasets_dir/<dataset_id>/` directory. What was still missing — for
datasets, and by the same argument for every other artifact this
engineering loop will eventually produce — is treating an artifact the
way software treats a release: a content-addressed identity that proves
exactly what produced it, a human-readable record of what changed since
the last one of *its own kind*, and a pointer to what it was *derived
from* (a different concern — see ADR-0011). This ADR does **not** replace
`dataset_id` (or any future artifact's own natural identifier) — it adds a
release record on top of whatever identity scheme the artifact's own
producer (`dataset_builder.py` today; a future `trainer.py`,
`evaluation harness`, or `export.py` later) already assigns.

---

## Decisions

### 1. `artifact_id` (the artifact's own identity) is unchanged; a new `version.json` adds the release record, generically shaped for any artifact type

**Decision:** `src/quality/versioning.py::compute_version_record()`
writes `version.json` into the artifact's own directory — for datasets,
the same `datasets_dir/<dataset_id>/` directory `build_dataset()` already
creates — containing:

```python
@dataclass
class VersionRecord:
    schema_version: str
    artifact_type: str                    # "dataset" today; "model" | "evaluation" | "deployment" later
    artifact_id: str                      # unchanged identity from the artifact's own manifest (dataset_id today)
    created_at: str
    git_commit: str | None
    config_hash: str                      # sha256 of the resolved config section(s) that are actual inputs to this artifact's build
    content_hashes: dict[str, str]        # named hash per artifact-defining file, e.g. {"manifest": ..., "statistics": ..., "quality_report": ...} for a dataset
    generator_version: str                # __version__ of src.quality, independent of project.version
    summary_counts: dict[str, int]        # named counts, e.g. {"sample_count": ..., "episode_count": ...} for a dataset
    previous_artifact_id: str | None      # prior version of THIS SAME artifact (lineage within one type — see ADR-0011 Decision 1)
    lineage_parents: list["LineageEdge"]  # artifacts this one was DERIVED FROM, possibly a different type (ADR-0011)
```

For a dataset specifically, `content_hashes` and `summary_counts` are
populated exactly as the original (dataset-only) design specified:
`content_hashes = {"manifest": manifest_hash, "statistics":
statistics_hash, "quality_report": quality_report_hash}`,
`summary_counts = {"sample_count": ..., "episode_count": ...}`.

**Rationale:**
- The brief's requested fields ("git commit, config hash, statistics
  hash, manifest hash, creation timestamp, generator version, sample
  count, episode count") map onto fields that either already exist
  (`DatasetManifest.git_commit`, `.created_at`, `.sample_count`,
  `.episode_count_included`) or are cheap hashes of files
  `dataset_builder.py` already writes — nothing here requires re-deriving
  data the builder does not already have. Generalizing the three fixed
  hash fields and two fixed count fields into named dicts
  (`content_hashes`, `summary_counts`) preserves every one of those
  concrete dataset fields (by name, inside the dict) while letting a
  future model artifact record `content_hashes = {"checkpoint": ...,
  "training_config": ...}` and `summary_counts = {"epoch_count": ...,
  "parameter_count": ...}` without a schema migration — the shape of
  "named hash" and "named count" is what's actually common across
  artifact types, not the specific names `manifest_hash`/`sample_count`.
- Not touching `dataset_builder.py`'s own manifest keeps Phase 3a/3b's
  existing hash-free manifest stable for any consumer that only knows
  about `dataset_manifest.json` (the one-way dependency from ADR-0004
  Decision 1 still holds: Phase 3.5 depends on Phase 3, not the reverse).
- `generator_version` is versioned independently of `project.version` in
  `config/default.yaml` (which tracks the whole repository) because the
  quality/versioning logic can change release cadence independently of
  whatever produced the artifact it describes.

**Consequences:** An artifact built before this ADR (or before a given
artifact type's Phase adopted it) has no `version.json`.
`dataset_dashboard.py`, `compare_datasets.py`, and `lineage.py` (ADR-0011)
all treat a missing `version.json` as "unversioned" rather than erroring —
this phase never requires rebuilding historical artifacts to stay usable.

### 2. Hashes are computed over canonicalized file content, not file bytes, using the same canonicalization `compute_route_hash()` already established — but the full digest, not an 8-char prefix

**Decision:** Each entry in `content_hashes` (and `config_hash`) is
`hashlib.sha256(json.dumps(parsed_content, sort_keys=True,
default=str).encode()).hexdigest()` — the **full 64-character** hex
digest. `src/quality/artifacts.py` exposes this as `hash_content(obj) ->
str`, following the identical canonicalize-then-hash approach
`compute_route_hash()` (`src/data/episode.py`) already established for
route hashing, reused rather than reimplemented.

**Rationale:**
- JSON files in this repo are written with `json.dumps(..., indent=2,
  default=str)` (see `dataset_builder.py`). Re-serializing with different
  `indent`/key order (e.g. a future formatting change, or copying the
  file through a tool that reformats it) would change a byte hash without
  changing the *content* — a false positive for "this artifact changed."
  Canonicalizing before hashing (sorted keys, no indentation) makes the
  hash a function of content only — exactly why `compute_route_hash()`
  canonicalizes before hashing in the first place.
- `compute_route_hash()` truncates to 8 hex characters, which is
  appropriate for a short human-readable route identifier embedded in
  filenames, but these version hashes exist specifically to let a
  reviewer or a future automated gate assert *exact* reproducibility
  (ADR-0004 Decision 7) for any artifact type — an 8-character prefix
  trades collision resistance for brevity for no benefit here
  (`version.json` is not a filename or a display label). `hash_content()`
  is therefore a new, tiny function (full digest, no truncation) rather
  than a call to `compute_route_hash()` itself, but it reuses that
  function's canonicalization strategy exactly, and lives in
  `artifacts.py` — the single module that already owns reading/parsing
  every artifact (ADR-0004 Decision 2) — rather than a separate
  `hashing.py`.

**Consequences:** A `content_hashes["statistics"]` entry changes if and
only if the actual statistics change — copying `stats.json` between
machines with different line endings, or re-indenting it, never produces
a spurious diff. The same guarantee applies to whatever files a future
artifact type hashes, since the mechanism is content-generic.

### 3. `config_hash` covers only the configuration that provably affects the artifact's content

**Decision:** For a dataset, `config_hash` is computed over the resolved
`dataset_engineering:` and `quality_engineering:` sections only — not the
entire `config/default.yaml`. The same principle applies generically:
each artifact type's producer declares which config section(s) are
actual build inputs, and only those are hashed.

**Rationale:**
- Hashing the whole file would make `config_hash` change every time an
  unrelated section is edited (e.g. `evaluation.num_episodes` while
  building a dataset), producing a reproducibility signal that is
  technically "different" but practically meaningless for this artifact.
  A hash that changes for reasons unrelated to what it is supposed to
  attest to is worse than no hash — it trains reviewers to ignore it.
- Scoping the hash to real inputs keeps "reproducible" meaning what it
  says: same `config_hash` implies same build behavior, full stop — for
  any artifact type, not just datasets.

**Consequences:** Adding a new `quality_engineering:` sub-key later
changes `config_hash` for every subsequent dataset build (expected — it
is a real input now) but does not retroactively invalidate the hash
already recorded in old `version.json` files (they simply reflect the
config shape at the time, which is the entire point of recording it).

### 4. `previous_artifact_id` is a single, same-type pointer, not a full DAG — cross-type derivation is a separate concern (ADR-0011)

**Decision:** `compute_version_record()` takes an optional
`previous_artifact_id` (defaulting to "the most recently created artifact
of the *same type* in the same artifact root at the time of this build,
excluding this one" — the same "most recent" convention
`inspect_dataset.py` already uses for datasets). This pointer only ever
connects an artifact to the prior version of itself.

**Rationale:**
- Every dataset in this repository is built from the same growing pool of
  raw episodes — there is no branching/merging concept for *dataset*
  versions the way there is for git commits. A linear
  `previous_artifact_id` pointer is sufficient to answer "what changed
  since last time" (ADR-0007) and reconstruct the same-type history by
  walking backward, without the complexity of a general DAG a single
  artifact type does not need for its own version history.
- Cross-type derivation (which dataset a checkpoint trained on, which
  checkpoint an evaluation run used) genuinely *is* a DAG — but that is a
  different question from "what's the previous version of this artifact,"
  and is deliberately kept as a separate field (`lineage_parents`) and a
  separate ADR (0011), rather than overloading `previous_artifact_id`
  with two meanings depending on which artifact type is asking.
- Matches the existing "most recent" convention already established for
  `EPISODE_DIR` (Makefile) and `inspect_dataset.py`'s default dataset
  resolution — one more place uses the same idea instead of inventing a
  second "which one is latest" rule.

**Consequences:** A reviewer who manually re-runs an old dataset build out
of chronological order gets a `previous_artifact_id` that reflects
wall-clock order, not "logical" order — documented as an edge case in
`docs/DATASET_VERSIONING.md`; `--baseline` on `compare_datasets.py`
(ADR-0007) always allows an explicit override for this reason.

### 5. `CHANGELOG.md` is generated per artifact directory, not maintained by hand

**Decision:** `versioning.py::generate_changelog()` writes
`<artifact_dir>/CHANGELOG.md`, built entirely from the `RegressionReport`
(ADR-0007) comparing this artifact to `previous_artifact_id`, with four
fixed sections: **Added**, **Removed**, **Changed**, **Improved /
Regressions**. For a dataset, these sections cover towns/weather/routes
present or absent, episode/sample/split-count deltas, and quality-score
sub-metric deltas — exactly as originally specified; the mechanism itself
(diff against `previous_artifact_id` via `regression.py`) is unchanged by
this revision and generalizes to any artifact type `regression.py`'s
comparator (ADR-0007) can diff.

**Rationale:**
- A hand-maintained changelog drifts from reality (the classic failure
  mode of hand-written changelogs) and cannot be produced retroactively
  across artifacts that already exist. A generated changelog is always in
  sync with the artifacts it describes, by construction.
- Reusing `regression.py`'s comparison (rather than `versioning.py` doing
  its own diffing) keeps exactly one place that knows how to compare two
  artifacts of the same type — the same principle as ADR-0004 Decision
  2's "composition-only" modules.

**Consequences:** The very first artifact of a given type (no
`previous_artifact_id`) gets a `CHANGELOG.md` with a single "Initial
version — no prior version to compare" line instead of the four sections
— handled explicitly in `generate_changelog()`, not left to error.

### 6. `make version` is the CLI surface for datasets today; the same underlying function serves any artifact type later

**Decision:** `scripts/dataset_version.py` (`make version`) computes (or
recomputes) `version.json` and `CHANGELOG.md` for a given *dataset*
directory (default: most recent) — it is a dataset-specific thin CLI, per
ADR-0004 Decision 4. The function it calls,
`versioning.py::write_version_artifacts(artifact_dir, artifact_type, cfg,
...)`, is artifact-type-generic; a future `scripts/model_version.py`
(Phase 4) would call the identical function with `artifact_type="model"`,
introducing zero new versioning logic.

**Rationale:** Versioning is deliberately decoupled from
`build_dataset()` itself (or any future artifact producer) — a build can
happen without CARLA, on a laptop, in CI; version recording (which
involves hashing potentially large index files) is kept as an explicit,
separate, cheap-to-rerun step so a reviewer can regenerate
`version.json`/`CHANGELOG.md` after, say, a `quality_engineering.scoring`
config change, without rebuilding the entire artifact.

**Consequences:** `version.json`'s `config_hash` can therefore be
recomputed independently of when the artifact itself was built —
`created_at` on `VersionRecord` reflects when versioning was *run*, which
may be later than the artifact's own build timestamp; both are recorded
so this is never ambiguous, for any artifact type.
