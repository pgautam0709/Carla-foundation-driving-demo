# Phase 3b — Dataset Hardening

## Overview

Phase 3b adds four hardening checks on top of Phase 3a's dataset engineering
pipeline, all **informational only** — none of them exclude an episode or a
sample. **This phase still does not train a model** — no PyTorch, no
BC-CNN, no trainer. That remains Phase 4.

| Feature | What it does |
|---|---|
| Outlier detection | Flags steering spikes and stuck-throttle runs per episode |
| Duplicate frame detection | Flags samples whose frame file is byte-for-byte identical, within or across episodes |
| Steering class-balance reporting | A steering-angle histogram in `stats.json` |
| `--fix-manifest` follow-through | Writes the validation outcome back into `manifest.json`'s `validation_status` |

---

## What Was Built

### New modules

| Module | Purpose |
|---|---|
| [`src/data/dataset_io.py`](../src/data/dataset_io.py) | Shared JSONL-reading helper (extracted from `dataset_builder.py` to avoid a second copy in `dataset_outliers.py`) |
| [`src/data/dataset_outliers.py`](../src/data/dataset_outliers.py) | `OutlierThresholds`, `OutlierResult`, `check_outliers()` — steering-spike and stuck-throttle detection |
| [`src/data/dataset_duplicates.py`](../src/data/dataset_duplicates.py) | `DuplicateGroup`, `find_duplicate_frames()` — exact duplicate-frame grouping |

### Modified modules

| Module | Change |
|---|---|
| [`src/data/dataset_schemas.py`](../src/data/dataset_schemas.py) | New `HistogramBin`; `DatasetStatistics.steering_histogram`; `QualityReport.episodes_with_outliers` / `duplicate_frame_groups`; `DatasetManifest.outlier_detection_enabled` / `outlier_thresholds` / `duplicate_detection_enabled` |
| [`src/data/dataset_statistics.py`](../src/data/dataset_statistics.py) | Computes the steering histogram alongside existing per-signal stats |
| [`src/data/dataset_builder.py`](../src/data/dataset_builder.py) | Runs outlier detection per discovered episode and duplicate detection across included samples; folds both into `quality_report.json` |
| [`src/data/validation.py`](../src/data/validation.py) | New `write_validation_status()` |
| [`scripts/validate_episode.py`](../scripts/validate_episode.py) | New `--fix-manifest` flag |
| [`scripts/build_dataset.py`](../scripts/build_dataset.py) | New `--outlier-detection/--no-outlier-detection` and `--duplicate-detection/--no-duplicate-detection` flags |
| [`scripts/inspect_dataset.py`](../scripts/inspect_dataset.py) | Prints outlier/duplicate counts and an ASCII steering histogram |

### Config

`config/default.yaml`'s `dataset_engineering:` section gained:

```yaml
  outlier_detection:
    enabled: true
    steering_spike_delta: 0.6       # per-tick |Δsteer| above this is flagged
    stuck_throttle_min: 0.9         # throttle at/above this counts as "full throttle"
    stuck_speed_max_kph: 1.0        # speed at/below this counts as "not moving"
    stuck_throttle_min_ticks: 40    # consecutive ticks before it's flagged as stuck
  duplicate_detection:
    enabled: true                   # hash every included frame (adds I/O cost on large datasets)
  steering_histogram_bins: 10       # equal-width bins across [-1.0, 1.0] in stats.json
```

### Makefile

- `make fix-manifest EPISODE_DIR=<path>` — validate an episode and write `validation_status` back into its `manifest.json`

---

## Developer Commands

