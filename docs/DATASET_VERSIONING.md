# Dataset Versioning and Lineage

Covers `src/quality/versioning.py`, `src/quality/lineage.py`, and the
`Artifact`/`DatasetArtifact`/`VersionRecord` schemas in
`src/quality/schemas.py`. See ADR-0006 and ADR-0011 for the design
rationale.

## The artifact envelope

`Artifact` (`schemas.py`) is a generic envelope every future artifact type
(dataset today, model/evaluation/deployment in later phases) shares:
`artifact_type`, `artifact_id`, `created_at`, `git_commit`. `DatasetArtifact`
is a proper `@dataclass` subclass — not a hand-written `__init__` — adding
`manifest`, `stats`, `quality_report`, and optionally `samples`, so that
`dataclasses.fields()` (and therefore `to_dict()`) sees every field
including the base ones.

`src/quality/artifacts.py` provides the loaders:

- `load_dataset_artifacts(dataset_dir, *, load_samples=False)` — the
  primary entry point every CLI and module uses.
- `load_artifact_envelope(artifact_dir)` — the generic (type-agnostic)
  envelope only, for code that only needs `artifact_type`/`artifact_id`.
- `load_version_record(artifact_dir)` — the *full* `VersionRecord`
  including `lineage_parents`, distinct from the envelope above.
- `load_quality_score_record(dataset_dir)` — reads back a previously
  written `quality_score.json`.

All raise `ArtifactNotFoundError` (a `FileNotFoundError` subclass) when
the expected file is missing, which every caller in `src/quality/` catches
explicitly rather than letting propagate — see, e.g.,
`dashboard.generate_dashboard()`'s baseline-resolution `try`/`except`.

## VersionRecord

Computed by `versioning.compute_version_record(dataset_dir, cfg, *,
previous_artifact_id=None, lineage_parents=None) -> VersionRecord`:

