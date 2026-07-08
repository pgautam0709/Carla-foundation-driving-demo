# Phase 2 — Expert Data Collection

## Overview

Phase 2 builds the production-quality data collection pipeline for the CARLA
Foundation Driving Demo. It answers the engineering question:

> **Can this platform generate high-quality autonomous driving training data
> in a disciplined, reproducible format?**

---

## What Was Built

### New modules

| Module | Purpose |
|---|---|
| [`src/data/schemas.py`](../src/data/schemas.py) | Dataclasses for all JSON/JSONL records |
| [`src/data/episode.py`](../src/data/episode.py) | Episode ID generation, route hashing, directory layout |
| [`src/data/writers.py`](../src/data/writers.py) | JSONLWriter, FrameWriter, EpisodeWriter context manager |
| [`src/data/validation.py`](../src/data/validation.py) | EpisodeValidator — 14 checks, CARLA-free |
| [`src/simulation/expert_driver.py`](../src/simulation/expert_driver.py) | Traffic Manager autopilot wrapper |

### New scripts

| Script | Purpose |
|---|---|
| [`scripts/collect_expert_episode.py`](../scripts/collect_expert_episode.py) | Expert data collection — `--dry-run` and live modes |
| [`scripts/validate_episode.py`](../scripts/validate_episode.py) | Episode validation CLI |

---

## Episode Directory Structure

Every collected episode is written to a self-contained directory:

```
data/raw/episodes/<episode_id>/
    metadata.json        ← Full provenance: runtime, sensors, git hash
    route.json           ← Start/end transforms, route hash
    controls.jsonl       ← One row per tick: throttle, steer, brake, gear ...
    telemetry.jsonl      ← One row per tick: location, velocity, speed_kph ...
    events.jsonl         ← Sparse: episode_started, collision, completed ...
    manifest.json        ← File inventory, counts, validation status
    frames/
        front_camera/
            000000.png
            000001.png
            ...
```

### Schema version: `2.0`

---

## Episode ID Format

```
episode_YYYYMMDD_HHMMSS_<town>_<route>_<profile>
```

Examples:
```
episode_20260707_143012_Town01_routeA_macos_docker
episode_20260707_143012_Town03_highway_remote_carla
```

Episode IDs are:
- **Deterministic** — same inputs, same timestamp → same ID
- **URL-safe** — alphanumerics and underscores only
- **Self-describing** — readable without a database lookup

---

## Developer Commands

```bash
# Generate a complete synthetic episode (no CARLA required):
make collect-dry-run

# Validate the most recently collected episode:
make validate-episode

# Validate a specific episode:
make validate-episode EPISODE_DIR=data/raw/episodes/episode_20260707_...

# Live collection (requires CARLA server):
CARLA_HOST=<ip> PROFILE=linux_local make collect

# Full options:
python scripts/collect_expert_episode.py --help
python scripts/validate_episode.py --help
```

---

## Data Schema Reference

### `metadata.json` — Episode provenance

```json
{
  "episode_id": "episode_20260707_143012_Town03_routeA_local_dev",
  "created_at": "2026-07-07T14:30:12+00:00",
  "schema_version": "2.0",
  "runtime_profile": "local_dev",
  "carla_host": "localhost",
  "carla_port": 2000,
  "carla_version_expected": "0.9.15",
  "carla_version_server": "0.9.15",
  "carla_version_client": "0.9.15",
  "town": "Town03",
  "route_name": "routeA",
  "route_hash": "a3f2b1c9",
  "tick_count_target": 500,
  "fixed_delta_seconds": 0.05,
  "sensors": [{ "name": "front_camera", "sensor_type": "sensor.camera.rgb", ... }],
  "ego_vehicle_blueprint": "vehicle.lincoln.mkz_2020",
  "git_commit": "bdfef76",
  "collection_mode": "live"
}
```

### `controls.jsonl` — Per-tick control state

```jsonl
{"tick": 0, "frame": 12345, "timestamp_sim": 0.0, "timestamp_wall": 1.23, "throttle": 0.72, "brake": 0.0, "steer": -0.05, "hand_brake": false, "reverse": false, "manual_gear_shift": false, "gear": 3}
{"tick": 1, "frame": 12346, "timestamp_sim": 0.05, ...}
```

### `telemetry.jsonl` — Per-tick vehicle state

