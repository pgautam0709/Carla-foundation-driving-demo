# ADR 0003 — Phase 3b Dataset Hardening Design Decisions

**Date:** 2026-07-20
**Status:** Accepted
**Deciders:** AI Engineering Team

---

## Context

Phase 3a produced a deterministic, quality-checked dataset index but only
checked structural quality (validity, alignment). It said nothing about
whether the *content* of a dataset was trustworthy: whether steering inputs
looked plausible, whether the vehicle ever got stuck, whether the same
frame was accidentally recorded twice, or whether the steering-angle
distribution was wildly skewed. Phase 3b adds four checks addressing this,
scoped explicitly to stop short of model training — no PyTorch, no
BC-CNN, no trainer, no `train.py`. This ADR records the decisions made in
[`src/data/dataset_outliers.py`](../../src/data/dataset_outliers.py),
[`src/data/dataset_duplicates.py`](../../src/data/dataset_duplicates.py),
the steering histogram in
[`src/data/dataset_statistics.py`](../../src/data/dataset_statistics.py),
and the `--fix-manifest` addition to
[`src/data/validation.py`](../../src/data/validation.py).

---

## Decisions

### 1. All Phase 3b findings are informational; none exclude data

**Decision:** Outlier detection (steering spikes, stuck throttle) and
duplicate-frame detection never set `included: false` on an episode or omit
a sample from `samples_index.jsonl`. They only append `QualityIssue`
entries and summary counters (`episodes_with_outliers`,
`duplicate_frame_groups`) to `quality_report.json`.

**Rationale:**
- Phase 3a's own closure review found and fixed a case where a hardening
  check (alignment truncation) silently changed which data was
  included, with no way to require strict behavior. The fix there was to
  make exclusion explicit and opt-in (`allow_partial_alignment`). Phase 3b
  avoids reintroducing a similar risk by not giving these two brand-new
  heuristics any exclusion power at all — a mis-tuned threshold here
  (e.g. `steering_spike_delta` set too low) degrades to "some noisy
  warnings," not "the dataset silently lost 30% of its samples."
- These are genuinely new, unvalidated-in-production heuristics (unlike
  alignment, which mirrors a check Phase 2's own validator already made).
  Withholding exclusion power until they've proven themselves against real
  collected data is the more conservative default.

**Consequences:**
- A dataset with severe stuck-throttle episodes or thousands of duplicate
  frames still builds successfully; a human (or a future Phase 4 script)
  must act on the warnings deliberately. This phase provides visibility,
  not enforcement.

### 2. Duplicate detection is exact (byte-hash) only — no perceptual near-duplicate detection

**Decision:** `find_duplicate_frames()` groups samples by the SHA-256 hash
of their frame file's raw bytes. It does not decode images or compare
pixel similarity.

**Rationale:**
- Exact hashing needs only `hashlib` — no new dependency, consistent with
  every other Phase 3 module's "no CARLA, Docker, GPU, or PyTorch" rule.
- Near-duplicate detection (perceptually similar but not identical frames)
  requires decoding PNG pixel data and computing something like an average
  hash or SSIM — which needs Pillow or OpenCV. Both already exist in this
  project's optional `sim` dependency group (used by the *live* CARLA
  collection path), but adding either as a hard dependency of the dataset
  *engineering* path would mean `build_dataset.py` — which has run without
  CARLA, Docker, or GPU access since Phase 3a — would stop working on a
  bare base install.

**Rejected alternatives:**
- Making the image library an optional soft dependency (attempt import,
  skip gracefully if absent): considered, but deferred. It adds real
  complexity (a second code path, a second set of tests, a threshold for
  "how similar counts as near-duplicate") for a capability nobody has
  asked for yet. Exact-match detection already catches the most common and
  highest-signal case — a frozen camera or an accidental frame double-write
  — without that complexity. Documented as a known limitation rather than
  silently declared "done."

**Consequences:**
- Two frames that are visually identical but differ by even one bit (e.g.
  re-encoded, or captured a frame apart during a dead stop) are not
  detected as duplicates. This is an accepted gap, not an oversight.

