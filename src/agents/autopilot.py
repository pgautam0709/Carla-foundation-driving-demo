"""
src/agents/autopilot.py — CARLA built-in autopilot agent wrapper.

The AutopilotAgent delegates all driving decisions to CARLA's internal
Traffic Manager autopilot. It serves as the expert policy for data
collection (behavioural cloning teacher) and as a baseline agent for
closed-loop evaluation.

Usage::

    from src.agents.autopilot import AutopilotAgent
    from src.simulation.client import CARLAClient

    with CARLAClient(...) as client:
        vehicle = ...  # spawned ego vehicle
        agent = AutopilotAgent(client, vehicle)
        agent.start()

        for _ in range(500):
            client.tick()
            control = agent.get_control()
            # control is the applied VehicleControl (for logging only —
            # autopilot applies it internally)

        agent.stop()
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.utils.logging import get_logger

log = get_logger(__name__)

try:
    import carla  # type: ignore[import]
    _CARLA_AVAILABLE = True
except ImportError:
    _CARLA_AVAILABLE = False
    carla = None  # type: ignore[assignment]


@dataclass
class DriveControl:
    """Normalised vehicle control output."""

    throttle: float   # [0, 1]
    steer: float      # [-1, 1]  negative = left
    brake: float      # [0, 1]
    hand_brake: bool = False
    reverse: bool = False


class AutopilotAgent:
    """Wrapper around CARLA's built-in Traffic Manager autopilot.

    The autopilot applies controls internally each tick; this class exposes
    the last applied control for logging and dataset recording.

    Args:
        client: Connected :class:`~src.simulation.client.CARLAClient`.
        vehicle: The ego vehicle actor.
        target_speed_kmh: Desired cruising speed passed to Traffic Manager.
        tm_port: Traffic Manager port (default 8000).
    """

    def __init__(
        self,
        client: Any,
        vehicle: Any,
        target_speed_kmh: float = 30.0,
        tm_port: int = 8000,
    ) -> None:
        if not _CARLA_AVAILABLE:
            raise ImportError("carla package required. Run: make diagnose")

        self._client = client
        self._vehicle = vehicle
        self._target_speed_kmh = target_speed_kmh
        self._tm_port = tm_port
        self._tm: Any = None
        self._active = False

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Enable autopilot on the vehicle."""
        tm = self._client.client.get_trafficmanager(self._tm_port)
        tm.set_synchronous_mode(True)
        tm.set_desired_speed(self._vehicle, self._target_speed_kmh)
        tm.ignore_lights_percentage(self._vehicle, 0)   # obey all lights
        tm.auto_lane_change(self._vehicle, True)
        self._vehicle.set_autopilot(True, self._tm_port)
        self._tm = tm
        self._active = True
        log.info(
            "agent.autopilot_started",
            target_speed_kmh=self._target_speed_kmh,
            tm_port=self._tm_port,
        )

    def stop(self) -> None:
        """Disable autopilot and apply zero control."""
        if self._active:
            self._vehicle.set_autopilot(False)
            zero = carla.VehicleControl(throttle=0.0, brake=1.0)
            self._vehicle.apply_control(zero)
            self._active = False
            log.info("agent.autopilot_stopped")

    # ── Runtime API ────────────────────────────────────────────────────────────

    def get_control(self) -> DriveControl:
        """Return the control currently applied by the autopilot.

        Call this *after* ``client.tick()`` to get the control that was
        applied in the last simulation step.
        """
        raw: Any = self._vehicle.get_control()
        return DriveControl(
            throttle=float(raw.throttle),
            steer=float(raw.steer),
            brake=float(raw.brake),
            hand_brake=bool(raw.hand_brake),
            reverse=bool(raw.reverse),
        )

    def get_velocity_kmh(self) -> float:
        """Return the ego vehicle's current speed in km/h."""
        vel = self._vehicle.get_velocity()
        speed_ms = (vel.x**2 + vel.y**2 + vel.z**2) ** 0.5
        return speed_ms * 3.6

    @property
    def is_active(self) -> bool:
        """Whether autopilot is currently enabled."""
        return self._active