```jsonl
{"tick": 0, "frame": 12345, "timestamp_sim": 0.0, "location": {"x": 0.0, "y": 0.0, "z": 0.0}, "rotation": {"pitch": 0.0, "yaw": 90.0, "roll": 0.0}, "velocity": {"x": 5.2, "y": 0.0, "z": 0.0}, "acceleration": {...}, "speed_mps": 5.2, "speed_kph": 18.7, "angular_velocity": {...}, "traffic_light_state": "Green", "speed_limit": 50.0}
```

### `events.jsonl` — Sparse simulation events

| `event_type` | When written |
|---|---|
| `episode_started` | Before first tick |
| `ego_spawned` | After ego vehicle is spawned |
| `camera_attached` | After camera sensor is attached |
| `route_started` | When autopilot engages (live only) |
| `tick_warning` | When a frame capture fails |
| `collision` | On collision sensor trigger (Phase 3) |
| `lane_invasion` | On lane invasion sensor trigger (Phase 3) |
| `episode_completed` | After final tick |
| `cleanup_completed` | After actor destruction |
| `episode_failed` | On KeyboardInterrupt or crash |

### `manifest.json` — Episode inventory

```json
{
  "episode_id": "...",
  "schema_version": "2.0",
  "files": ["controls.jsonl", "events.jsonl", "frames/front_camera/000000.png", ...],
  "frame_count": 500,
  "control_row_count": 500,
  "telemetry_row_count": 500,
  "event_count": 2,
  "status": "success",
  "validation_status": "unchecked",
  "completed_at": "2026-07-07T14:31:03+00:00"
}
```

---

## Validation Checks

`make validate-episode` runs 14 checks:

| Check | Description |
|---|---|
| `file: metadata.json` | File present |
| `file: route.json` | File present |
| `file: controls.jsonl` | File present |
| `file: telemetry.jsonl` | File present |
| `file: events.jsonl` | File present |
| `file: manifest.json` | File present |
| `jsonl parseable: controls.jsonl` | All rows parse as valid JSON |
| `jsonl parseable: telemetry.jsonl` | All rows parse as valid JSON |
| `jsonl parseable: events.jsonl` | All rows parse as valid JSON |
| `metadata fields` | All 9 required fields present |
| `manifest fields` | All 9 required fields present |
| `telemetry non-empty` | At least one telemetry row |
| `frame sequential` | `000000.png`, `000001.png`, … no gaps |
| `frame/control counts` | Informational (frames vs control rows) |

---

## Dry-Run Mode

`--dry-run` generates a complete synthetic episode with:
- **Solid black 640×480 PNG frames** — valid PNG, no OpenCV required
- **Zero-valued control records** — throttle=0, steer=0, brake=0
- **Zero-valued telemetry records** — vehicle at world origin
- **Two events** — `episode_started`, `episode_completed`
- **Full metadata and manifest** — identical structure to live collection

The dry-run output passes all 14 validation checks and is designed to let
ML tooling be developed and tested before CARLA is available.

---

## Design Decisions

### ADR-001: Flat-file format (PNG + JSONL) over HDF5

The Phase 0 `EpisodeRecorder` uses HDF5. Phase 2 uses flat files because:
- **Debuggability** — any frame can be opened with an image viewer
- **Parallelism** — file-level sharding without HDF5 locking
- **Portability** — no h5py dependency in the collection hot path
- **ML-readiness** — PyTorch `Dataset` subclasses trivially index by frame ID

HDF5 remains available via `collect_data.py` for legacy use.

### ADR-002: Pure stdlib PNG encoder

The `_make_black_png` and `_bgra_to_png` functions use only `struct` and
`zlib` — no OpenCV or PIL dependency in the critical collection path. This
keeps the collection script importable in environments where image libraries
are not installed.

### ADR-003: No validation status written to manifest during collection

`validation_status` is always written as `"unchecked"` during collection.
The validator updates it only when `--fix-manifest` is passed (Phase 3
feature). This keeps the collection path clean and the manifest accurate.

---

## Extension Points for Phase 3

The following stubs are already in place:

- `ExpertDriver.attach_collision_sensor(world)` — collision event recording
- `ExpertDriver.attach_lane_sensor(world)` — lane invasion recording
- `EventRecord.event_type = "collision"` — payload schema ready
- `validate_episode.py --fix-manifest` — will rewrite `validation_status`

---

## Verified Results

| Check | Result |
|---|---|
| `make lint` | ✅ All checks passed |
| `make test` | ✅ **121/121 unit tests** (0.39s, no CARLA required) |
| `make collect-dry-run` | ✅ 500 frames · 500 controls · 500 telemetry rows |
| `make validate-episode` | ✅ **14/14 checks passed** |
