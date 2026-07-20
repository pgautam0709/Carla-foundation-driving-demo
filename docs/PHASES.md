# Project Phase Roadmap

## Overview

The project is structured as seven phases, each building on the previous. Each phase produces concrete deliverables that a reviewer can inspect and verify independently.

---

## Phase Summary

| Phase | Name | Status | Key Deliverable |
|-------|------|--------|-----------------|
| **0** | Platform Scaffold | ✅ Complete | Diagnostics, config, docs, test skeleton |
| **1** | Simulation Bootstrap | ✅ Complete | Portable runtime, smoke test, 4 profiles, 58 unit tests |
| **2** | Data Collection | ✅ Complete | Expert episode pipeline: PNG frames + JSONL, 121 unit tests |
| **3** | Dataset Engineering | ✅ Complete | 3a Dataset Engineering ✅ · 3b Dataset Hardening ✅ |
| **4** | Model Training | 🔲 Planned | Trained BC-CNN, TensorBoard logs |
| **5** | Evaluation & XAI | 🔲 Planned | Closed-loop metrics, attention maps |
| **6** | Deployment Packaging | 🔲 Planned | ONNX + TensorRT export, inference script |

**Note:** Phase 3 is dataset engineering and hardening only — it never
produces a trained model. Behavioural cloning training is entirely Phase 4.

---

## Phase 0 — Platform Scaffold

**Goal:** Create a reproducible AI engineering platform scaffold with diagnostics, documentation, config separation, and agent-ready task structure.

**Deliverables:**
- `scripts/diagnose.py` — self-contained dependency health check
- `config/` — YAML-based configuration with profile overrides
- `src/` — source library skeleton (simulation, sensors, agents, data, utils)
- `tests/unit/` — unit tests for config loader
- `docs/` — README, SETUP, ARCHITECTURE, PHASES, ADR
- `.github/workflows/ci.yml` — lint + type-check + unit test CI

**Success Criteria:**
- `make diagnose` produces a readable report on a clean machine
- `make lint` passes with no errors
- `make test` runs and all unit tests pass
- A new contributor can set up the environment following SETUP.md in < 30 minutes

---

## Phase 1 — Simulation Bootstrap

**Status: ✅ Complete — commit `57e5978`**

**Goal:** Prove end-to-end CARLA connectivity across all supported runtimes
(macOS/Docker, Windows native, Linux native, remote server) without hardcoding
any platform-specific paths in source code.

**Deliverables:**
- [`scripts/smoke_test.py`](smoke_test.py) — connect to CARLA, spawn ego vehicle, run 100 synchronous ticks, report Hz
- [`scripts/start_carla_docker.sh`](../scripts/start_carla_docker.sh) — Docker launcher with Apple Silicon (Rosetta 2) warnings
- [`scripts/start_carla_windows.ps1`](../scripts/start_carla_windows.ps1) — Windows native CARLA launcher
- [`src/utils/config.py`](../src/utils/config.py) — `apply_env_overrides()`: `CARLA_HOST`/`PORT`/`VERSION`/`API_PATH` always win over YAML
- [`src/utils/runtime.py`](../src/utils/runtime.py) — `is_apple_silicon()`, `build_docker_command()`, error message formatters
- `config/default.yaml` restructured: new `carla_connection:` and `runtime:` sections
- 4 new runtime profiles: `macos_docker`, `windows_local`, `linux_local`, `remote_carla`
- [`docs/RUNTIME_PROFILES.md`](RUNTIME_PROFILES.md) — profile selection guide with decision tree
- [`docs/PHASE1_SMOKE_TEST.md`](PHASE1_SMOKE_TEST.md) — step-by-step smoke test guide for all 4 runtimes
- [`tests/unit/test_runtime.py`](../tests/unit/test_runtime.py) — 41 new tests (6 classes)
- `Makefile` — new `make smoke`, `make carla-docker`, `make carla-windows-help` targets

**Verified Results:**

