# ADR 0002 — Phase 3 Dataset Engineering Design Decisions

**Date:** 2026-07-08 (amended same day after closure review)
**Status:** Accepted
**Deciders:** AI Engineering Team

> **Amendment note:** A closure review of the initial implementation found
> two real defects, corrected in this revision: (1) independent per-episode
> hash bucketing could leave the `train` split completely empty for small
> episode counts (reproduced with a 2-episode dry run); (2) misaligned
> episodes were truncated and included by default with no way to require
> strict alignment. Decisions 2, 3, and 5 below describe the corrected
> design; the original (superseded) reasoning is kept where relevant so the
> record shows why the change was made, not just what it changed to.

---

## Context

Phase 2 produces one self-contained flat-file directory per episode
(`metadata.json`, `route.json`, `controls.jsonl`, `telemetry.jsonl`,
`events.jsonl`, `manifest.json`, `frames/front_camera/*.png`). Episodes vary
in length, some may be partial (`status: "partial"` from a
`KeyboardInterrupt`), and none of them are indexed together. Before any
model training code is written, this project needs a deterministic,
inspectable step that turns "a directory of episodes" into "one dataset,"
and a place to make the quality/inclusion decisions training will otherwise
have to make ad hoc. This ADR records the decisions made in
[`src/data/dataset_builder.py`](../../src/data/dataset_builder.py) and its
supporting modules.

---

## Decisions

### 1. Split assignment is per-episode, not per-sample

**Decision:** Every sample from a given episode is assigned the same
train/val/test split.

**Rationale:**
- Consecutive ticks within an episode are highly correlated — the front
  camera changes only slightly frame to frame at 20 Hz.
- Splitting at the sample level would put near-duplicate frames in both
  train and validation, inflating validation metrics without the model
  actually generalizing.
- Per-episode splitting is the standard practice for sequential/driving
  datasets (comparable to how nuScenes/Waymo split by scene, not by frame).

**Consequences:**
- Split ratios are only approximate for small numbers of episodes — with
  very few episodes, an entire episode can tip a split's share well away
  from its configured ratio. This is expected and improves as more episodes
  are collected.

### 2. Splits are assigned as a deterministic batch, not independently per episode

**Decision:** `assign_splits()` takes the full list of included episode IDs
at once. It orders them by a deterministic hash of `(episode_id,
split_seed)` (via `compute_route_hash()`, the same SHA-256-based helper
Phase 2 uses for route hashing), then converts `split_ratios` into exact
integer counts with the largest-remainder method, then walks the ordered
list handing out that many episodes to each split. A final explicit check
guarantees `train` gets at least one episode whenever any episodes are
included and `train`'s ratio is positive.

**Superseded design:** the original version assigned each episode
independently — `hash(episode_id, seed) mod N` compared against cumulative
ratio thresholds, computed per episode with no knowledge of the others.
This was deterministic and, for a large number of episodes, distributed
close to the configured ratios (verified by a 3000-episode test). **It was
not safe for small episode counts.** A dry run with only 2 episodes
produced `train=0, val=0, test=2` — both episodes independently hashed into
the same minority split by chance, and nothing in the design prevented it.
Since a fresh Phase 2 collection run often starts with a handful of
episodes, this was not a theoretical edge case but the first thing closure
testing hit.

**Rationale for the batch approach:**
- The largest-remainder method is the standard technique for turning a
  ratio into integer counts that sum exactly to the total — for 100
  episodes at 0.8/0.1/0.1 it produces exactly 80/10/10, not "approximately."
- Batch assignment still requires no persisted state and no random module:
  the hash-based order is itself the only "randomness," and it is a pure
  function of `(episode_id, seed)`.
- The train-guarantee is a small, explicit, well-commented correction
  (`_guarantee_priority_split()`) rather than a hope that ratio proportions
  happen to favor `train` — it is checked, not assumed.
- Changing `split_seed` still reshuffles every assignment; discovery order
  still does not affect the result (episodes are re-sorted by hash, not
  read in list order).

**Consequences:**
- `val` and/or `test` can still legitimately end up empty when there are
  too few episodes to give every split a nonzero share. This is now always
  surfaced: `build_dataset()` emits a dataset-level (`episode_id:
  "<dataset>"`) `"warning"` quality issue for every split with a positive
  ratio and zero assigned samples, so it is visible in `quality_report.json`
  and `inspect_dataset.py` rather than silently absent.
