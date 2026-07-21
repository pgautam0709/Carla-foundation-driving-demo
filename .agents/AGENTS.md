# Agent Workspace Rules — CARLA Foundation Driving Demo

These rules apply to all AI coding agents working in this repository.
Read this file before making any changes.

---

## 1. Project Phase Awareness

This project progresses through phases (0–6, plus 3.5). **Do not implement features from a future phase** unless explicitly instructed. Check `docs/PHASES.md` to understand what is in scope.

- Phase 0: Scaffold, config, diagnostics, docs — no model weights, no training loop
- Phase 1: Simulation bootstrap
- Phase 2: Data collection
- Phase 3: Dataset engineering and hardening — indexing, validation, splits, quality reporting. **No model weights, no training loop, no PyTorch `Dataset` here.**
- Phase 3.5 (current): Engineering loop over Phase 3's output — quality scoring, versioning, regression detection, coverage planning, review, training-readiness gates, HTML dashboard (`src/quality/`). Reads Phase 3 artifacts only; **still no model weights, no training loop, no PyTorch anywhere in this layer.** See `docs/ENGINEERING_LOOPS.md`.
- Phase 4: Model training (BC-CNN, PyTorch `Dataset`, trainer, `train.py`)
- Phase 5: Evaluation and explainability
- Phase 6: Deployment packaging

---

## 2. Before Making Any Code Change

1. **Read `docs/ARCHITECTURE.md`** — understand the component that owns the code you're touching.
2. **Read the existing module** — check docstrings, understand the contract.
3. **Check `config/default.yaml`** — never hardcode values that belong in config.
4. **Run `make diagnose`** — verify the environment is healthy.

---

## 3. Configuration Contract

- **All tunable parameters live in `config/`** — no magic numbers in `src/` or `scripts/`.
- Use `src/utils/config.load_config(profile=...)` to load config at entry points.
- Use `src/utils/config.get_nested(cfg, ...)` to safely access nested keys.
- Never read environment variables directly for simulation or training parameters — put them in config.

---

## 4. Coding Conventions

- Python 3.10 — use type hints on all public functions and class attributes.
- Line length: 100 characters (enforced by ruff).
- Imports: `from __future__ import annotations` at the top of every module.
- No bare `except:` — always catch specific exception types or `Exception`.
- Use `structlog` for all logging — never `print()` in library code (`src/`).
- `scripts/` may use `click.echo()` for user-facing CLI output.
- Prefer dataclasses over plain dicts for structured data.

---

## 5. CARLA-Specific Rules

- **Always use `CARLAClient` as a context manager** — never instantiate it outside `with`.
- **Register all spawned actors** via `client.register_actor(actor)` — this ensures cleanup on exit.
- **Always tick after spawning sensors** — sensors need at least one tick before data flows.
- **Drain the sensor queue after warm-up** — call `camera.drain()` before recording starts.
- Do not call `world.tick()` directly — always use `client.tick()`.

---

## 6. Dataset and Recording Rules

- Episode IDs must be unique and URL-safe (pattern: `ep_XXXX` or `ep_<uuid8>`).
- Use `EpisodeRecorder` as a context manager — never call `_open()` / `_close()` directly.
- Never write to `data/raw/` or `data/processed/` outside of `EpisodeRecorder` and dataset processors.
- HDF5 files must always embed the active config as JSON in `/metadata/config_json`.

---

## 7. Testing Rules

- Unit tests in `tests/unit/` must require **no external services** (no CARLA, no GPU, no network).
- Integration tests in `tests/integration/` must use the `skip_no_carla` fixture to auto-skip.
- Use `pytest.mark.integration` for all integration tests.
- Aim for ≥ 90% branch coverage on `src/utils/`.

---

## 8. Documentation Rules

- Every public function and class must have a docstring with Args, Returns, and Raises sections.
- Significant architectural decisions must be recorded in `docs/ADR/` as a new ADR.
- The `docs/PHASES.md` success criteria must be kept up to date as phases are completed.

---

## 9. Dependency Rules

- Add new runtime dependencies to `pyproject.toml` under the appropriate group (`sim`, `ml`, `dev`).
- Do not add ML dependencies to the base `dependencies` list — they belong in `[ml]`.
- Do not add CARLA-specific packages to `pyproject.toml` — document them in `docs/SETUP.md`.
- Run `make lint` and `make test` after any dependency change.

---

## 10. What Agents Must NOT Do

- Do not delete or modify `docs/PROJECT_BRIEF.md` — it is the source of truth for the project mission.
- Do not commit data files (`.hdf5`, `.pt`, `.onnx`) — they are gitignored for good reason.
- Do not add `print()` statements to `src/` — use `structlog`.
- Do not disable mypy or ruff for an entire file — use inline `# type: ignore[...]` or `# noqa: ...` with a comment explaining why.
- Do not implement Phase 2+ features in Phase 0 code.
