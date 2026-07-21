# Phase 3.5 — Engineering Loop

**Status: ✅ Complete**

Phase 3.5 turns Phase 3's dataset builder from a one-shot pipeline into a
system that can be operated over time: score every build, compare it to
what came before, catch regressions before they reach training, plan what
data to collect next, and give a human a single page to review. It never
trains, evaluates, or touches a model — see [Non-goals](#non-goals) below.

This document is the entry point. For the internals of a specific area,
see:

- [`docs/QUALITY_SYSTEM.md`](QUALITY_SYSTEM.md) — metrics, scoring, review, gates
- [`docs/DATASET_VERSIONING.md`](DATASET_VERSIONING.md) — identity, hashing, changelog, lineage
- [`docs/REGRESSION_DETECTION.md`](REGRESSION_DETECTION.md) — build-over-build comparison
- [`docs/ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md) — the ADR index and what was deliberately left out

---

## The loop

```
Phase 3 dataset build (data/processed/datasets/<dataset_id>/)
        │
        ▼
  make version   ──► version.json, CHANGELOG.md          (src/quality/versioning.py)
        │
        ▼
  make quality   ──► quality_score.json, gate_report.json (src/quality/scoring.py, gates.py)
        │
        ▼
  make review    ──► review.json                          (src/quality/review.py)
        │
        ▼
  make recommend-data ──► coverage_report.json             (src/quality/coverage.py)
        │
        ▼
  make compare-data   ──► regression_report.json           (src/quality/regression.py)
        │
        ▼
  make dashboard ──► outputs/dashboard/<id>_dashboard.html (src/quality/dashboard.py)
```

`make quality-loop-dry-run` runs version → quality → review → recommend →
dashboard back to back against one dataset directory, no CARLA required.
`compare-data` is run separately since it needs two dataset directories
(candidate + baseline) rather than one.

Every step is independent and idempotent — re-running any of them
overwrites only the file(s) it owns, never a Phase 3 artifact and never
another engineering-loop file it doesn't produce.

---

## What each file contains

| File | Written by | Contents |
|------|-----------|----------|
| `version.json` | `make version` | `VersionRecord` — artifact identity, content hashes, config hash, `previous_artifact_id`, `lineage_parents` |
| `CHANGELOG.md` | `make version` | Added/Removed/Changed/Improved/Regressions vs. the previous version, in Markdown |
| `quality_score.json` | `make quality` | `QualityScore` — overall 0-100 score, letter grade, per-metric breakdown |
| `gate_report.json` | `make quality` | `GateReport` — pass/fail verdict, one `GateCheckResult` per configured check |
| `review.json` | `make review` | `ReviewReport` — 1-5 stars, strengths, weaknesses, all six metric scores |
| `coverage_report.json` | `make recommend-data` | `CoverageResult` — target town×weather matrix, which cells are met |
| `regression_report.json` | `make compare-data` (also written into the candidate dir by `make version`'s changelog step) | `RegressionReport` — per-dimension findings, each with a severity |
| `outputs/dashboard/<id>_dashboard.html` | `make dashboard` | Self-contained HTML rollup of all of the above, plus a quality trend chart across every versioned+scored dataset under `datasets_dir` |

All are plain JSON (`Artifact.to_dict()` / dataclass `to_dict()` methods),
readable with `python -m json.tool` or `jq` — nothing here requires the
engineering-loop code to inspect.

---

## CLI reference

| Command | Script | Requires |
|---------|--------|----------|
| `make version [DATASET_DIR=...]` | `scripts/dataset_version.py` | A built Phase 3 dataset |
| `make quality [DATASET_DIR=...]` | `scripts/dataset_quality.py` | A built dataset; exits 1 if the training gate fails |
| `make review [DATASET_DIR=...]` | `scripts/dataset_review.py` | A built dataset |
| `make recommend-data [DATASET_DIR=...]` | `scripts/recommend_data.py` | A built dataset |
| `make compare-data [CANDIDATE=... BASELINE=...]` | `scripts/compare_datasets.py` | Two built datasets; exits 1 if any finding is `failure` severity |
| `make dashboard [DATASET_DIR=...]` | `scripts/dataset_dashboard.py` | A built dataset |
| `make quality-loop-dry-run [DATASET_DIR=...]` | all of the above except `compare-data` | A built dataset (no CARLA) |

Every command defaults `DATASET_DIR` to the most recently built dataset
under `dataset_engineering.datasets_dir`, matching the existing
`inspect-dataset` convention. `compare-data`'s baseline defaults to the
candidate's own `version.json.previous_artifact_id` if it has been
versioned, or every dimension is reported `informational` with a warning
printed to stderr.

All six scripts share `scripts/_format.py` for console formatting
(`ok()`/`warn()`/`fail()`/`print_header()`) and dataset-directory
resolution (`resolve_dataset_dir()`/`resolve_baseline_artifact()`), so
none of them re-implement the "which dataset am I even looking at" logic
independently.

---

## Configuration

Every tunable value lives under `quality_engineering:` in
`config/default.yaml`. Nothing in `src/quality/` hardcodes a threshold,
weight, or file name — see `src/quality/config.py`'s
`QualityEngineeringConfig` for the authoritative dataclass shape and
defaults (every key omitted from YAML falls back to its dataclass
default, verified by a round-trip test in
`tests/unit/test_quality_engineering.py::TestConfig`).

| Section | Governs |
|---------|---------|
| `scoring:` | Metric weights, grade thresholds (A/B/C/D/F), steering-balance qualitative labels |
| `coverage:` | Target town/weather matrix, minimum episodes per cell, recommendation cap |
| `regression:` | Warning/failure thresholds per comparison dimension |
| `review:` | Strength/weakness call-out thresholds |
| `gates:` | Minimum scores to pass, which regression severity blocks training, whether a missing baseline is itself a failure |
| `dashboard:` | Output directory, quality-trend window size |
| `versioning:` | `version.json`/`CHANGELOG.md` file names |
| `lineage:` | Per-artifact-type root directories (`dataset` today; `model`/`evaluation`/`deployment` reserved for Phase 4-6) |

---

## Non-goals

Phase 3.5 does not add, and this document does not describe, any of the
following — they remain Phase 4+ per the original project brief:

- Behavioural cloning, PyTorch, or any model training code
- A PyTorch `Dataset`/`DataLoader`
- Reinforcement learning, diffusion, or VLA approaches
- Closed-loop or offline model evaluation
- Anything that reads `outputs/training/` (the directory exists only as a
  reserved `lineage.artifact_roots.model` entry for Phase 4 to use)

---

## Extending to a second artifact type (Phase 4 preview)

Every module in `src/quality/` is generic over `Artifact`
(`src/quality/schemas.py`) except `dataset_metrics.py`, which is
deliberately dataset-specific — its six metrics only make sense for a
dataset. `regression.py`'s `compare_metric_snapshots()` is artifact-agnostic
by design; `compare_datasets()` is the dataset-specific wrapper around it.
`versioning.py`'s docstring and ADR-0010 Section 2 lay out the exact
pattern Phase 4 should follow to add a `model` artifact type: a
`load_model_artifacts()` loader parallel to `load_dataset_artifacts()`, a
`ModelArtifact(Artifact)` subclass, and — if scoring is wanted — a set of
`Metric` subclasses registered under `METRIC_REGISTRY.register("model", ...)`
the same way `register_dataset_metrics()` registers the six dataset
metrics today. No existing `src/quality/` code needs to change for this;
the `CategoryRegistry` pattern (ADR-0004 Decision 6b) exists specifically
so a second category can be added without touching the first.
