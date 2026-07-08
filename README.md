# CARLA Foundation Driving Demo

[![CI](https://github.com/pgautam0709/Carla-foundation-driving-demo/actions/workflows/ci.yml/badge.svg)](https://github.com/pgautam0709/Carla-foundation-driving-demo/actions)
[![Python 3.10](https://img.shields.io/badge/python-3.10-blue.svg)](https://www.python.org/downloads/release/python-3100/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **A credible autonomous driving AI engineering demo** — demonstrating the full lifecycle of an end-to-end driving model: environment bootstrap → data collection → dataset engineering → model training → inference → evaluation → explainability → deployment packaging.

---

## ⚡ Quick Start (3 commands)

```bash
# 1. Bootstrap the environment
make setup

# 2. Run diagnostics — see what's installed, what's missing, and why
make diagnose

# 3. Run unit tests (no CARLA required)
make test
```

---

## Architecture

```
CARLA Simulator ──► ExpertDriver ──► Episode Directories (PNG + JSONL)
                                              │
                                              ▼
                                     Dataset Builder
                                     (index · split · quality report)
                                              │
                                              ▼
                               Training Loop (Phase 3b, planned)
                                              │
                                              ▼
Evaluation Harness ◄────────────── Trained Model ◄── Inference Engine
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for a full component diagram and data-flow description.

---

## Project Phases

| Phase | Goal | Status |
|-------|------|--------|
| **0** | Reproducible scaffold, diagnostics, docs | ✅ Complete |
| **1** | CARLA environment bootstrap & smoke test | ✅ Complete |
| **2** | Expert data collection — RGB camera + autopilot | ✅ Complete |
| **3a** | Dataset engineering — index, validate, split, quality report | ✅ Complete |
| **3b** | Behavioural cloning model (BC-CNN) | 🔲 Planned |
| **4** | Closed-loop evaluation + explainability | 🔲 Planned |
| **5** | Deployment packaging (ONNX/TensorRT) | 🔲 Planned |

Full phase details: [`docs/PHASES.md`](docs/PHASES.md)

---

## Prerequisites

| Dependency | Version | Required For |
|-----------|---------|-------------|
| Python | ≥ 3.10 | Everything |
| uv | latest | Package management |
| CARLA Server | 0.9.15 | Simulation, data collection |
| CUDA Toolkit | ≥ 11.8 | GPU training (optional for CPU) |
| PyTorch | ≥ 2.2 | Model training (Phase 3b+) |

**Full setup guide:** [`docs/SETUP.md`](docs/SETUP.md)

---

## Repository Layout

```
├── config/                        # YAML configuration (base + runtime profiles)
│   └── profiles/                  # macos_docker, windows_local, linux_local, remote_carla
├── data/                          # Raw and processed datasets (gitignored)
│   ├── raw/episodes/              # Phase 2 episode directories (PNG + JSONL)
│   └── processed/                 # Phase 3a dataset index, splits, quality report
├── docs/                          # Architecture, setup, phase roadmap, ADRs
│   └── ADR/                       # Architectural Decision Records
├── scripts/                       # Entry-point scripts
│   ├── diagnose.py                # Dependency health check
│   ├── smoke_test.py              # Phase 1 CARLA connectivity test
│   ├── collect_expert_episode.py  # Phase 2 data collection (+ --dry-run)
│   ├── validate_episode.py        # Phase 2 episode validation CLI
│   ├── build_dataset.py           # Phase 3a dataset builder CLI
│   └── inspect_dataset.py         # Phase 3a dataset inspector CLI
├── src/                           # Source library
│   ├── data/                      # Episode writers, schemas, validators, dataset pipeline
│   ├── simulation/                # CARLA client, expert driver
│   ├── models/                    # Model architectures (Phase 3b)
│   ├── training/                  # Training loop (Phase 3b)
│   ├── evaluation/                # Evaluation harness (Phase 4)
│   └── utils/                     # Config loader, structured logging
└── tests/                         # Unit and integration tests
```

---

## Developer Commands

```bash
make help              # List all commands
make setup             # Bootstrap Python environment
make diagnose          # Check all dependencies
make lint              # Run ruff linter
make type-check        # Run mypy
make test              # Unit tests (no CARLA needed)  — 157 passing

# Phase 1 — CARLA connectivity
make smoke             # Connect to CARLA, run 100 ticks (requires CARLA server)
make carla-docker      # Start CARLA via Docker

# Phase 2 — Data collection
make collect-dry-run   # Generate a synthetic episode (no CARLA required)
make validate-episode  # Validate the most recent episode

# Phase 3a — Dataset engineering
make dataset-dry-run   # Build dataset from a dry-run episode (no CARLA required)
make build-dataset     # Build dataset index from all Phase 2 episodes
make inspect-dataset   # Print dataset summary and quality report
```

---

## Operating Model

- **Product Owner / Chief Architect / Reviewer:** Human
- **Senior Staff Engineer / ML Engineer / DevOps / Test Engineer:** AI agents

> This is an AI engineering demonstration, not a production autonomous vehicle stack, certified safety system, or Level 4 autonomous driving product.

---

## License

MIT — see [LICENSE](LICENSE).
