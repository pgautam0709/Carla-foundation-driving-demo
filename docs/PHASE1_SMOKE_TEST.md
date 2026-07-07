# Phase 1 Smoke Test Guide

This guide shows how to run the Phase 1 smoke test (`make smoke`) for each
supported runtime environment.

The smoke test:
1. Connects to the CARLA server at `CARLA_HOST:CARLA_PORT`
2. Loads the configured map
3. Spawns one ego vehicle
4. Runs 100 synchronous ticks and measures the tick rate (Hz)
5. Destroys all actors and restores world settings
6. Reports connection time, server version, and tick rate

---

## Prerequisites (all environments)

- `make setup` must have been run at least once
- The CARLA Python wheel must be installed (see [docs/SETUP.md](SETUP.md))
- `make lint` and `make test` should pass before running any CARLA commands

---

## macOS — Docker Runtime

### Step 1: Start CARLA in Docker

```bash
make carla-docker
```

> **Apple Silicon note:** The `carlasim/carla` image is `amd64` only and runs
> under Rosetta 2 emulation on M1/M2/M3. Connectivity testing works, but
> 20 Hz performance is not achievable. For data collection, use a remote
> Linux node.

Wait 15–30 seconds for CARLA to initialise.

### Step 2: Run the smoke test

```bash
CARLA_HOST=127.0.0.1 CARLA_PORT=2000 PROFILE=macos_docker make smoke
```

### Step 3: Stop CARLA when done

```bash
docker stop carla-server
```

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Docker daemon is not running` | Start Docker Desktop |
| `Cannot connect to CARLA` | Wait longer for CARLA to start; check `docker logs carla-server` |
| `No such image` | `docker pull carlasim/carla:0.9.15` |
| Tick rate < 5 Hz | Normal under Rosetta emulation; use remote server for data collection |

---

## Windows — Local Runtime

### Step 1: Start CARLA

Open PowerShell and run:

```powershell
powershell -ExecutionPolicy Bypass `
    -File scripts\start_carla_windows.ps1 `
    -CARLA_ROOT C:\CARLA\CARLA_0.9.15
```

Or for headless mode:

```powershell
powershell -ExecutionPolicy Bypass `
    -File scripts\start_carla_windows.ps1 `
    -CARLA_ROOT C:\CARLA\CARLA_0.9.15 `
    -Headless
```

### Step 2: Install the Python wheel (once)

```powershell
pip install C:\CARLA\CARLA_0.9.15\PythonAPI\carla\dist\carla-0.9.15-cp310-*.whl
```

### Step 3: Run the smoke test

In a **separate** terminal (or the same one after CARLA has started):

```powershell
set CARLA_HOST=127.0.0.1
set CARLA_PORT=2000
set PROFILE=windows_local
make smoke
```

Or as a one-liner:

```powershell
$env:CARLA_HOST="127.0.0.1"; $env:PROFILE="windows_local"; make smoke
```

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `CarlaUE4.exe not found` | Check `CARLA_ROOT` path |
| `Cannot connect to CARLA` | Wait ~30s for CARLA to load; check Windows Firewall |
| `carla package not installed` | Run the `pip install` step above |
| Crash on startup | Ensure your GPU drivers are up to date |

---

## Linux — Local Runtime

### Step 1: Start CARLA

```bash
cd $CARLA_ROOT
./CarlaUE4.sh -RenderOffScreen -carla-port=2000 &
```

### Step 2: Install the Python wheel (once)

```bash
pip install $CARLA_ROOT/PythonAPI/carla/dist/carla-0.9.15-cp310-*.whl
# Or set CARLA_PYTHON_API_PATH and let diagnose.py guide you:
export CARLA_PYTHON_API_PATH=$CARLA_ROOT/PythonAPI/carla/dist/
make diagnose
```

### Step 3: Run the smoke test

```bash
PROFILE=linux_local make smoke
# CARLA_HOST defaults to localhost; no override needed
```

### Step 4: Collect data

```bash
PROFILE=linux_local make collect
```

---

## Remote CARLA Server

### Step 1: Start CARLA on the remote server

SSH into the remote machine and start CARLA:

```bash
cd $CARLA_ROOT
./CarlaUE4.sh -RenderOffScreen -carla-port=2000 &
```

Ensure the firewall allows TCP 2000–2002 from your local machine.

### Step 2: Test connectivity

From your local machine:

```bash
CARLA_HOST=<server-ip> CARLA_PORT=2000 PROFILE=remote_carla make smoke
```

### Step 3: Collect data

```bash
CARLA_HOST=<server-ip> PROFILE=remote_carla make collect
```

### SSH Tunnel (if firewall blocks ports)

```bash
# Forward CARLA ports over SSH
ssh -L 2000:localhost:2000 \
    -L 2001:localhost:2001 \
    -L 2002:localhost:2002 \
    user@<server-ip> -N &

# Then connect via tunnel
CARLA_HOST=127.0.0.1 PROFILE=remote_carla make smoke
```

---

## Smoke Test Output Reference

A successful smoke test looks like:

```
────────────────────────────────────────────────────
  Phase 1 Smoke Test
────────────────────────────────────────────────────
  Target  : 127.0.0.1:2000
  Profile : macos_docker
  Map     : Town03
  Ticks   : 100
────────────────────────────────────────────────────

  [ OK ] Connected  1245ms  server=0.9.15
  [ OK ] Map: Town03
  [ OK ] Ego vehicle: vehicle.lincoln.mkz_2020  id=42

  Running 100 synchronous ticks ...

────────────────────────────────────────────────────
  Results
────────────────────────────────────────────────────
  Ticks        : 100
  Duration     : 8.71s
  Tick rate    : 11.5 Hz
  Server ver.  : 0.9.15

  ⚠ Tick rate 11.5 Hz is below 15 Hz target
    Consider a native or GPU-accelerated CARLA instance
────────────────────────────────────────────────────
```

> **Tick rate guidance:** ≥ 15 Hz is the Phase 1 target. Under Rosetta 2
> emulation, 5–12 Hz is typical and is acceptable for connectivity testing.
> For data collection, use a native Linux instance.

---

## Common Error Messages

### CARLA Python package is not installed

```
[FAIL] CARLA Python package is not installed.
  Install it from the CARLA 0.9.15 release tarball:
    pip install <CARLA_ROOT>/PythonAPI/carla/dist/carla-0.9.15-cp310-*.whl
```

**Fix:** Install the wheel from the CARLA release. See [docs/SETUP.md](SETUP.md).

### Cannot connect to CARLA

```
[FAIL] Cannot connect to CARLA at 127.0.0.1:2000
  make carla-docker   # start the CARLA Docker container
  See: docs/PHASE1_SMOKE_TEST.md
```

**Fix:** Start CARLA using the appropriate command for your platform, wait for
it to initialise, then retry.