| Check | Result |
|-------|--------|
| `make lint` | ✅ All checks passed |
| `make test` | ✅ **58/58 unit tests pass** (0.14s, no CARLA required) |
| `make diagnose --profile macos_docker` | ✅ 24 OK · 5 WARN · **0 FAIL** |

**Success Criteria:**
- ✅ Smoke test connects to CARLA 0.9.15 and runs 100 synchronous ticks without error
- ✅ Hz ≥ 15 on native hardware (Docker/emulation will be lower — documented)
- ✅ Switching runtimes (macOS ↔ Windows ↔ Linux ↔ remote) requires only profile/env var changes — no source edits
- ✅ `make diagnose` never FAILs on macOS when CARLA is not running

---

## Phase 2 — Data Collection

**Status: ✅ Complete**

**Goal:** Build a production-quality data collection pipeline that generates
high-quality autonomous driving training data in a disciplined, reproducible
format — answering the engineering question:

> *Can this platform generate high-quality autonomous driving training data in a
> disciplined, reproducible format?*

**Deliverables:**
- [`src/data/schemas.py`](../src/data/schemas.py) — `ControlRecord`, `TelemetryRecord`, `EventRecord`, `EpisodeMetadata`, `RouteDefinition`, `EpisodeManifest` (schema v2.0)
- [`src/data/episode.py`](../src/data/episode.py) — deterministic episode IDs, route hashing, git context, `EpisodeDirectory` path layout
- [`src/data/writers.py`](../src/data/writers.py) — `JSONLWriter`, `FrameWriter`, `EpisodeWriter` context manager
- [`src/data/validation.py`](../src/data/validation.py) — `EpisodeValidator`: 14 checks, fully CARLA-free
- [`src/simulation/expert_driver.py`](../src/simulation/expert_driver.py) — `ExpertDriver` wrapping Traffic Manager autopilot with Phase 3 sensor stubs
- [`scripts/collect_expert_episode.py`](../scripts/collect_expert_episode.py) — Click CLI: `--dry-run` + live CARLA modes
- [`scripts/validate_episode.py`](../scripts/validate_episode.py) — standalone episode validation CLI
- `config/default.yaml` — new `expert_collection:` section
- `Makefile` — `make collect-dry-run`, `make validate-episode` targets
- [`docs/PHASE2_DATA_COLLECTION.md`](PHASE2_DATA_COLLECTION.md) — full schema reference and developer guide
- [`tests/unit/test_episode.py`](../tests/unit/test_episode.py) — 36 new unit tests (9 test classes)

**Episode directory layout:**
```
data/raw/episodes/<episode_id>/
    metadata.json    ← provenance: runtime, sensors, git commit
    route.json       ← start/end transforms, route hash
    controls.jsonl   ← throttle, steer, brake, gear per tick
    telemetry.jsonl  ← location, velocity, speed_kph per tick
    events.jsonl     ← episode_started, collision, completed …
    manifest.json    ← file inventory, counts, validation status
    frames/front_camera/000000.png … 000499.png
```

**Verified Results:**

| Check | Result |
|-------|--------|
| `make lint` | ✅ All checks passed |
| `make test` | ✅ **121/121 unit tests pass** (0.39s, no CARLA required) |
| `make collect-dry-run` | ✅ 500 frames · 500 controls · 500 telemetry rows |
| `make validate-episode` | ✅ **14/14 checks passed** |

**Success Criteria:**
- ✅ `make collect-dry-run` generates a complete, valid episode without CARLA
- ✅ `make validate-episode` passes 14 checks on the generated episode
- ✅ Episode ID is deterministic, URL-safe, and self-describing
- ✅ All records are JSON-serialisable with schema version embedded
- ✅ No CARLA, Docker, or GPU required for the test suite

**Phase 3 extension points (already stubbed):**
- `ExpertDriver.attach_collision_sensor()` — will record `collision` events
- `ExpertDriver.attach_lane_sensor()` — will record `lane_invasion` events
- `validate_episode.py --fix-manifest` — will update `validation_status`

---

## Phase 3 — Dataset Engineering