**Future enhancement:** Perceptual near-duplicate detection can be added
later using image hashing or embeddings, but Phase 3b intentionally avoids
image-processing dependencies. Until then, every field, log message, and
CLI string produced by this feature — `duplicate_frame_groups`, the
`quality_report.json` issue text, `inspect_dataset.py`'s output — describes
it as **exact duplicate detection**, never "near-duplicate," since that
would misstate what is actually implemented.

### 3. Dry-run synthetic episodes are expected to report large duplicate counts

**Decision:** No special-casing was added to suppress or discount duplicate
findings against `--dry-run`-collected episodes.

**Rationale:**
- Phase 2's dry-run mode (`scripts/collect_expert_episode.py`,
  `_make_black_png`) deliberately emits a solid black placeholder PNG for
  every tick, by design (documented in Phase 2 ADR-002: a pure-stdlib PNG
  encoder for testing without CARLA). Every dry-run frame, in every
  dry-run episode, is therefore byte-identical to every other dry-run
  frame of the same dimensions.
- Detecting this honestly — a real, large duplicate-frame group — is the
  *correct* behavior of the detector, not a bug to work around. Silently
  exempting dry-run episodes would mean the detector's behavior on
  synthetic test data no longer matches its behavior on real data, making
  it harder to trust the tests that exercise it.

**Consequences:**
- Running `make dataset-dry-run` against accumulated dry-run episodes
  reports a large `duplicate_frame_groups` finding. This is documented
  prominently (see docs/PHASE3B_DATASET_HARDENING.md) so it reads as
  expected behavior, not an alarm.
- Test fixtures for "normal" episodes (`_write_episode` in
  `tests/unit/test_dataset_engineering.py`) were updated to vary frame
  content per tick specifically so that ordinary tests aren't
  incidentally exercising the duplicate-detection path — tests that *do*
  want a duplicate construct one explicitly.

### 4. Steering-spike detection uses the tick-to-tick derivative, not an absolute magnitude

**Decision:** `check_outliers()` flags `|steer[t] - steer[t-1]| >
steering_spike_delta`.

**Rationale:** A large steering angle by itself is unremarkable (sharp
turns exist); an instantaneous jump between ticks 50ms apart is not,
because steering does not teleport. Flagging on the derivative catches
control glitches without penalizing legitimately sharp cornering.

### 5. Stuck-throttle detection requires two signals (throttle AND speed), not one

**Decision:** A run only counts as stuck-throttle when throttle stays at or
above `stuck_throttle_min` **and** speed stays at or below
`stuck_speed_max_kph` for `stuck_throttle_min_ticks` consecutive ticks.

**Rationale:** Full throttle alone is ordinary (highway acceleration,
overtaking). The actual anomaly is commanding full throttle while not
moving — wedged against an obstacle, wheels spinning. Requiring both
signals together avoids flagging normal high-throttle driving.

### 6. `write_validation_status()` was added to the existing `validation.py`, not a new module

**Decision:** The `--fix-manifest` follow-through lives in
`src/data/validation.py` alongside `EpisodeValidator`, not in a new Phase 3
module.

**Rationale:** This was pre-committed in Phase 2's own documentation —
`docs/PHASE2_DATA_COLLECTION.md` ADR-003 states plainly that collection
always writes `"unchecked"` and "the validator updates it only when
`--fix-manifest` is passed (Phase 3 feature)." Honoring that by extending
the validator itself, rather than introducing a parallel Phase 3 module
that also knows how to read and rewrite `manifest.json`, keeps a single
source of truth for what "valid" means and how that gets recorded.

**Consequences:** `scripts/validate_episode.py` is the only caller of
`write_validation_status()` in this phase — `dataset_builder.py`
deliberately does not call it during a dataset build, since a build reads
many episodes' existing manifests but has no reason to rewrite them; fixing
manifests remains an explicit, one-episode-at-a-time operator action.