```bash
# Build a dataset with hardening checks on (the default):
make build-dataset

# Disable a check that's too slow or not needed for a given build:
python scripts/build_dataset.py --no-duplicate-detection
python scripts/build_dataset.py --no-outlier-detection

# Tune outlier thresholds via config/default.yaml, or inspect the ones
# actually used for a build (recorded in its manifest):
cat data/processed/datasets/<dataset_id>/dataset_manifest.json | python -m json.tool | grep -A5 outlier_thresholds

# Write validation_status back into an episode's manifest.json:
make fix-manifest EPISODE_DIR=data/raw/episodes/episode_...
python scripts/validate_episode.py data/raw/episodes/episode_... --fix-manifest
```

---

## Artifact Reference (additions to Phase 3a's artifacts)

### `quality_report.json` — new fields

```json
{
  "episodes_with_outliers": 1,
  "duplicate_frame_groups": 2,
  "issues": [
    {"episode_id": "episode_stuck", "severity": "warning", "message": "stuck-throttle: 45 consecutive ticks with throttle >= 0.9 and speed <= 1.0 kph"},
    {"episode_id": "episode_spike", "severity": "warning", "message": "1 steering spike(s) exceeding |Δsteer| > 0.6 (max observed 0.90)"},
    {"episode_id": "<dataset>", "severity": "warning", "message": "5 samples share an exact duplicate frame (byte-identical content) across 2 episode(s): episode_a_000000, episode_a_000001, episode_b_000000, episode_b_000001, episode_b_000002 (+1 more)"}
  ]
}
```

Outlier and duplicate findings are always `severity: "warning"` — they
never change whether an episode or sample is included, only add visibility.
A duplicate group confined to one episode is attributed to that
`episode_id`; a group spanning multiple episodes uses the same
`"<dataset>"` sentinel Phase 3a introduced for split-coverage warnings.

### `stats.json` — steering histogram

```json
{
  "steering_histogram": [
    {"range_min": -1.0, "range_max": -0.8, "count": 12},
    {"range_min": -0.8, "range_max": -0.6, "count": 30},
    "...",
    {"range_min": 0.8, "range_max": 1.0, "count": 9}
  ]
}
```

Purely informational — nothing in this codebase reads it back to resample,
reweight, or balance the dataset. It exists so a human (or a future Phase 4
training script, deliberately not written here) can *see* the class
imbalance before deciding what, if anything, to do about it.

### `dataset_manifest.json` — new fields

```json
{
  "outlier_detection_enabled": true,
  "outlier_thresholds": {
    "steering_spike_delta": 0.6,
    "stuck_throttle_min": 0.9,
    "stuck_speed_max_kph": 1.0,
    "stuck_throttle_min_ticks": 40
  },
  "duplicate_detection_enabled": true
}
```

`outlier_thresholds` is `null` when `outlier_detection_enabled` is `false`
— there is nothing to record.

---

## Design Decisions

### Outlier and duplicate detection are always informational, never exclusionary

Neither check can turn an `included: true` episode into `included: false`,
or drop a sample from `samples_index.jsonl`. This mirrors the lesson from
Phase 3a's closure review: a hardening pass that silently starts excluding
data on a new heuristic, with no way to see what got excluded, is exactly
the kind of "silent behavior change" that review caught with alignment
truncation. Here, findings are visible (`quality_report.json`,
`inspect_dataset.py`) but never gate the dataset.

### Only exact (byte-identical) duplicate detection — no perceptual near-duplicate detection

`find_duplicate_frames()` hashes raw frame bytes with SHA-256 and groups
exact matches — stdlib only (`hashlib`), no new dependency. Detecting
*near*-duplicates (visually similar but not byte-identical frames, e.g. two
frames one tick apart at a red light) would require decoding pixel data and
computing a perceptual hash (e.g. average/difference hash), which needs an
image library. Pillow and OpenCV are already present in this project's
optional `sim` dependency group — but adding a hard dependency on either to
the dataset-engineering path would break the "no CARLA, Docker, GPU, or
PyTorch dependency" invariant this phase has maintained since Phase 3a,
for machines that only ever run the base install. Given that trade-off,
this pass implements exact-match detection only; near-duplicate detection
is left as a documented gap rather than silently degraded or half-built.

