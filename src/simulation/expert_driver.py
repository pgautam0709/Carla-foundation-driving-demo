"""
src/simulation/expert_driver.py — Expert driver abstraction for Phase 2.

Wraps the CARLA Traffic Manager autopilot to provide a clean interface for
recording control signals and telemetry during expert data collection.

Design decisions:
- Uses ``set_autopilot(True)`` via Traffic Manager — acceptable for Phase 2.
- Collision and lane-invasion sensors are stubbed with clear extension points
  for Phase 3 implementation.
- All CARLA API calls are guarded with exception handlers so a sensor failure
  does not abort a long collection episode.

Usage::

    with CARLAClient(...) as client:
        vehicle = ...  # spawned and registered ego vehicle
        driver = ExpertDriver(vehicle, cfg=cfg)
        driver.start()

        with EpisodeWriter(ep_dir) as writer:
            for tick_i in range(ticks):
                frame_id = client.tick()
                ts_sim = ...
                ts_wall = time.monotonic()
                writer.write_control(driver.get_control_record(tick_i, frame_id, ts_sim, ts_wall))
                writer.write_telemetry(driver.get_telemetry_record(tick_i, frame_id, ts_sim))

        driver.stop()
"""

from __future__ import annotations

import math
from typing import Any

from src.data.schemas import ControlRecord, TelemetryRecord
from src.utils.logging import get_logger

log = get_logger(__name__)


class ExpertDriver:
    """Wrap CARLA Traffic Manager autopilot for expert data collection.

    The driver samples vehicle state each tick and converts CARLA objects to
    serialisable :class:`~src.data.schemas.ControlRecord` and
    :class:`~src.data.schemas.TelemetryRecord` instances.

    Extension points (not implemented in Phase 2 — annotated for Phase 3):
    - ``_collision_sensor``: attach a ``sensor.other.collision`` actor.
    - ``_lane_sensor``: attach a ``sensor.other.lane_invasion`` actor.

    Args:
        vehicle: A live ``carla.Vehicle`` actor.
        cfg: The loaded configuration dict (used for future Traffic Manager
            parameters; not required by the base autopilot mode).
    """

    def __init__(self, vehicle: Any, cfg: dict) -> None:
        self._vehicle = vehicle
        self._cfg = cfg
        self._started: bool = False

        # ── Phase 3 extension points ──────────────────────────────────────────
        # These will be populated by attach_collision_sensor() and
        # attach_lane_sensor() when implemented in Phase 3.
        self._collision_sensor: Any = None  # type: ignore[assignment]
        self._lane_sensor: Any = None       # type: ignore[assignment]

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Enable autopilot via CARLA Traffic Manager.

        Raises:
            RuntimeError: If the vehicle is not alive.
        """
        if not self._vehicle.is_alive:
            raise RuntimeError("Cannot start ExpertDriver: vehicle is not alive.")
        self._vehicle.set_autopilot(True)
        self._started = True
        log.info("expert_driver.started", vehicle_id=self._vehicle.id)

    def stop(self) -> None:
        """Disable autopilot.

        Safe to call even if :meth:`start` was never called.
        """
        if self._started and self._vehicle.is_alive:
            try:
                self._vehicle.set_autopilot(False)
            except Exception as exc:
                log.warning("expert_driver.stop_error", error=str(exc))
        self._started = False
        log.info("expert_driver.stopped", vehicle_id=self._vehicle.id)

    # ── Record sampling ────────────────────────────────────────────────────────

    def get_control_record(
        self,
        tick: int,
        frame: int,
        timestamp_sim: float,
        timestamp_wall: float,
    ) -> ControlRecord:
        """Sample the vehicle's current control state.

        Args:
            tick: Simulation tick counter.
            frame: CARLA frame ID returned by ``world.tick()``.
            timestamp_sim: Simulation elapsed time in seconds.
            timestamp_wall: Wall-clock sample time (:func:`time.monotonic`).

        Returns:
            A :class:`~src.data.schemas.ControlRecord` for this tick.
        """
        ctrl = self._vehicle.get_control()
        return ControlRecord(
            tick=tick,
            frame=frame,
            timestamp_sim=timestamp_sim,
            timestamp_wall=timestamp_wall,
            throttle=float(ctrl.throttle),
            brake=float(ctrl.brake),
            steer=float(ctrl.steer),
            hand_brake=bool(ctrl.hand_brake),
            reverse=bool(ctrl.reverse),
            manual_gear_shift=bool(ctrl.manual_gear_shift),
            gear=int(ctrl.gear),
        )

    def get_telemetry_record(
        self,
        tick: int,
        frame: int,
        timestamp_sim: float,
    ) -> TelemetryRecord:
        """Sample the vehicle's current kinematic state.

        Optional CARLA fields (traffic light state, speed limit) are
        silently swallowed if unavailable (e.g. no traffic lights on map).

        Args:
            tick: Simulation tick counter.
            frame: CARLA frame ID.
            timestamp_sim: Simulation elapsed time in seconds.

        Returns:
            A :class:`~src.data.schemas.TelemetryRecord` for this tick.
        """
        transform  = self._vehicle.get_transform()
        velocity   = self._vehicle.get_velocity()
        accel      = self._vehicle.get_acceleration()
        ang_vel    = self._vehicle.get_angular_velocity()

        speed_mps = math.sqrt(velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2)

        # Optional fields — may not be available on all maps or CARLA versions.
        try:
            tl_state: str | None = str(self._vehicle.get_traffic_light_state())
        except Exception:
            tl_state = None

        try:
            speed_limit: float | None = float(self._vehicle.get_speed_limit())
        except Exception:
            speed_limit = None

        return TelemetryRecord(
            tick=tick,
            frame=frame,
            timestamp_sim=timestamp_sim,
            location={
                "x": float(transform.location.x),
                "y": float(transform.location.y),
                "z": float(transform.location.z),
            },
            rotation={
                "pitch": float(transform.rotation.pitch),
                "yaw":   float(transform.rotation.yaw),
                "roll":  float(transform.rotation.roll),
            },
            velocity={
                "x": float(velocity.x),
                "y": float(velocity.y),
                "z": float(velocity.z),
            },
            acceleration={
                "x": float(accel.x),
                "y": float(accel.y),
                "z": float(accel.z),
            },
            speed_mps=speed_mps,
            speed_kph=speed_mps * 3.6,
            angular_velocity={
                "x": float(ang_vel.x),
                "y": float(ang_vel.y),
                "z": float(ang_vel.z),
            },
            traffic_light_state=tl_state,
            speed_limit=speed_limit,
        )

    # ── Phase 3 extension stubs ────────────────────────────────────────────────
    # These methods are intentionally empty. Phase 3 will implement them by
    # spawning sensor actors and attaching queue-based listeners.

    def attach_collision_sensor(self, world: Any) -> None:
        """[Phase 3] Attach a collision sensor to the ego vehicle.

        Args:
            world: The CARLA world object.
        """
        log.debug("expert_driver.collision_sensor_stub_called")

    def attach_lane_sensor(self, world: Any) -> None:
        """[Phase 3] Attach a lane invasion sensor to the ego vehicle.

        Args:
            world: The CARLA world object.
        """
        log.debug("expert_driver.lane_sensor_stub_called")
