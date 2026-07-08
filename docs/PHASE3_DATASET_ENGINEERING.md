# Phase 3 — Dataset Engineering

## Overview

Phase 3 turns a directory of Phase 2 episodes into a flat, indexed,
quality-checked dataset that a future PyTorch `Dataset` can read directly. It
answers the engineering question:

> **Given whatever episodes Phase 2 has collected so far — of varying
> length, quality, and completeness — can we produce one deterministic,
> inspectable dataset artifact that training can trust?**

Model training itself (`src/models/`, `src/training/`, `scripts/train.py`)
is **Phase 4, not Phase 3** — this phase does not import PyTorch, define a
model, or write a training loop.

---

## What Was Built

### New modules

| Module | Purpose |
|---|---|
| [`src/data/dataset_schemas.py`](../src/data/dataset_schemas.py) | Dataclasses for all index/manifest/report records (schema v1.0) |
| [`src/data/dataset_discovery.py`](../src/data/dataset_discovery.py) | Finds episode directories under a raw episodes directory |
| [`src/data/dataset_alignment.py`](../src/data/dataset_alignment.py) | Checks frame/control/telemetry alignment per episode; reports a usable prefix without deciding inclusion |
| [`src/data/dataset_splits.py`](../src/data/dataset_splits.py) | Deterministic **batch** train/val/test split assignment across all included episodes |
| [`src/data/dataset_statistics.py`](../src/data/dataset_statistics.py) | Aggregate signal statistics |
| [`src/data/dataset_builder.py`](../src/data/dataset_builder.py) | Orchestrates discovery → validation → alignment → splits → samples → statistics → output files |

### New scripts

| Script | Purpose |
|---|---|
| [`scripts/build_dataset.py`](../scripts/build_dataset.py) | Builds the dataset from Phase 2 episodes |
| [`scripts/inspect_dataset.py`](../scripts/inspect_dataset.py) | Prints a human-readable summary of a built dataset |

### Config