### Steering spikes are the tick-to-tick derivative, not a fixed magnitude threshold

`check_outliers()` flags `|steer[t] - steer[t-1]| > steering_spike_delta`,
not `|steer[t]| > threshold`. A large steering angle is normal (a sharp
turn); a large angle that appears in a single 50 ms tick with no
transition is not — steering wheels do not teleport. The derivative check
catches the latter without penalizing genuinely sharp turns.

### Stuck-throttle requires two signals, not one

Full throttle alone is normal (e.g. highway acceleration); the check only
fires when full throttle **and** near-zero speed persist together for a
sustained run — the vehicle is commanding maximum acceleration but not
actually moving, which is the actual anomaly (stuck against an obstacle,
wheels spinning). Checking throttle in isolation would flag ordinary
highway driving as an "outlier."

### `write_validation_status()` lives in `validation.py`, not a new module

This was explicitly planned in Phase 2: `docs/PHASE2_DATA_COLLECTION.md`
ADR-003 states collection always writes `validation_status: "unchecked"`
and that "the validator updates it only when `--fix-manifest` is passed
(Phase 3 feature)." Adding this function to the existing
`src/data/validation.py` — rather than a new Phase 3 module — keeps that
promise literally: the same validator that decided `valid`/`invalid` is
what writes the outcome back.

---

## Verified Results

| Check | Result |
|---|---|
| `make lint` | ✅ All checks passed |
| `make type-check` | ✅ No issues in any Phase 3 file |
| `make test` | ✅ **203/203 unit tests pass** (no CARLA required) |
| `make dataset-dry-run` | ✅ End to end, hardening checks run by default |

**Success Criteria:**
- ✅ Outlier and duplicate detection findings are visible in
  `quality_report.json` and `inspect_dataset.py`, never silently exclude
  data, and can be disabled per-flag
- ✅ Steering histogram is written to `stats.json` and does not feed any
  resampling logic (there is none — Phase 4 will decide what, if anything,
  to do with it)
- ✅ `--fix-manifest` writes `validation_status` back into `manifest.json`
  without disturbing any other field
- ✅ No CARLA, Docker, GPU, or PyTorch dependency anywhere in this layer
- ✅ Phase 0, 1, 2, and 3a behavior is unchanged

**Known limitations:**
- **Near-duplicate (perceptual) detection is out of scope** — see Design
  Decisions above. Only byte-identical frames are caught.
- **Dry-run / synthetic episodes are, by construction, one giant duplicate
  group.** Phase 2's `--dry-run` mode always emits a solid black PNG per
  tick (see `_make_black_png` in `scripts/collect_expert_episode.py`), so
  every dry-run frame across every episode is byte-identical. Running
  `make dataset-dry-run` will report a large `duplicate_frame_groups`
  count — this reflects the synthetic data honestly and is not a defect in
  the detector or in real collected data.
- Hashing every included frame adds I/O proportional to dataset size;
  `--no-duplicate-detection` is the escape hatch for very large datasets
  where this becomes a bottleneck.
- The stuck-throttle and steering-spike thresholds are global constants
  per build, not per-map or per-route — a genuinely twisty mountain route
  might trip the steering-spike check more often than a highway route at
  the same threshold. Thresholds are config/CLI-tunable per build to
  compensate.

**Future enhancement (not implemented):** Perceptual near-duplicate
detection can be added later using image hashing (e.g. average/difference
hash) or embeddings, but Phase 3b intentionally avoids image-processing
dependencies. Everywhere in this document, in code, and in tool output,
"duplicate" for this feature means **exact, byte-for-byte identical** frame
content — never a similarity or perceptual match.

**Extension points for Phase 4 (model training):** none of this phase's
output is consumed anywhere yet — `stats.json`'s histogram and
`quality_report.json`'s outlier/duplicate findings are available for a
future training script to read, log, or act on, but nothing in this
codebase does so today.
