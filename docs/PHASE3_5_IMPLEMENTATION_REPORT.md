# Phase 3.5 Implementation Report — Final Self-Review

**Status: ✅ Complete.** This report is the mandatory final self-review
called for by the original Phase 3.5 brief: architecture implemented,
ADR decisions, files added/modified, test results, known limitations,
and recommendations for Phase 4.

---

## 1. What was built

Phase 3.5 adds a complete engineering loop over Phase 3's dataset
builder: quality scoring, dataset versioning, regression detection,
coverage planning, a human-facing review, training-readiness gates, a
self-contained HTML dashboard, six new CLIs, and a metrics/config
framework tying all of it together. No Behavioural Cloning, PyTorch,
training loop, reinforcement learning, diffusion, VLA, or model
evaluation code was added — those remain Phase 4+, as mandated by the
original project brief's Non-Goals. See
[`docs/ENGINEERING_LOOPS.md`](ENGINEERING_LOOPS.md) for the end-to-end
walkthrough.

## 2. Process followed

1. Read the repository completely (config, `src/data/`, existing docs,
   ADRs, prior phase reports) before writing any code.
2. Drafted ADR-0004 through ADR-0011 (eight ADRs) plus a critical
   architecture review (`docs/PHASE3_5_DESIGN_REVIEW.md`) before
   implementation.
3. Revised the architecture once in response to review feedback
   (`docs/PHASE3_5_ARCHITECTURE_REVISION.md`): generalized `Artifact` to
   a type-agnostic envelope with a `DatasetArtifact` subtype, introduced
   the `CategoryRegistry` pattern shared by metric and dashboard-section
   registration, and added ADR-0011 (lineage).
