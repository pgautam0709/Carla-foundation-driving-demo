"""
src/data/schemas.py — Data schema definitions for Phase 2 expert data collection.

All records are plain dataclasses that serialize to JSON-compatible dicts via
:func:`dataclasses.asdict`.  The ``SCHEMA_VERSION`` constant is embedded in
every metadata and manifest file so downstream tools can detect format changes.

Schema version history:
    2.0 — Phase 2 initial: flat-file episodes, PNG frames, JSONL records.
"""

from __future__ import annotations

import dataclasses
from typing import Any

# ── Schema version ─────────────────────────────────────────────────────────────
SCHEMA_VERSION: str = "2.0"


# ── Per-tick records (written to JSONL files) ──────────────────────────────────

@dataclasses.dataclass
class ControlRecord:
    """One row in controls.jsonl — vehicle control state at a single tick.

    Args:
        tick: Simulation tick counter (increments each world.tick()).
        frame: CARLA frame ID returned by world.tick().
        timestamp_sim: Simulation elapsed time in seconds.
        timestamp_wall: Wall-clock time (time.monotonic()) at sample point.
        throttle: [0, 1] throttle input.
        brake: [0, 1] brake input.
        steer: [-1, 1] steering input (negative = left).
        hand_brake: Hand brake engaged.
        reverse: Reverse gear engaged.
        manual_gear_shift: Manual gear control active.
        gear: Current gear number.
    """

    tick: int
    frame: int
    timestamp_sim: float
    timestamp_wall: float
    throttle: float
    brake: float
    steer: float
    hand_brake: bool
    reverse: bool
    manual_gear_shift: bool
    gear: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class TelemetryRecord:
    """One row in telemetry.jsonl — vehicle state at a single tick.

    Args:
        tick: Simulation tick counter.
        frame: CARLA frame ID.
        timestamp_sim: Simulation elapsed time in seconds.
        location: {x, y, z} world coordinates in metres.
        rotation: {pitch, yaw, roll} in degrees.
        velocity: {x, y, z} velocity vector in m/s.
        acceleration: {x, y, z} acceleration in m/s², or None if unavailable.
        speed_mps: Scalar speed in metres per second.
        speed_kph: Scalar speed in kilometres per hour.
        angular_velocity: {x, y, z} angular velocity in deg/s, or None.
        traffic_light_state: Traffic light state string, or None.
        speed_limit: Current road speed limit in kph, or None.
    """

    tick: int
    frame: int
    timestamp_sim: float
    location: dict[str, float]
    rotation: dict[str, float]
    velocity: dict[str, float]
    acceleration: dict[str, float] | None
    speed_mps: float
    speed_kph: float
    angular_velocity: dict[str, float] | None
    traffic_light_state: str | None
    speed_limit: float | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class EventRecord:
    """One row in events.jsonl — a meaningful simulation event.

    Event types:
        episode_started, ego_spawned, camera_attached, route_started,
        tick_warning, collision, lane_invasion, episode_completed,
        cleanup_completed, episode_failed.

    Args:
        tick: Simulation tick at which the event occurred.
        frame: CARLA frame ID at the event, or 0 for startup events.
        timestamp_wall: Wall-clock time of the event (time.monotonic()).
        event_type: One of the event type strings above.
        payload: Arbitrary key-value metadata for this event.
    """

    tick: int
    frame: int
    timestamp_wall: float
    event_type: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


# ── Episode-level documents (written to JSON files) ────────────────────────────