**Goal:** Turn Phase 2's episode directories into a deterministic,
quality-checked, indexed dataset. **This phase does not train a model** —
no PyTorch, no BC-CNN, no trainer, no `train.py`. That is Phase 4.

### Phase 3a — Dataset Engineering

**Status: ✅ Complete**

**Goal:** Turn a directory of Phase 2 episodes — of varying length and
quality — into one deterministic, quality-checked dataset that training can
trust, without writing any training code yet.

**Deliverables:**
- [`src/data/dataset_schemas.py`](../src/data/dataset_schemas.py) — `EpisodeIndexEntry`, `SampleRecord`, `DatasetStatistics`, `QualityReport`, `DatasetManifest` (schema v1.0)
- [`src/data/dataset_discovery.py`](../src/data/dataset_discovery.py) — episode directory discovery
- [`src/data/dataset_alignment.py`](../src/data/dataset_alignment.py) — frame/control/telemetry alignment checking with truncation to a usable prefix
- [`src/data/dataset_splits.py`](../src/data/dataset_splits.py) — deterministic per-episode train/val/test split assignment
- [`src/data/dataset_statistics.py`](../src/data/dataset_statistics.py) — aggregate signal statistics
- [`src/data/dataset_builder.py`](../src/data/dataset_builder.py) — orchestrates the full build
- [`scripts/build_dataset.py`](../scripts/build_dataset.py) / [`scripts/inspect_dataset.py`](../scripts/inspect_dataset.py) — CLIs
- `config/default.yaml` — new `dataset_engineering:` section
- `Makefile` — `make build-dataset`, `make inspect-dataset`, `make dataset-dry-run`
- [`docs/PHASE3_DATASET_ENGINEERING.md`](PHASE3_DATASET_ENGINEERING.md) — full artifact reference and design decisions
- [`docs/ADR/0002-dataset-engineering-design.md`](ADR/0002-dataset-engineering-design.md)
- [`tests/unit/test_dataset_engineering.py`](../tests/unit/test_dataset_engineering.py) — 54 new unit tests (8 test classes)

**Verified Results:**

| Check | Result |
|-------|--------|
| `make lint` | ✅ All checks passed |
| `make type-check` | ✅ No issues in the new modules |
| `make test` | ✅ **175/175 unit tests pass** (no CARLA required) |
| `make dataset-dry-run` | ✅ Generates an episode, builds the dataset, prints the summary |

**Success Criteria:**
- ✅ `make build-dataset` produces a new versioned directory (`data/processed/datasets/<dataset_id>/`) containing `dataset_manifest.json`, `episodes_index.jsonl`, `samples_index.jsonl`, `stats.json`, `quality_report.json`, and `splits/{train,val,test}.jsonl` from any set of Phase 2 episodes, without overwriting prior builds
- ✅ Alignment is strict by default — a misaligned episode is excluded, not silently truncated; `--allow-partial-alignment` opts into truncation, and either way the outcome is recorded in the quality report
- ✅ Splits are assigned per episode (not per sample) via a deterministic batch algorithm; rebuilding the same episodes with the same seed reproduces identical split assignments, and `train` is never left empty when samples exist, even for a 1-2 episode dataset
- ✅ Misaligned, invalid, or split-coverage issues are reported in the quality report and surfaced by `inspect_dataset.py`, not silently dropped or included
- ✅ No CARLA, Docker, GPU, or PyTorch dependency anywhere in this layer
- ✅ Phase 0, Phase 1, and Phase 2 behavior is unchanged

### Phase 3b — Dataset Hardening

**Status: ✅ Complete**

**Goal:** Improve dataset quality and trustworthiness further — **still no
model training code**.

