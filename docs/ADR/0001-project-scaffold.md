# ADR 0001 — Project Scaffold Architecture Decisions

**Date:** 2026-07-06
**Status:** Accepted
**Deciders:** Product Owner + AI Engineering Team

---

## Context

Phase 0 establishes the foundational architectural choices for the entire project. Decisions made here affect every subsequent phase. This record documents the rationale for each significant choice so future contributors understand why the project is structured this way, and can make informed decisions about when to deviate.

---

## Decisions

### 1. Python 3.10 as Target Version

**Decision:** Pin Python 3.10.

**Rationale:**
- CARLA 0.9.15 distributes pre-built Python wheels for cp310.
- PyTorch 2.x fully supports 3.10 with CUDA 11.8 and 12.x wheels.
- Structural Pattern Matching (`match`/`case`) and improved type hints are available.
- Python 3.11+ introduces minor API changes that break some CARLA internals.

**Consequences:**
- CI and developer environments must use exactly 3.10.
- `.python-version` file ensures pyenv/uv pick the correct interpreter automatically.

---

### 2. `uv` as Package Manager

**Decision:** Use `uv` (Astral) instead of `pip` + `setuptools` + `requirements.txt`.

**Rationale:**
- `uv` resolves and installs dependencies 10–100× faster than pip.
- Lockfile (`uv.lock`) ensures byte-for-byte reproducibility across machines without manually pinning every transitive dependency.
- Compatible with `pyproject.toml` (PEP 517/621), the modern Python packaging standard.
- `pip install -e .` remains available as a fallback for environments without `uv`.

**Consequences:**
- Developers must install `uv` before running `make setup`.
- `uv` is not available in all corporate environments; the `Makefile` falls back to `pip`.

---

### 3. `src/` Package Layout

**Decision:** Place all library code under `src/carla_driving/` (via `src/` layout).

**Rationale:**
- Forces the project to be installed in editable mode (`pip install -e .`) before importing, which catches import errors that would otherwise be silently masked by the working directory being on `sys.path`.
- Separates importable library code (`src/`) from scripts, tests, and configuration.
- Follows the recommendation of the Python Packaging Authority and `hatchling`.

**Consequences:**
- Scripts that run from the repo root must either install the package or add `src/` to `sys.path`. Current scripts use `sys.path.insert(0, ...)` as a bootstrap.

---

### 4. YAML Configuration with Profile Overrides

**Decision:** Single `config/default.yaml` with profile overrides via deep-merge.

**Rationale:**
- All hyperparameters, paths, and simulation settings in one place — no hunting across code files.
- Profile system allows the same codebase to behave differently on a developer laptop (`local_dev`), a GPU training node (`linux_gpu`), and CI (`ci`) without environment variables or code branches.
- Deep-merge means profiles only need to specify the keys that differ; everything else inherits the default.
- YAML is human-readable and widely understood; no custom DSL.

**Rejected alternatives:**
- `.env` files: too flat; poor for nested simulation config.
- Python config files: too easy to accidentally execute code.
- Hydra: excellent but heavy dependency; adds complexity before the team has established patterns.

---

### 5. HDF5 for Dataset Storage

**Decision:** Record each episode as a single compressed HDF5 file using `h5py`.

**Rationale:**
- Storing tens of thousands of PNG images creates OS-level inode pressure and degrades filesystem performance.
- HDF5 provides random access by frame index — essential for shuffled DataLoader batching.
- Built-in `gzip` compression reduces storage by 40–60% for RGB frames.
- Metadata (config, timestamps, episode ID) is embedded alongside the data in the same file — no separate sidecar JSON needed.
- `h5py` is mature, well-documented, and compatible with PyTorch DataLoader via `__getitem__`.

**Rejected alternatives:**
- Individual PNG + CSV: poor random access, inode pressure.
- TFRecord: TensorFlow-specific; complicates pure PyTorch pipelines.
- Zarr: excellent but less battle-tested for image + telemetry mixed data.

---

### 6. CARLA Synchronous Mode

**Decision:** Always enable synchronous mode for data collection and evaluation.

**Rationale:**
- In asynchronous mode, the CARLA server advances at its own pace. If the client is slow (e.g., saving to disk), frames are silently dropped, creating temporal gaps in the dataset.
- These gaps corrupt sequential models (transformers, RNNs) and cause label misalignment between the camera frame and the vehicle control.
- Synchronous mode guarantees `tick()` advances the simulation by exactly one `fixed_delta_seconds`, providing a perfectly uniform 20 Hz data stream.

**Consequences:**
- The client must call `tick()` regularly or the server stalls. All collection loops must use a tight tick loop.
- Parallel collections on the same server are harder; not in scope for this phase.

---

### 7. structlog for Logging

**Decision:** Use `structlog` instead of the standard `logging` module.

**Rationale:**
- Structured key-value log events (`log.info("sensor.spawned", width=640, height=480)`) are machine-parseable without regex.
- Switching from human-readable console output (`local_dev`) to JSON output (`linux_gpu`, CI) is a single config flag — no code change.
- Compatible with the standard `logging` module for library compatibility.

**Consequences:**
- `structlog` is an additional dependency.
- All log call sites use keyword arguments rather than f-strings — a minor style adjustment.