- The train-guarantee is specific to the split named `"train"`. A config
  using different split names loses that specific guarantee (though the
  largest-remainder method still applies); see Known Limitations in
  [`docs/PHASE3_DATASET_ENGINEERING.md`](../PHASE3_DATASET_ENGINEERING.md).

**Rejected alternatives:**
- Random shuffle with a fixed RNG seed: reproducible only as long as episode
  discovery order never changes; adding one episode can reshuffle every
  other episode's split.
- A persisted `splits.json` assignment file: an extra artifact and an extra
  place for staleness (drifting from `split_ratios` in config) with no
  benefit over a pure function.

### 3. Alignment is strict by default; truncation requires an explicit opt-in

**Decision:** `check_alignment()` computes `usable_tick_count` — the longest
shared contiguous `0..N-1` prefix across frames, `controls.jsonl`, and
`telemetry.jsonl` — but only *reports* it; it does not decide inclusion.
`build_dataset()` decides, via the `allow_partial_alignment` parameter
(config key `dataset_engineering.allow_partial_alignment`, CLI flag
`--allow-partial-alignment`, **default `False`**):
- **Strict (default):** an episode with `aligned: False` is **excluded**.
  Its discrepancies are recorded in `quality_report.json` with
  `severity: "error"`.
- **Partial (opt-in):** an episode with `aligned: False` is **included**,
  truncated to `usable_tick_count`. Its `episodes_index.jsonl` row gets
  `truncated: true`, and `quality_report.json` records the same
  discrepancies with `severity: "warning"` plus an explicit message stating
  the truncated tick count.

**Superseded design:** the original version always truncated and included
misaligned episodes, with no way to require exact alignment. This meant
"included in the dataset" silently meant different things per episode —
some contributed every tick, others contributed a truncated prefix — with
no config switch to demand the strict behavior, only a quality-report
message an operator had to already know to look for.

**Rationale for strict-by-default:**
- A dataset engineering step's job is to make quality decisions legible,
  not to quietly reinterpret "5 of 10 ticks are usable" as "included." Data
  pipelines should fail loud by default; silently accepting partial data is
  an opt-in a caller makes deliberately, not a default they discover later
  by noticing their train split is smaller than expected.
- Phase 2's own `EpisodeValidator` already tolerates count mismatches as
  informational (not a validation failure) — see
  [`src/data/validation.py`](../../src/data/validation.py) — so Phase 3
  still needed its own explicit knob rather than inheriting silence from
  Phase 2's more lenient definition of "valid."
- Truncating to the shared prefix (when `allow_partial_alignment=True`) is
  still the only sound choice for a BC training sample — a sample is a
  `(frame, control, telemetry)` triple, so a tick missing any one of the
  three cannot become a sample regardless of the others being present.

**Consequences:**
- With the default strict setting, a single dropped frame mid-episode
  excludes the entire episode, even if most of it is well-formed. Operators
  who want to keep the usable prefix of such episodes must explicitly pass
  `--allow-partial-alignment` — a conscious choice, not a silent default.
- The alignment check itself never attempts to reconstruct or splice around
  gaps; a gap is information about collection quality that
  `quality_report.json` surfaces either way (as an `"error"` if it caused
  exclusion, as a `"warning"` if the caller opted into truncation).

### 4. Episode validity and dataset inclusion are independent, tunable knobs

**Decision:** `require_valid` (default `true`) controls whether
`EpisodeValidator` failures exclude an episode; `min_episode_ticks` (default
`1`) independently controls how short a usable prefix may be before
exclusion. Both apply per-episode and are recorded in
`episodes_index.jsonl` regardless of outcome.

