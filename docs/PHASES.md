# Project Phase Roadmap

## Overview

The project is structured as six phases, each building on the previous. Each phase produces concrete deliverables that a reviewer can inspect and verify independently.

---

## Phase Summary

| Phase | Name | Status | Key Deliverable |
|-------|------|--------|-----------------|
| **0** | Platform Scaffold | ✅ Complete | Diagnostics, config, docs, test skeleton |
| **1** | Simulation Bootstrap | ✅ Complete | Portable runtime, smoke test, 4 profiles, 58 unit tests |
| **2** | Data Collection | 🔲 Planned | HDF5 dataset from autopilot driving |
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

**Goal:** Collect a high-quality labelled driving dataset using the CARLA autopilot.

**Deliverables:**
- `scripts/collect_data.py` — multi-episode collection runner (implemented)
- Data processing pipeline: raw HDF5 → normalised + split dataset
- Dataset statistics report (frame count, speed distribution, steering distribution)
- At least 10,000 labelled frames for training

**Success Criteria:**
- Dataset contains ≥ 10,000 frames across ≥ 5 maps and ≥ 3 weather conditions
- No corrupt HDF5 files
- Dataset statistics are plausible (no all-zero steering, reasonable speed range)

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