**Deliverables:**
- [`src/data/dataset_outliers.py`](../src/data/dataset_outliers.py) — steering-spike and stuck-throttle detection (informational only)
- [`src/data/dataset_duplicates.py`](../src/data/dataset_duplicates.py) — exact (byte-hash) duplicate frame detection, within and across episodes
- [`src/data/dataset_io.py`](../src/data/dataset_io.py) — shared JSONL helper (deduplicated from `dataset_builder.py`)
- Steering-angle histogram in `stats.json` (`src/data/dataset_statistics.py`) — informational class-balance reporting, no resampling
- `write_validation_status()` in [`src/data/validation.py`](../src/data/validation.py) + `--fix-manifest` flag on [`scripts/validate_episode.py`](../scripts/validate_episode.py)
- `config/default.yaml` — new `outlier_detection:` / `duplicate_detection:` / `steering_histogram_bins` keys
- `Makefile` — `make fix-manifest`
- [`docs/PHASE3B_DATASET_HARDENING.md`](PHASE3B_DATASET_HARDENING.md)
- [`docs/ADR/0003-dataset-hardening-design.md`](ADR/0003-dataset-hardening-design.md)
- 28 new unit tests across `TestOutlierDetection`, `TestDuplicateDetection`, extended `TestStatistics`, extended `TestDatasetBuilder`/`TestQualityReport`/CLI tests, and `TestFixManifest`

**Verified Results:**

| Check | Result |
|-------|--------|
| `make lint` | ✅ All checks passed |
| `make type-check` | ✅ No issues in any Phase 3 file |
| `make test` | ✅ **203/203 unit tests pass** (no CARLA required) |
| `make dataset-dry-run` | ✅ Hardening checks run by default, end to end |

**Success Criteria:**
- ✅ Outlier and duplicate findings are visible in `quality_report.json` / `inspect_dataset.py` and never exclude data
- ✅ Steering histogram is informational only — nothing in this codebase resamples or reweights based on it
- ✅ `--fix-manifest` writes `validation_status` back without disturbing other manifest fields
- ✅ No CARLA, Docker, GPU, or PyTorch dependency anywhere in this layer
- ✅ Phase 0, 1, 2, and 3a behavior is unchanged

**Known limitation carried forward:** duplicate detection is exact
(byte-hash) only; near-duplicate (perceptually similar) detection was
deliberately not implemented to avoid adding an image-processing dependency
to a path that has been dependency-light since Phase 3a — see
`docs/PHASE3B_DATASET_HARDENING.md` and ADR 0003.

---

## Phase 4 — Model Training

**Goal:** Train a behavioural cloning model that maps front-camera images to vehicle controls.

**Status: 🔲 Planned**

**Deliverables:**
- `src/models/bc_cnn.py` — BC-CNN architecture (ResNet backbone + MLP head)
- `src/data/dataset.py` — PyTorch `Dataset`/`DataLoader` reading `splits/{train,val,test}.jsonl` (produced by Phase 3)
- `src/training/trainer.py` — training loop with validation, checkpointing, TensorBoard
- `scripts/train.py` — fully implemented training entry point
- Trained checkpoint achieving < 0.05 mean absolute error on steering

**Success Criteria:**
- Model trains to convergence without NaN loss
- Validation MAE (steer) < 0.05
- TensorBoard shows smooth loss curve

---

## Phase 5 — Evaluation & Explainability

**Goal:** Quantitatively evaluate the model in closed-loop simulation and explain its decisions.

**Deliverables:**
- `src/evaluation/harness.py` — closed-loop evaluation runner
- Metrics: route completion %, collision rate, average speed, jerk
- `src/evaluation/xai.py` — GradCAM attention map visualiser
- Evaluation report comparing trained model vs. autopilot baseline

**Success Criteria:**
- Route completion ≥ 60% on Town03 test routes
- Collision rate ≤ 2 per km
- Attention maps visually highlight road and lane markings

---

## Phase 6 — Deployment Packaging

**Goal:** Package the trained model for inference-time deployment.

**Deliverables:**
- ONNX export of the trained model
- TensorRT optimised engine (Linux + CUDA only)
- `scripts/infer.py` — real-time inference script (camera → control in < 50ms)
- Deployment README with performance benchmarks

**Success Criteria:**
- ONNX model produces identical outputs to PyTorch model (< 1e-4 max diff)
- TensorRT inference latency < 20ms on RTX 30xx or better
- `scripts/infer.py` runs at ≥ 20 FPS