**Rationale:**
- Keeps the "is this well-formed" question (Phase 2's concern) separate
  from the "is this useful for training" question (Phase 3's concern) —
  they are allowed to disagree, and a caller who wants every discovered
  frame indexed (e.g. for manual data auditing) can set
  `require_valid=false` without touching validator internals.

### 5. Statistics and per-split sample lists are their own files, referenced by path from the manifest

**Decision:** `compute_statistics()`'s output is written to its own
`stats.json`; per-split sample lists are written to `splits/train.jsonl`,
`splits/val.jsonl`, `splits/test.jsonl`. `dataset_manifest.json` holds only
paths to these files (`statistics_path`, `splits_dir`,
`split_index_paths`) plus top-level build parameters and counts, not their
content.

**Superseded design:** the original version embedded the full statistics
object as a nested field inside `dataset_manifest.json`, and did not
produce per-split sample files at all — a consumer wanting only `train`
samples had to load and filter the full `samples_index.jsonl` itself.

**Rationale for the corrected design:**
- Per-split files are the more useful artifact for the eventual PyTorch
  `Dataset` consumer (Phase 4): `splits/train.jsonl` loads directly with no
  filtering logic duplicated in every consumer.
- Separating statistics into `stats.json` means a consumer that only wants
  normalization constants (mean/std per signal) reads one small file
  instead of parsing the full manifest.
- The manifest becomes purely "the index of indexes" — a stable, small
  document naming every other artifact and the parameters used to produce
  them — rather than a mixed document that also carries one artifact's full
  content inline.

**Consequences:**
- Consumers must open one extra file (`stats.json` or a `splits/*.jsonl`)
  rather than finding everything under a single top-level manifest key.
  This is judged a reasonable cost for the clearer per-purpose file
  boundaries above.

### 6. `EpisodeValidator` is reused unmodified, not forked

**Decision:** `dataset_builder.py` imports and calls
`src.data.validation.EpisodeValidator` exactly as Phase 2 left it.

**Rationale:**
- Avoids two divergent definitions of "is this episode well-formed."
- Phase 2's 14 checks (file presence, JSONL parseability, required fields,
  frame sequencing) are exactly the checks a Phase 3 consumer needs too —
  there was nothing to add or change.

**Consequences:**
- Any future change to what makes an episode "valid" is made once, in
  `src/data/validation.py`, and both `validate_episode.py` (Phase 2) and
  `build_dataset.py` (Phase 3) pick it up automatically.

### 7. Quality issue severity tracks inclusion, and build-level issues use a sentinel episode_id

**Decision:** `QualityIssue.severity` is `"error"` when the associated
episode was excluded from the dataset, and `"warning"` when it was included
(with or without a caveat) — this applies uniformly to validation errors
and alignment discrepancies alike. Issues that describe the whole build
rather than one episode (currently: a configured split ending up with zero
samples) use the sentinel `episode_id: "<dataset>"` rather than adding a
second issue type or a separate list.

**Rationale:**
- A single severity rule ("did this exclude the episode?") is easier to
  reason about than per-source-specific severity rules, and lets
  `inspect_dataset.py` render one issue list instead of several.
- A sentinel `episode_id` keeps `QualityIssue` a single flat record shape.
  Introducing a second dataclass for build-level issues would mean two
  issue lists to merge and re-sort for display, for one currently-existing
  issue kind.

**Consequences:**
- `"<dataset>"` is a reserved value: an actual Phase 2 episode ID can never
  collide with it because `generate_episode_id()` always produces an
  `episode_...`-prefixed identifier.

### 8. Each build writes to its own versioned directory, not a shared fixed path

**Decision:** `build_dataset()` itself remains agnostic — it writes
wherever `output_dir` tells it to, as it always has. `scripts/build_dataset.py`
now resolves that path to `datasets_dir/<dataset_id>/` by default (parent
directory `data/processed/datasets`, `<dataset_id>` a UTC timestamp or an
explicit `--dataset-id`), instead of the fixed `data/processed`.
`DatasetManifest` gained a `dataset_id` field, defaulting to `output_dir`'s
own final path component when not given explicitly.

**Superseded design:** the original CLI defaulted to the single fixed path
`data/processed` for every build. Running `build-dataset` twice — e.g. once
before and once after collecting more episodes — silently overwrote the
first build with no record that an earlier version had existed, no way to
compare the two, and no way for a Phase 4 training run to record which
exact dataset it trained against.

**Rationale:**
- A dataset used to train a model is itself an artifact worth keeping
  around and referring back to, not a scratch file to be clobbered by the
  next build.
- Recording `dataset_id` in the manifest (rather than relying solely on the
  directory name) means the identity survives if the directory is later
  moved, copied, or archived elsewhere.
- `--output-dir` is preserved unchanged for callers who want an exact path
  with no versioning — tests rely on this heavily, and it remains the
  simplest way to point at a scratch location.

**Consequences:**
- `inspect_dataset.py` no longer has one universally-correct default path;
  it now resolves the most recently modified subdirectory of `datasets_dir`
  when `--dataset-dir` is omitted, mirroring the pre-existing
  `EPISODE_DIR` "most recent" convention in the `Makefile` for
  `validate-episode`.
- `make dataset-dry-run` deliberately does *not* use the timestamped
  default — it passes a fixed `--dataset-id dry_run_dataset` so the smoke
  test always targets the same, easy-to-find location and is safe to
  re-run repeatedly without accumulating directories.