| Field | Meaning |
|-------|---------|
| `artifact_type` / `artifact_id` | Identity — `"dataset"` and the dataset's own ID today |
| `created_at` / `git_commit` | Provenance |
| `config_hash` | Hash of the resolved `dataset_engineering:` + `quality_engineering:` config sections **only** — never the whole file, so unrelated config changes (e.g. `carla_connection:`) don't spuriously bump it (ADR-0006 Decision 3) |
| `content_hashes` | Per-file hashes (`manifest`, `statistics`, `quality_report`) so a byte-for-byte-identical rebuild is detectable even if `artifact_id` differs |
| `generator_version` | `src.quality.__version__` — independent of `project.version` in `config/default.yaml` (ADR-0006 Decision 1) |
| `summary_counts` | `sample_count`, `episode_count` at time of versioning |
| `previous_artifact_id` | Same-type version history — see below |
| `lineage_parents` | Cross-artifact-type derivation edges — see [Lineage](#lineage-cross-artifact-type-derivation) below |

`write_version_artifacts(dataset_dir, cfg) -> VersionRecord` computes the
record, writes it to `<dataset_dir>/<versioning.version_filename>`
(default `version.json`), generates and writes the changelog to
`<versioning.changelog_filename>` (default `CHANGELOG.md`), and returns
the record. Safe to re-run — it only ever overwrites these two files
(ADR-0006 Decision 6).

## `previous_artifact_id` resolution — an intentional edge case

When `previous_artifact_id` isn't passed explicitly,
`_resolve_previous_artifact_id()` picks **the most recently *modified*
other dataset directory** under the same parent — by filesystem mtime,
not by any notion of "the build that logically came before this one."

This means: if you build dataset A, then dataset B, then go back and
version A *after* B already exists, A's `previous_artifact_id` resolves to
B — the most recently touched sibling — not to "nothing, since A predates
B." This is documented and accepted (ADR-0006 Decision 4), not a bug:
mtime is cheap, requires no extra bookkeeping file, and the ambiguity only
arises when versioning is run out of build order, which is not the normal
workflow. `tests/unit/test_quality_engineering.py` has a dedicated test,
`test_compute_version_record_previous_reflects_wall_clock_not_build_order`,
that pins this exact behavior so it can't regress silently.

If a caller needs a specific, non-ambiguous baseline, pass
`previous_artifact_id` explicitly to `compute_version_record()`, or pass
`--baseline` to `scripts/compare_datasets.py`.

## Changelog generation

`generate_changelog(dataset_dir, version, quality_cfg) -> str` renders
Markdown with five sections — Added, Removed, Changed, Improved,
Regressions — by running `regression.compare_datasets()` against
`version.previous_artifact_id` and bucketing findings by
`RegressionFinding.severity` and whether the dimension is categorical
(`town:`/`weather:`/`route:` prefixed). If `previous_artifact_id` is
`None`, or the previous dataset's artifacts can no longer be found on
disk, a single-line placeholder is written instead of attempting a
comparison.

## Lineage — cross-artifact-type derivation

`lineage.py` answers a different question than `previous_artifact_id`:
not "what version of *this same type* came before," but "what artifact of
a *different type* was this one derived from." A dataset today has no
parents (nothing produces datasets from other artifacts yet), but the
graph exists so Phase 4's models can record "trained from dataset X" via
`VersionRecord.lineage_parents: list[LineageEdge]` without inventing a new
mechanism.

- `LineageNode` — one artifact instance: `artifact_type`, `artifact_id`,
  `artifact_dir`, its `VersionRecord`.
- `LineageGraph` — `nodes: dict[str, LineageNode]` keyed
  `"{artifact_type}:{artifact_id}"`, plus `edges: list[tuple[child_key,
  parent_key, relation]]`.
- `build_lineage_graph(cfg)` — scans every directory under each
  `cfg.lineage.artifact_roots[type]` that has a `version.json`, building
  nodes and edges from each one's `lineage_parents`.
- `trace_ancestors(graph, artifact_type, artifact_id)` — walks **both**
  `lineage_parents` edges (cross-type) **and** the `previous_artifact_id`
  chain (same-type version history), so "how did this artifact come to
  be" includes its own version history.
- `trace_descendants(graph, artifact_type, artifact_id)` — walks **only**
  `lineage_parents` edges forward, deliberately never reversing
  `previous_artifact_id` — "what was derived from this" should not
  include "what later version superseded this."
- `evaluate_lineage_check(version, *, expected_parent_type,
  expected_parent_id, ...)` — a `GateCheckResult`-shaped assertion Phase 4
  can use to verify a model was trained from the dataset it claims to be
  ("did lineage actually record what I expect"), not currently wired into
  `DATASET_GATE_CHECKS` since datasets have no parents to check.

### Dashboard registration (circular-import note)

`lineage.py` contributes a seventh dashboard section ("Lineage", order
70) into `dashboard.SECTION_REGISTRY`, which `dashboard.py` itself owns.
Since `dashboard.py` needs `lineage.py` for this and `lineage.py`'s
render function needs `dashboard.py`'s `DashboardContext` type, the two
modules resolve the cycle as follows: `lineage.register_lineage_section()`
imports `SECTION_REGISTRY`/`DashboardSection` from `dashboard` **inside
the function body** (not at module load time), and only imports
`DashboardContext` under `TYPE_CHECKING` for annotations. `dashboard.py`
imports and calls `register_lineage_section()` at the very bottom of its
own file, after its own six sections are already registered. The
registration is idempotent — calling it twice is a no-op, checked
directly in `tests/unit/test_quality_engineering.py`.

**Implementation note carried into Phase 4:** `lineage.py` currently
lives under `src/quality/` alongside the dataset-specific modules, even
though its graph is artifact-type-generic. If Phase 4 adds a second
artifact type with its own lineage needs, moving `lineage.py` (and only
`lineage.py`) to a shared-infrastructure location outside `src/quality/`
is worth reconsidering at that point — noted in ADR-0011 as the one
accepted follow-up from the architecture review, not undertaken in this
phase.
