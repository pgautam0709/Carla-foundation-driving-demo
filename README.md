# CARLA Foundation Driving Demo

[![CI](https://github.com/your-org/carla-foundation-driving-demo/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/carla-foundation-driving-demo/actions)
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
CARLA Simulator ──► Data Collector ──► HDF5 Dataset ──► Training Loop
                                                              │
                                                              ▼
Evaluation Harness ◄──────────────── Trained Model ◄── Inference Engine
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for a full component diagram and data-flow description.

---

## Project Phases

| Phase | Goal | Status |
|-------|------|--------|
| **0** | Reproducible scaffold, diagnostics, docs | ✅ Complete |
| **1** | CARLA environment bootstrap & smoke test | 🔲 Planned |
| **2** | Data collection — RGB camera + autopilot | 🔲 Planned |
| **3** | Dataset engineering + behavioral cloning model | 🔲 Planned |
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
| PyTorch | ≥ 2.2 | Model training |

**Full setup guide:** [`docs/SETUP.md`](docs/SETUP.md)

---

## Repository Layout

```
├── config/          # YAML configuration (base + profiles)
├── data/            # Raw and processed datasets (gitignored)
├── docs/            # Architecture, setup, phase roadmap, ADRs
├── scripts/         # Entry-point scripts (diagnose, collect, train)
├── src/             # Source library
│   ├── agents/      # Driving agent implementations
│   ├── data/        # Episode recorder, dataset loader
│   ├── evaluation/  # Evaluation harness
│   ├── models/      # Model architectures
│   ├── sensors/     # Sensor configuration and capture
│   ├── simulation/  # CARLA client and world management
│   ├── training/    # Training loop
│   └── utils/       # Config loader, structured logging
└── tests/           # Unit and integration tests
```

---

## Developer Commands

```bash
make help        # List all commands
make setup       # Bootstrap environment
make diagnose    # Check all dependencies
make lint        # Run ruff linter
make type-check  # Run mypy
make test        # Unit tests (no CARLA needed)
make test-all    # All tests (requires CARLA server)
make collect     # Run data collection
make train       # Run training
```

---

## Operating Model

- **Product Owner / Chief Architect / Reviewer:** Human
- **Senior Staff Engineer / ML Engineer / DevOps / Test Engineer:** AI agents

> This is an AI engineering demonstration, not a production autonomous vehicle stack, certified safety system, or Level 4 autonomous driving product.

---

## License

MIT — see [LICENSE](LICENSE).