`config/default.yaml` gained a `dataset_engineering:` section (raw episode
source, versioned output location, split ratios/seed, inclusion thresholds,
and the `allow_partial_alignment` strictness switch) — see
[Config Reference](#config-reference) below.

### Makefile

- `make build-dataset` — build a new versioned dataset from the configured raw episode directory
- `make inspect-dataset` — print a summary of a dataset (`DATASET_DIR=<path>`, defaults to the most recently built)
- `make dataset-dry-run` — full smoke test: generate a synthetic Phase 2 episode, build the dataset, print the summary — no CARLA required

---

## Pipeline

```
data/raw/episodes/<episode_id>/ ...       (Phase 2 output, unchanged)
            │
            ▼
   discover_episodes()                     any directory with metadata.json
            │
            ▼
   EpisodeValidator().validate()           reused from Phase 2, unchanged
            │
            ▼
   check_alignment()                       frame/control/telemetry tick agreement
            │                              (reports a usable prefix; does NOT decide inclusion)
            ▼
   inclusion decision                      strict by default: misaligned → excluded
            │                              --allow-partial-alignment: misaligned → truncated + included
            ▼
   assign_splits() — BATCH over all         deterministic hash order + largest-remainder
   included episodes at once                method; guarantees train is non-empty
            │                              whenever samples exist
            ▼
   build samples (one per usable tick)
            │
            ▼
   compute_statistics()
            │
            ▼
data/processed/datasets/<dataset_id>/     <dataset_id> defaults to a UTC
    dataset_manifest.json                 timestamp (dataset_YYYYMMDD_HHMMSS);
    episodes_index.jsonl                   --dataset-id overrides it;
    samples_index.jsonl                    --output-dir bypasses this nesting
    stats.json                             entirely (see Config Reference)
    quality_report.json
    splits/
        train.jsonl          ← samples_index.jsonl rows filtered to split=train
        val.jsonl            ← ... filtered to split=val
        test.jsonl           ← ... filtered to split=test
```

Every `make build-dataset` (or bare `python scripts/build_dataset.py`)
creates a **new** dataset directory rather than overwriting the previous
one — datasets are versioned, not mutated in place.

---

## Developer Commands

```bash
# Build a new versioned dataset from data/raw/episodes (no CARLA required).
# Writes to data/processed/datasets/dataset_<UTC timestamp>/:
make build-dataset

# Give the build a memorable name instead of a timestamp:
python scripts/build_dataset.py --dataset-id baseline_v1

# Print a summary of a dataset. With no DATASET_DIR, inspects the most
# recently built dataset under data/processed/datasets/:
make inspect-dataset
make inspect-dataset DATASET_DIR=data/processed/datasets/baseline_v1
python scripts/inspect_dataset.py --dataset-dir data/processed/datasets/baseline_v1 --verbose

# Full smoke test — generate + build + inspect, no CARLA required.
# Always writes to data/processed/datasets/dry_run_dataset/ (fixed name,
# overwritten on each run) so the smoke test is easy to find and re-run:
make dataset-dry-run

# Include misaligned episodes truncated to their usable prefix, instead of
# excluding them (strict alignment is the default):
python scripts/build_dataset.py --allow-partial-alignment

# Write to an exact path instead of the versioned datasets_dir/<dataset_id>/
# default:
python scripts/build_dataset.py --output-dir data/processed/scratch

# Full options:
python scripts/build_dataset.py --help
python scripts/inspect_dataset.py --help
```

---

## Artifact Reference

### `episodes_index.jsonl` — one row per discovered episode

```jsonl
{"episode_id": "episode_20260707_143012_Town03_routeA_local_dev", "episode_dir": "data/raw/episodes/episode_...", "town": "Town03", "route_name": "routeA", "collection_mode": "dry_run", "created_at": "...", "frame_count": 500, "control_row_count": 500, "telemetry_row_count": 500, "valid": true, "validation_errors": [], "aligned": true, "alignment_issues": [], "usable_tick_count": 500, "included": true, "exclusion_reason": null, "truncated": false, "split": "train"}
```

`truncated` is `true` only when the episode was misaligned (`aligned:
false`) **and** still included — i.e. `--allow-partial-alignment` was set
and the usable prefix met `min_episode_ticks`. With the default strict
alignment, a misaligned episode is always `included: false`, never
`truncated: true`.

### `samples_index.jsonl` — one row per usable tick of an included episode

```jsonl
{"sample_id": "episode_..._000000", "episode_id": "episode_...", "tick": 0, "frame_path": "data/raw/episodes/episode_.../frames/front_camera/000000.png", "throttle": 0.72, "brake": 0.0, "steer": -0.05, "speed_kph": 18.7, "split": "train"}
```

### `splits/train.jsonl`, `splits/val.jsonl`, `splits/test.jsonl`

Each file is exactly the subset of `samples_index.jsonl` rows whose
`split` field matches the filename — a pre-filtered view so a future
PyTorch `Dataset` can load one split file directly with no runtime
filtering. All three files always exist, even if empty (e.g. `val.jsonl`
and `test.jsonl` may be empty for a 1-2 episode dry run — see
[Splitting](#splitting-per-episode-not-per-sample) below).

### `stats.json` — aggregate dataset statistics

```json
{
  "episode_count": 2,
  "sample_count": 520,
  "split_counts": {"train": 520, "val": 0, "test": 0},
  "towns": {"Town03": 2},
  "throttle": {"mean": 0.42, "std": 0.18, "min": 0.0, "max": 1.0},
  "brake": {"...": "..."},
  "steer": {"...": "..."},
  "speed_kph": {"...": "..."}
}
```

### `dataset_manifest.json` — build summary

```json
{
  "schema_version": "1.0",
  "created_at": "...",
  "git_commit": "caf53e1",
  "dataset_id": "dataset_20260708_030000",
  "raw_episodes_dir": "data/raw/episodes",
  "output_dir": "data/processed/datasets/dataset_20260708_030000",
  "episode_count_discovered": 2,
  "episode_count_included": 2,
  "episode_count_excluded": 0,
  "sample_count": 520,
  "split_ratios": {"train": 0.8, "val": 0.1, "test": 0.1},
  "split_seed": 42,
  "allow_partial_alignment": false,
  "episodes_index_path": "episodes_index.jsonl",
  "samples_index_path": "samples_index.jsonl",
  "quality_report_path": "quality_report.json",
  "statistics_path": "stats.json",
  "splits_dir": "splits",
  "split_index_paths": {"train": "splits/train.jsonl", "val": "splits/val.jsonl", "test": "splits/test.jsonl"}
}
```

The manifest holds only paths and build parameters, not the statistics
themselves — see [Design Decisions](#statistics-and-splits-live-in-their-own-files-not-embedded-in-the-manifest).

### `quality_report.json` — per-episode and per-build issues

```json
{
  "schema_version": "1.0",
  "created_at": "...",
  "episodes_scanned": 3,
  "episodes_valid": 2,
  "episodes_invalid": 1,
  "episodes_included": 1,
  "episodes_excluded": 2,
  "episodes_misaligned": 1,
  "episodes_truncated": 0,
  "issues": [
    {"episode_id": "episode_bad", "severity": "error", "message": "Missing required file: route.json"},
    {"episode_id": "episode_misaligned", "severity": "error", "message": "frame_count (10) != control_row_count (11)"},
    {"episode_id": "<dataset>", "severity": "warning", "message": "split 'val' has 0 samples despite a configured ratio of 0.1 — likely too few episodes to cover every split"}
  ]
}
```

`episode_id: "<dataset>"` marks an issue that applies to the whole build
rather than one episode (currently: a configured split ending up with zero
samples). Severity follows a simple rule: `"error"` means the episode (or
in the case of `EpisodeValidator` errors, always) was excluded or failed
outright; `"warning"` means the data was still used, with a caveat.

---

## Config Reference

```yaml
dataset_engineering:
  raw_episodes_dir: "data/raw/episodes"    # source of Phase 2 episodes
  datasets_dir: "data/processed/datasets"  # parent dir; each build gets its own <dataset_id>/ subdirectory
  dataset_id: null                         # null = auto-generate a timestamped ID at build time
  output_dir: null                         # explicit full output path override (bypasses
                                            # datasets_dir/dataset_id when set) — null = use versioned default
  require_valid: true                      # drop episodes that fail EpisodeValidator
  allow_partial_alignment: false           # strict by default: drop misaligned episodes
                                            # rather than silently truncating them
  min_episode_ticks: 1                     # episodes with fewer usable ticks are dropped
  split_seed: 42                           # deterministic split assignment seed
  split_ratios:
    train: 0.8
    val: 0.1
    test: 0.1
```

**Resolving the output directory:** if `--output-dir` (or config
`output_dir`) is set, it is used exactly as given, and `dataset_id`
defaults to that directory's own name. Otherwise the build writes to
`datasets_dir/<dataset_id>/`, where `dataset_id` is `--dataset-id` (or
config `dataset_id`) if given, else a freshly generated
`dataset_YYYYMMDD_HHMMSS` timestamp — so two builds without an explicit ID
never collide.

All fields are overridable on the CLI (`--raw-episodes-dir`, `--dataset-id`,
`--output-dir`, `--split-seed`, `--min-episode-ticks`,
`--require-valid/--no-require-valid`,
`--allow-partial-alignment/--no-allow-partial-alignment`); CLI flags win
over config, per the project's config contract.

---

## Design Decisions

### Split by episode, not by sample

Every sample from a given episode is assigned the same split. Consecutive
ticks within one episode are highly correlated (near-duplicate frames of the
same drive); splitting at the sample level would leak correlated frames
across train/val/test and inflate validation metrics. See
[`src/data/dataset_splits.py`](../src/data/dataset_splits.py).

### Splits are assigned as a batch, with a largest-remainder method and a train guarantee

`assign_splits()` takes **all** included episode IDs at once — not one
episode independently at a time. Each episode's position in a
deterministic hash-sorted order (via `compute_route_hash`, the same helper
Phase 2 uses for route hashing) is combined with the largest-remainder
method to turn ratios into exact integer counts.

This replaced an earlier per-episode design where each episode was bucketed
independently via its own hash. That design was deterministic and correct
on average, but for **small numbers of episodes it could — and during
closure testing with only 1-2 dry-run episodes, did — place every episode
in the same minority split, leaving `train` completely empty** even though
samples existed. The batch method:
- Produces exact proportional counts for larger datasets (e.g. exactly
  80/10/10 for 100 episodes at the default ratios).
- Explicitly guarantees `train` is never empty when at least one episode
  is included and `train`'s ratio is greater than zero — see
  `_guarantee_priority_split()`.
- Still reshuffles completely when `split_seed` changes, and is invariant
  to the order episodes were discovered in.

When there are too few episodes to give every split a nonzero share (e.g.
2 episodes at 0.8/0.1/0.1), `val` and/or `test` legitimately end up with 0
samples — this is not a bug, but it **is** surfaced: `build_dataset()`
appends a `"warning"`-severity, dataset-level (`episode_id: "<dataset>"`)
issue to `quality_report.json` for every configured split with a positive
ratio that ended up empty.

### Alignment is strict by default; truncation is opt-in

`check_alignment()` computes `usable_tick_count` — the longest shared
contiguous `0..N-1` prefix across frames, `controls.jsonl`, and
`telemetry.jsonl` — but **does not decide inclusion**. `build_dataset()`
does, via `allow_partial_alignment`:

- **Default (`allow_partial_alignment=False`):** an episode where
  `aligned` is False is **excluded** entirely. Its alignment discrepancies
  are recorded in `quality_report.json` with `severity: "error"`, and
  `exclusion_reason` on its `episodes_index.jsonl` row explains why.
- **Opt-in (`allow_partial_alignment=True`):** such an episode is
  **included**, but only its usable prefix becomes samples. Its
  `episodes_index.jsonl` row gets `truncated: true`, and
  `quality_report.json` records the same discrepancies with
  `severity: "warning"` plus an explicit truncation message stating the
  before/after tick counts.

An earlier version of this module truncated by default with no opt-out,
which silently changed what "included" meant per episode without a config
switch to disable it. Strict-by-default matches the principle that data
pipelines should fail loud, not quietly reinterpret partial data as
complete; `allow_partial_alignment` exists for callers who have judged
truncation acceptable for their use case (e.g. bootstrapping a small
dataset from a few partial dry runs).

Either way, only episodes whose usable prefix falls below
`min_episode_ticks`, or that fail `EpisodeValidator` outright (when
`require_valid: true`), are excluded for those additional reasons.

### Statistics and splits live in their own files, not embedded in the manifest

`stats.json` and `splits/{train,val,test}.jsonl` are dedicated files, not
fields nested inside `dataset_manifest.json`. The manifest holds paths to
every other artifact plus the build parameters used to produce them — it
is the index of indexes, not a container for their content. This also
means a consumer that only wants split membership (`splits/train.jsonl`)
or only wants statistics (`stats.json`) can read a small, purpose-built
file instead of parsing the full manifest.

### Each build gets its own versioned directory, not a shared mutable path

**Decision:** `build_dataset()` still just writes to whatever `output_dir`
it is given — that has not changed. What changed is what
`scripts/build_dataset.py` passes by default: instead of the fixed path
`data/processed`, it resolves `data/processed/datasets/<dataset_id>/`,
where `<dataset_id>` is a fresh UTC timestamp unless `--dataset-id` (or
config `dataset_id`) is given.

**Rationale:**
- A single shared `data/processed/` output meant every `build-dataset` run
  silently overwrote the previous one — there was no way to keep dataset
  `v1` around while building `v2` to compare against it, and no record of
  *which* raw episodes a given `stats.json` or `splits/train.jsonl`
  actually came from once a later build replaced it.
- Naming the directory after the build (`dataset_id`, recorded in
  `dataset_manifest.json` regardless of how the path was chosen) makes a
  dataset a durable, referenceable artifact — Phase 4 training runs can
  record which `dataset_id` they trained against.
- `--output-dir` still exists and is used exactly as given with no
  versioning applied, for callers who genuinely want a scratch/throwaway
  location (tests use this heavily) or their own directory convention.

**Consequences:**
- `inspect_dataset.py` and `make inspect-dataset` no longer have one fixed
  path to fall back to; both default to the most recently modified
  subdirectory of `datasets_dir` (mirroring the existing "most recent
  episode" pattern `make validate-episode` already used for
  `EPISODE_DIR`).
- `make dataset-dry-run` uses the fixed id `dry_run_dataset` rather than a
  timestamp, so the smoke test always writes to (and overwrites) the same
  known location — appropriate for a repeatable smoke test, in contrast to
  `make build-dataset`'s real builds, which should not overwrite each other.

### Reuses `EpisodeValidator` unmodified

Phase 3 does not duplicate or fork Phase 2's validation logic. It calls
`EpisodeValidator().validate()` as-is and folds `ValidationResult.errors`
into the quality report. Phase 2 behavior is untouched.

---

## Verified Results

| Check | Result |
|---|---|
| `make lint` | ✅ All checks passed |
| `make type-check` | ✅ No issues in the new modules |
| `make test` | ✅ **175/175 unit tests pass** (no CARLA required) — 121 Phase 0–2 + 54 new |
| `make dataset-dry-run` | ✅ Generates an episode, builds the dataset, prints the summary — end to end, no CARLA |

**Success Criteria:**
- ✅ `make build-dataset` produces all eight artifacts from any set of Phase 2 episodes, including zero episodes
- ✅ Alignment is strict by default; truncation only happens with `--allow-partial-alignment`, and either way is recorded in the quality report
- ✅ Rebuilding the same episodes with the same seed reproduces identical split assignments; `train` is never left empty when samples exist
- ✅ Misaligned, invalid, or split-coverage issues are reported in `quality_report.json` and shown by `inspect_dataset.py`, not silently dropped or included
- ✅ No CARLA, Docker, GPU, or PyTorch dependency anywhere in the dataset engineering path
- ✅ Phase 0, Phase 1, and Phase 2 behavior is unchanged

**Known limitations:**
- With very few episodes (fewer than the number of configured splits), a
  minority split can still legitimately end up empty — this is reported
  as a quality warning, not silently hidden, but it is not "fixed" because
  there is no correct nonzero assignment when there simply aren't enough
  episodes to go around.
- `assign_splits()` currently assumes the three canonical split names
  (`train`, `val`, `test`) for the "never empty" guarantee and for
  `SplitCounts`/statistics; a config with different split names would still
  build correctly but wouldn't benefit from the `train`-specific guarantee.

**Extension points for Phase 4 (model training):**
- `splits/train.jsonl`, `splits/val.jsonl`, `splits/test.jsonl` are the
  intended inputs to a future `src/data/dataset.py` PyTorch `Dataset` (not
  implemented in this phase)
- `stats.json` is the intended source for input normalization constants