@dataclasses.dataclass
class SensorConfig:
    """Sensor specification embedded in episode metadata.

    Args:
        name: Logical sensor name (e.g. ``"front_camera"``).
        sensor_type: CARLA blueprint ID (e.g. ``"sensor.camera.rgb"``).
        width: Image width in pixels, or None for non-image sensors.
        height: Image height in pixels, or None for non-image sensors.
        fov: Horizontal field of view in degrees, or None.
        transform: Sensor pose relative to ego vehicle {x, y, z, pitch, yaw, roll}.
    """

    name: str
    sensor_type: str
    width: int | None
    height: int | None
    fov: float | None
    transform: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class EpisodeMetadata:
    """Full provenance record written to metadata.json at episode start.

    Args:
        episode_id: Unique episode identifier (see :func:`generate_episode_id`).
        created_at: ISO 8601 UTC timestamp string.
        schema_version: Always :data:`SCHEMA_VERSION`.
        runtime_profile: Active config profile name (e.g. ``"macos_docker"``).
        carla_host: CARLA server hostname or IP.
        carla_port: CARLA server port.
        carla_version_expected: Version string from ``carla_connection.version``.
        carla_version_server: Actual server version, or None in dry-run.
        carla_version_client: Python wheel version, or None in dry-run.
        town: CARLA map name (e.g. ``"Town03"``).
        weather_preset: CARLA weather preset name, or None.
        route_name: Human-readable route label.
        route_hash: First 8 hex chars of SHA-256 over the canonical route dict.
        tick_count_target: Number of ticks requested.
        fixed_delta_seconds: Synchronous timestep in seconds.
        sensors: List of attached sensor configurations.
        ego_vehicle_blueprint: CARLA blueprint ID of the ego vehicle.
        git_commit: Short git HEAD hash, or None if not in a git repo.
        collection_mode: ``"live"`` (real CARLA) or ``"dry_run"``.
        camera_width: Front camera width in pixels.
        camera_height: Front camera height in pixels.
        camera_fov: Front camera horizontal FOV in degrees.
    """

    episode_id: str
    created_at: str
    schema_version: str
    runtime_profile: str
    carla_host: str
    carla_port: int
    carla_version_expected: str
    carla_version_server: str | None
    carla_version_client: str | None
    town: str
    weather_preset: str | None
    route_name: str
    route_hash: str
    tick_count_target: int
    fixed_delta_seconds: float
    sensors: list[SensorConfig]
    ego_vehicle_blueprint: str
    git_commit: str | None
    collection_mode: str
    camera_width: int
    camera_height: int
    camera_fov: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (sensors expanded to list of dicts)."""
        d = dataclasses.asdict(self)
        return d


@dataclasses.dataclass
class RouteDefinition:
    """Route specification written to route.json.

    Args:
        town: CARLA map name.
        route_name: Human-readable label.
        route_hash: First 8 hex chars of SHA-256 over the canonical route dict.
        start_transform: Spawn pose {x, y, z, pitch, yaw, roll}.
        destination_transform: Target pose, or None for fixed-tick episodes.
        distance_estimate_m: Euclidean start→destination distance, or None.
        generation_method: How the route was produced: ``"spawn_point"``,
            ``"waypoint"``, or ``"manual"``.
    """

    town: str
    route_name: str
    route_hash: str
    start_transform: dict[str, float]
    destination_transform: dict[str, float] | None
    distance_estimate_m: float | None
    generation_method: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class EpisodeManifest:
    """Episode inventory written to manifest.json after collection completes.

    Args:
        episode_id: Same identifier as in metadata.json.
        schema_version: Always :data:`SCHEMA_VERSION`.
        files: Sorted list of relative file paths within the episode directory.
        frame_count: Number of PNG frames captured.
        control_row_count: Number of rows in controls.jsonl.
        telemetry_row_count: Number of rows in telemetry.jsonl.
        event_count: Number of rows in events.jsonl.
        status: ``"success"``, ``"partial"``, or ``"failed"``.
        validation_status: ``"valid"``, ``"invalid"``, or ``"unchecked"``.
        completed_at: ISO 8601 UTC timestamp when the manifest was written.
    """

    episode_id: str
    schema_version: str
    files: list[str]
    frame_count: int
    control_row_count: int
    telemetry_row_count: int
    event_count: int
    status: str
    validation_status: str
    completed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return dataclasses.asdict(self)
