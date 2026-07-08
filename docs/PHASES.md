# Project Phase Roadmap

## Overview

The project is structured as six phases, each building on the previous. Each phase produces concrete deliverables that a reviewer can inspect and verify independently.

---

## Phase Summary

| Phase | Name | Status | Key Deliverable |
|-------|------|--------|-----------------|
| **0** | Platform Scaffold | ✅ Complete | Diagnostics, config, docs, test skeleton |
| **1** | Simulation Bootstrap | ✅ Complete | Portable runtime, smoke test, 4 profiles, 58 unit tests |
| **2** | Data Collection | ✅ Complete | Expert episode pipeline: PNG frames + JSONL, 121 unit tests |
| **3** | Model Training | 🔲 Planned | Trained BC-CNN, TensorBoard logs |
| **4** | Evaluation & XAI | 🔲 Planned | Closed-loop metrics, attention maps |
| **5** | Deployment Packaging | 🔲 Planned | ONNX + TensorRT export, inference script |

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

## Phase 3 — Model Training

**Goal:** Train a behavioural cloning model that maps front-camera images to vehicle controls.

**Deliverables:**
- `src/models/bc_cnn.py` — BC-CNN architecture (ResNet backbone + MLP head)
- `src/data/dataset.py` — PyTorch Dataset/DataLoader for HDF5 episodes
- `src/training/trainer.py` — training loop with validation, checkpointing, TensorBoard
- `scripts/train.py` — fully implemented training entry point
- Trained checkpoint achieving < 0.05 mean absolute error on steering

**Success Criteria:**
- Model trains to convergence without NaN loss
- Validation MAE (steer) < 0.05
- TensorBoard shows smooth loss curve

---

## Phase 4 — Evaluation & Explainability

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

## Phase 5 — Deployment Packaging

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
