# CARLA Runtime Profiles

This document explains the four runtime profiles and when to use each one.
Pick the profile that matches your current environment and activate it using
the `PROFILE` Makefile variable or `--profile` CLI flag.

---

## Overview

| Profile | `runtime.mode` | Where CARLA runs | Best for |
|---------|---------------|-----------------|----------|
| `macos_docker` | `docker` | Docker on macOS | Connectivity testing on Apple Silicon |
| `windows_local` | `local` | Natively on Windows | Development with display |
| `linux_local` | `local` | Natively on Linux | Data collection + training |
| `remote_carla` | `remote` | Remote Linux server | Headless data collection via SSH |

**How to activate a profile:**
```bash
# CLI flag
python scripts/smoke_test.py --profile macos_docker

# Makefile variable
PROFILE=remote_carla CARLA_HOST=192.168.1.5 make smoke

# Persistent (in your shell session)
export PROFILE=linux_local
make smoke
```

**Note:** All profiles inherit from `config/default.yaml`. A profile only
overrides the keys it explicitly sets — everything else is unchanged.

---

## macOS Docker (`macos_docker`)

**Use when:** You are on a Mac (Intel or Apple Silicon) and have Docker Desktop
running. CARLA runs inside a container accessible on `127.0.0.1:2000`.

### Apple Silicon (M1/M2/M3) Limitations

The official `carlasim/carla` Docker image is built for `linux/amd64`. On arm64
Macs it runs under **Rosetta 2 CPU emulation**.

| Feature | Availability |
|---------|-------------|
| TCP connectivity (host → container) | ✅ Works |
| Synchronous CARLA ticks | ✅ Works (slowly) |
| OpenGL / GPU passthrough | ❌ Not supported |
| 20 Hz tick rate | ❌ Unlikely under emulation |
| Data collection for training | ⚠️ Use remote node instead |

**The profile lowers `simulation.fixed_delta_seconds` to 0.1 (10 Hz) to
accommodate emulation overhead.**

### Setup
```bash
# Start CARLA in Docker (handles Apple Silicon warning automatically)
make carla-docker

# Wait 15-30s, then run the smoke test
CARLA_HOST=127.0.0.1 PROFILE=macos_docker make smoke
```

### Switching to a remote server
When macOS performance is insufficient (e.g., data collection), set the host
to your Linux server and switch to `remote_carla`:
```bash
CARLA_HOST=<server-ip> PROFILE=remote_carla make smoke
```

---

## Windows Local (`windows_local`)

**Use when:** You have CARLA installed natively on Windows with a GPU.

### Characteristics
- Render enabled (visible CARLA window)
- 20 Hz tick rate
- CUDA training device

### Setup
```powershell
# Start CARLA
powershell -ExecutionPolicy Bypass -File scripts\start_carla_windows.ps1 `
    -CARLA_ROOT C:\CARLA\CARLA_0.9.15

# Install Python wheel
pip install C:\CARLA\CARLA_0.9.15\PythonAPI\carla\dist\carla-0.9.15-cp310-*.whl

# Run smoke test
$env:CARLA_HOST="127.0.0.1"; $env:PROFILE="windows_local"; make smoke
```

See [docs/PHASE1_SMOKE_TEST.md](PHASE1_SMOKE_TEST.md) for the full guide.

---

## Linux Local (`linux_local`)

**Use when:** You have CARLA installed natively on a Linux machine with a GPU.
This is the recommended environment for data collection.

### Characteristics
- Headless (`-RenderOffScreen`)
- 20 Hz tick rate
- CUDA training device
- 8 DataLoader workers
- JSON logging for log aggregation

### Setup
```bash
# Start CARLA headlessly
cd $CARLA_ROOT
./CarlaUE4.sh -RenderOffScreen -carla-port=2000 &

# Install Python wheel
pip install $CARLA_ROOT/PythonAPI/carla/dist/carla-0.9.15-cp310-*.whl

# Run smoke test
PROFILE=linux_local make smoke

# Collect data
PROFILE=linux_local make collect
```

---

## Remote CARLA (`remote_carla`)

**Use when:** CARLA is running on a separate machine (LAN, cloud VM, or
university cluster) and your local machine is the client.

### Characteristics
- `carla_connection.host` defaults to `192.168.1.100` (placeholder)
- **Always override with `CARLA_HOST` env var**
- Longer timeout (60s) to handle network latency

### Setup

**On the remote server:**
```bash
cd $CARLA_ROOT
./CarlaUE4.sh -RenderOffScreen -carla-port=2000 &

# Ensure the firewall allows TCP 2000-2002 from your client IP
```

**On your local machine:**
```bash
# Test connectivity
CARLA_HOST=<server-ip> PROFILE=remote_carla make smoke

# Collect data
CARLA_HOST=<server-ip> PROFILE=remote_carla make collect
```

### SSH Tunnel (if firewall blocks port 2000)
```bash
ssh -L 2000:localhost:2000 -L 2001:localhost:2001 -L 2002:localhost:2002 \
    user@<server-ip> -N &

# Then connect via tunnel
CARLA_HOST=127.0.0.1 PROFILE=remote_carla make smoke
```

---

## Environment Variable Reference

These env vars override the corresponding config values in any profile:

| Variable | Config key | Example |
|----------|-----------|---------|
| `CARLA_HOST` | `carla_connection.host` | `192.168.1.5` |
| `CARLA_PORT` | `carla_connection.port` | `2000` |
| `CARLA_VERSION` | `carla_connection.version` | `0.9.15` |
| `CARLA_PYTHON_API_PATH` | `carla_connection.python_api_path` | `/home/user/carla/PythonAPI/carla/dist/` |
| `CARLA_DOCKER_IMAGE` | `runtime.docker_image` | `carlasim/carla:0.9.14` |

---

## Decision Tree

```
Are you on macOS?
├─ Yes → Do you have a remote Linux server?
│         ├─ Yes → remote_carla  (best for data collection)
│         └─ No  → macos_docker  (testing only)
└─ No  → Are you on Windows?
          ├─ Yes → windows_local
          └─ No  → linux_local  (recommended)
```
