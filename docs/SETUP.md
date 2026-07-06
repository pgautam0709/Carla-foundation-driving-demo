# Environment Setup Guide

This guide walks through setting up the full development environment from scratch on both macOS (development) and Ubuntu 22.04 LTS (training/simulation).

---

## Prerequisites at a Glance

| Dependency | Version | Purpose |
|-----------|---------|---------|
| Python | 3.10.x | Core runtime |
| uv | latest | Package management |
| CARLA | 0.9.15 | Simulation server |
| CUDA Toolkit | ≥ 11.8 | GPU training (Linux only) |
| Git | ≥ 2.40 | Version control |

---

## Step 1 — Install Python 3.10

### macOS (using pyenv)

```bash
# Install pyenv if needed
brew install pyenv

# Install Python 3.10
pyenv install 3.10.14
pyenv global 3.10.14

# Verify
python --version   # Python 3.10.14
```

### Ubuntu 22.04

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3.10-dev

# Verify
python3.10 --version
```

---

## Step 2 — Install uv (Package Manager)

```bash
curl -Ls https://astral.sh/uv/install.sh | sh
source ~/.bashrc   # or ~/.zshrc

# Verify
uv --version
```

---

## Step 3 — Clone and Bootstrap the Project

```bash
git clone https://github.com/your-org/carla-foundation-driving-demo.git
cd carla-foundation-driving-demo

# Bootstrap (creates .venv, installs core + dev + sim deps)
make setup
```

After `make setup`:
- `.venv/` is created with Python 3.10
- All packages from `pyproject.toml` `[dev]` and `[sim]` groups are installed
- Activate the environment: `source .venv/bin/activate`

---

## Step 4 — Install CARLA

> **Note:** CARLA does not run on macOS natively. macOS users should use a remote Linux server or Docker.

### Linux — Tarball Install (recommended)

```bash
# Download CARLA 0.9.15
wget https://github.com/carla-simulator/carla/releases/download/0.9.15/CARLA_0.9.15.tar.gz

# Extract to a dedicated directory
mkdir -p ~/carla
tar -xzf CARLA_0.9.15.tar.gz -C ~/carla

# Set environment variable
echo 'export CARLA_ROOT=~/carla/CARLA_0.9.15' >> ~/.bashrc
source ~/.bashrc

# Install CARLA Python wheel (Python 3.10)
pip install $CARLA_ROOT/PythonAPI/carla/dist/carla-0.9.15-cp310-cp310-linux_x86_64.whl
```

### macOS — Docker (for dev without a Linux box)

```bash
# Run CARLA in Docker (server only, no GUI)
docker run --rm -d \
  -p 2000-2002:2000-2002 \
  carlasim/carla:0.9.15 \
  /bin/bash -c "cd /home/carla && ./CarlaUE4.sh -RenderOffScreen -nosound"

# Verify connectivity
python scripts/diagnose.py
```

---

## Step 5 — (Linux) Install CUDA Toolkit

Required only for GPU training. Skip on macOS (use MPS or CPU).

```bash
# Ubuntu 22.04 + CUDA 11.8
wget https://developer.download.nvidia.com/compute/cuda/11.8.0/local_installers/cuda_11.8.0_520.61.05_linux.run
sudo sh cuda_11.8.0_520.61.05_linux.run --silent --toolkit

# Add to PATH
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
source ~/.bashrc

# Verify
nvcc --version
```

Then install the CUDA-enabled PyTorch build:

```bash
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
```

---

## Step 6 — Run Diagnostics

```bash
make diagnose
```

Expected output when fully configured:

```
  [ OK   ]  Python ≥ 3.10
  [ OK   ]  git
  [ OK   ]  uv (package manager)
  [ OK   ]  pyyaml
  [ OK   ]  structlog
  [ OK   ]  numpy
  ...
  [ OK   ]  carla Python package
  [ OK   ]  CARLA server (localhost:2000)
  [ OK   ]  CUDA  ─  1 device(s) — NVIDIA GeForce RTX 4090

  ✓ All critical checks passed.
```

---

## Step 7 — Start CARLA and Collect Data

```bash
# Terminal 1: Start CARLA server (Linux)
$CARLA_ROOT/CarlaUE4.sh -RenderOffScreen

# Terminal 2: Collect data
make collect
```

---

## Troubleshooting

### `carla` import fails
Make sure you installed the correct wheel for your Python version (cp310) and OS.

### CARLA server not reachable
Check that the server is running and port 2000 is not blocked by a firewall:
```bash
netstat -tlnp | grep 2000
```

### MPS not available (macOS)
MPS requires macOS 12.3+ and an Apple Silicon or AMD GPU. On Intel Macs, use `device: cpu` in `config/profiles/local_dev.yaml`.

### Out of disk space
Raw HDF5 datasets can be large. Ensure `data/` is on a volume with ≥ 50 GB free for full collection runs.