4. Checkpointed with the product owner before implementation began.
5. The architecture was accepted with **exactly one** implementation
   note (`lineage.py`'s possible future relocation — see
   [`docs/ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md)) and an
   explicit instruction **not** to implement three further review
   suggestions (an event system, a `Pipeline` abstraction, typed enums)
   and **not** to restructure `src/quality/` into `src/core/`. Both
   constraints were honored throughout implementation — verified by
   `grep`-checking for `events.py`/`pipeline.py` (absent) and confirming
   every category value remains a plain string (`artifact_type="dataset"`,
   `category="dataset"`, severities as strings) rather than an enum.
6. Implemented ten deliverables in dependency order: schemas → config →
   registry → artifacts → metrics/scoring/coverage → regression/versioning
   → lineage → review/gates → dashboard → CLIs/Makefile/config wiring.
7. Wrote the test suite, closing coverage gaps iteratively against
   `--cov-report=term-missing` until every module exceeded the 95% bar.
8. Wrote documentation (this being the final piece).
9. Ran the full validation suite one more time and, in the process,
   found and fixed one real bug (§5 below) before calling the phase done.

## 3. Files added

**`src/quality/`** (16 files, ~3,000 lines): `__init__.py`, `schemas.py`,
`config.py`, `registry.py`, `artifacts.py`, `metrics.py`,
`dataset_metrics.py`, `scoring.py`, `coverage.py`, `regression.py`,
`versioning.py`, `lineage.py`, `review.py`, `gates.py`, `dashboard.py`.

**`scripts/`** (8 new files): `_format.py` (shared console formatting +
dataset/baseline resolution), `dataset_version.py`, `dataset_quality.py`,
`dataset_review.py`, `recommend_data.py`, `compare_datasets.py`,
`dataset_dashboard.py`, `__init__.py` (empty — resolves a mypy
module-identity ambiguity).

**`tests/unit/test_quality_engineering.py`** — 121 tests across 14 test
classes, one per `src/quality/` module.

**Documentation** (13 new files): eight ADRs (`docs/ADR/0004` through
`0011`), `docs/ENGINEERING_LOOPS.md`, `docs/QUALITY_SYSTEM.md`,
`docs/DATASET_VERSIONING.md`, `docs/REGRESSION_DETECTION.md`,
`docs/ARCHITECTURE_DECISIONS.md`, plus the two pre-implementation review
documents (`docs/PHASE3_5_DESIGN_REVIEW.md`,
`docs/PHASE3_5_ARCHITECTURE_REVISION.md`) and this report.

## 4. Files modified

- `config/default.yaml` — new `quality_engineering:` section (verified
  via round-trip: `load_quality_config(load_config())` exactly equals
  `QualityEngineeringConfig()`'s dataclass defaults).
- `Makefile` — seven new targets (`quality`, `review`, `compare-data`,
  `dashboard`, `recommend-data`, `version`, `quality-loop-dry-run`).
- `README.md`, `docs/PHASES.md`, `.agents/AGENTS.md` — Phase 3.5 sections
  and phase-awareness updates.
- `scripts/build_dataset.py`, `scripts/inspect_dataset.py` — refactored
  to import shared `ok()`/`warn()`/`fail()` from the new `_format.py`
  instead of each defining its own private copies.
- `src/data/dataset_builder.py`, `dataset_schemas.py`,
  `dataset_statistics.py`, `tests/unit/test_dataset_engineering.py` — pre-
  existing modifications from Phase 3b/3.5 schema work (weather field,
  `duplicate_sample_count`), unrelated to this report's own changes and
  already covered by Phase 3b's own test suite.

## 5. A real bug found and fixed during final validation

Manual end-to-end validation (running all six new CLIs against a freshly
built dataset, rather than trusting unit tests alone) surfaced a genuine
crash: `versioning._resolve_previous_artifact_id()` picks the most
recently modified sibling dataset directory as "previous," then loads it
via `load_dataset_artifacts()`. Two dataset directories left over from
earlier Phase 3 development (`data/processed/datasets/dataset_20260708_031851`,
`dry_run_dataset`) were built before Phase 3b added three fields to
`DatasetManifest` (`outlier_detection_enabled`, `outlier_thresholds`,
`duplicate_detection_enabled`). Loading either one raises `TypeError` —
not `ArtifactNotFoundError`, the only exception `_resolve_previous_artifact_id`
caught — crashing `make version` outright.

The same catch-only-`ArtifactNotFoundError` pattern existed in three more
places: `versioning.generate_changelog()`, `dashboard.generate_dashboard()`'s
baseline resolution and quality-trend scan, and
`scripts/_format.py::resolve_baseline_artifact()` — every "best-effort,
look at another dataset directory" code path in the phase.

**Fix:** added `ARTIFACT_LOAD_ERRORS = (ArtifactNotFoundError, TypeError,
KeyError, ValueError)` to `src/quality/artifacts.py` — one named,
documented tuple, imported by all four call sites, replacing their narrow
`except ArtifactNotFoundError`. Each affected try block was already
scoped to a single `load_dataset_artifacts()`/`load_version_record()`
call, so broadening the caught exception types could not mask an
unrelated bug in surrounding logic. Deliberately did **not** broaden the
try/except around the *primary* dataset a CLI is pointed at
(`load_dataset_artifacts(dataset_dir)` in every script, unguarded) — a
genuinely broken primary target should still fail loudly and specifically,
only best-effort scans of *other* directories degrade gracefully.

Two regression tests were added
(`test_resolve_previous_skips_sibling_with_incompatible_manifest_schema`,
`test_generate_changelog_previous_has_incompatible_manifest_schema`,
`test_generate_dashboard_baseline_has_incompatible_manifest_schema`) that
build a dataset with a manifest missing Phase 3b fields and assert the
affected functions degrade gracefully instead of raising. All six CLIs
were then re-run end to end against the same two legacy directories that
originally triggered the crash, confirmed clean.

## 6. Test results

```
$ ruff check src/ scripts/ tests/
All checks passed!

$ mypy src/ scripts/
Success: no issues found in 61 source files

$ pytest tests/unit/ -m "not integration" -q
328 passed in 2.53s

$ pytest tests/unit/test_quality_engineering.py --cov=src.quality --cov-report=term-missing
121 passed — 99% coverage on src/quality/ (target: ≥95%)
```

Per-module coverage on `src/quality/`:

| Module | Coverage | Uncovered |
|--------|----------|-----------|
| `__init__.py`, `artifacts.py`, `config.py`, `dashboard.py`, `dataset_metrics.py`, `gates.py`, `lineage.py`, `registry.py`, `review.py` | 100% | — |
| `coverage.py` | 98% | one defensive line |
| `regression.py` | 99% | one defensive line |
| `schemas.py` | 98% | three trivial `to_dict()`/boilerplate lines |
| `scoring.py` | 97% | one defensive line |
| `versioning.py` | 96% | three defensive mtime-resolution edge-case lines |

The uncovered lines remaining are single-line defensive branches (e.g.
"this dict key is somehow absent") or trivial boilerplate, not untested
logic paths — a deliberate stopping point once 99% overall already
exceeded the 95% bar and further tests would have tested Python's own
`except`/`if` mechanics rather than this codebase's behavior.

**Environment note:** this validation ran against the sandbox's system
Python 3.10 (`ruff`, `mypy`, `pytest`, `click`, `structlog`, `numpy` all
present) because the repository's own `.venv/` was built by `uv` on the
user's macOS machine and its `python3` symlink targets a macOS-only path
that does not exist inside this Linux sandbox. The commands run are
byte-for-byte what `make lint` / `make type-check` / `make test` invoke
(`ruff check src/ scripts/ tests/`, `mypy src/ scripts/`, `pytest
tests/unit/ -m "not integration"`) — only the interpreter binary differs.
On the user's own machine, `make lint`/`make type-check`/`make test` will
run these same commands against the project's real `.venv` directly.

## 7. Known limitations

- **`lineage.py` has nothing to trace yet.** No earlier phase produces a
  dataset from another artifact, so `lineage_parents` is always empty in
  practice today; the graph-walking functions are exercised in tests via
  synthetic edges. This is expected to become load-bearing once Phase 4
  records "trained from dataset X."
- **`previous_artifact_id` resolution is mtime-based, not build-order-based**
  (ADR-0006 Decision 4) — an accepted, documented edge case (see
  `docs/DATASET_VERSIONING.md`), not something this phase attempted to
  make more sophisticated (e.g. via a persisted build-order ledger), since
  the ambiguity only arises when versioning is run out of build order.
- **Duplicate detection inherited from Phase 3b remains exact
  (byte-hash) only** — unchanged by this phase, carried forward as a
  known limitation from `docs/PHASE3B_DATASET_HARDENING.md`.
- **No CI wiring for the new gate/regression exit codes.** `make quality`
  and `make compare-data` exit 1 on failure, ready to be dropped into
  `.github/workflows/ci.yml` as a blocking step, but that wiring was not
  requested and was not added.

## 8. Recommendations for Phase 4

1. Follow `versioning.py`'s documented pattern (ADR-0010 Section 2) to add
   a `model` artifact type: a `ModelArtifact(Artifact)` subclass, a
   `load_model_artifacts()` loader, and `Metric` subclasses registered
   under `METRIC_REGISTRY.register("model", ...)` — no existing
   `src/quality/` code needs to change for this.
2. Have `train.py` record `lineage_parents=[LineageEdge("dataset",
   dataset_id, "trained_from")]` when writing a model's `VersionRecord`,
   so `lineage.trace_ancestors()` becomes meaningful for the first time.
3. Revisit `lineage.py`'s location (the one accepted implementation note
   from the architecture review) once a second artifact type's lineage
   needs are concrete, rather than speculatively moving it now.
4. Consider wiring `gate_report.json`'s pass/fail verdict as a
   precondition check at the top of `train.py`, so a dataset that fails
   Phase 3.5's training-readiness gate cannot silently be trained on.
5. The three deliberately-deferred improvements from the architecture
   review (event system, `Pipeline` abstraction, typed enums — see
   `docs/ARCHITECTURE_DECISIONS.md`) remain available to reconsider if
   Phase 4 introduces a second pipeline shape or a second event consumer
   that would concretely benefit from them.
