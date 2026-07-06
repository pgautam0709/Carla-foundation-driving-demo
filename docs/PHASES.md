# Project Phase Roadmap

## Overview

The project is structured as six phases, each building on the previous. Each phase produces concrete deliverables that a reviewer can inspect and verify independently.

---

## Phase Summary

| Phase | Name | Status | Key Deliverable |
|-------|------|--------|-----------------|
| **0** | Platform Scaffold | ✅ Complete | Diagnostics, config, docs, test skeleton |
| **1** | Simulation Bootstrap | 🔲 Planned | CARLA smoke test, map loading, NPC traffic |
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

**Goal:** Prove end-to-end CARLA connectivity and spawn a working simulation with traffic.

**Deliverables:**
- `scripts/smoke_test.py` — connect, spawn vehicle, run 100 ticks, report FPS
- NPC traffic spawning utilities in `src/simulation/`
- Weather and map randomisation helpers
- Integration tests that pass with a live CARLA server

**Success Criteria:**
- Smoke test connects to CARLA 0.9.15 and runs 100 synchronous ticks without error
- FPS ≥ 15 on the target hardware

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
