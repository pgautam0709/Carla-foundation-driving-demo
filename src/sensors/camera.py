"""
src/sensors/camera.py — RGB and Depth camera sensor wrappers for CARLA.

Each camera class provides:
- Blueprint configuration from a config dict
- Attachment to an ego vehicle
- A thread-safe frame queue for consuming sensor data
- Context-manager lifecycle (start / stop)

Usage::

    from src.sensors.camera import RGBCamera
    from src.simulation.client import CARLAClient

    with CARLAClient(...) as client:
        with RGBCamera(client, vehicle, cfg["sensors"]["rgb_camera"]) as cam:
            client.tick()
            frame = cam.get_frame(timeout=1.0)
            # frame.image: np.ndarray (H, W, 3) uint8 BGR
            # frame.timestamp: float
            # frame.frame_id: int
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

import numpy as np

from src.utils.logging import get_logger

log = get_logger(__name__)

try:
    import carla  # type: ignore[import]
    _CARLA_AVAILABLE = True
except ImportError:
    _CARLA_AVAILABLE = False
    carla = None  # type: ignore[assignment]


# ── Data containers ────────────────────────────────────────────────────────────

@dataclass
class CameraFrame:
    """A single captured camera frame."""

    image: np.ndarray          # (H, W, C) uint8
    frame_id: int
    timestamp: float           # simulation timestamp in seconds
    sensor_transform: Any      # carla.Transform at capture time
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Internal helper ────────────────────────────────────────────────────────────

def _build_transform(cfg: dict[str, Any]) -> Any:
    """Build a carla.Transform from a config dict."""
    loc = carla.Location(
        x=float(cfg.get("x", 0.0)),
        y=float(cfg.get("y", 0.0)),
        z=float(cfg.get("z", 0.0)),
    )
    rot = carla.Rotation(
        pitch=float(cfg.get("pitch", 0.0)),
        yaw=float(cfg.get("yaw", 0.0)),
        roll=float(cfg.get("roll", 0.0)),
    )
    return carla.Transform(loc, rot)


# ── Base class ─────────────────────────────────────────────────────────────────

class _BaseCamera:
    """Shared implementation for CARLA camera sensors."""

    _BLUEPRINT_ID: str = ""  # set in subclasses

    def __init__(
        self,
        client: Any,           # CARLAClient instance
        vehicle: Any,          # carla.Actor (ego vehicle)
        cfg: dict[str, Any],
        queue_maxsize: int = 10,
    ) -> None:
        if not _CARLA_AVAILABLE:
            raise ImportError(
                "The 'carla' package is required for camera sensors. "
                "Run: make diagnose"
            )
        self._client = client
        self._vehicle = vehicle
        self._cfg = cfg
        self._sensor: Any = None
        self._lock = threading.Lock()
        self._frame_queue: queue.Queue[CameraFrame] = queue.Queue(maxsize=queue_maxsize)

    # ── Context manager ────────────────────────────────────────────────────────

    def __enter__(self) -> _BaseCamera:
        self._spawn()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._destroy()

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_frame(self, timeout: float = 2.0) -> CameraFrame:
        """Block until a frame is available and return it.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            The next :class:`CameraFrame` from the queue.

        Raises:
            queue.Empty: If no frame arrives within *timeout*.
        """
        return self._frame_queue.get(block=True, timeout=timeout)

    def drain(self) -> None:
        """Discard all pending frames in the queue."""
        while not self._frame_queue.empty():
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                break

    # ── Internal ───────────────────────────────────────────────────────────────

    def _spawn(self) -> None:
        world = self._client.world
        bp_lib = world.get_blueprint_library()
        bp = bp_lib.find(self._BLUEPRINT_ID)

        bp.set_attribute("image_size_x", str(self._cfg.get("width", 640)))
        bp.set_attribute("image_size_y", str(self._cfg.get("height", 480)))
        bp.set_attribute("fov", str(self._cfg.get("fov", 110.0)))

        transform = _build_transform(self._cfg.get("transform", {}))
        sensor = world.spawn_actor(bp, transform, attach_to=self._vehicle)
        self._client.register_actor(sensor)
        sensor.listen(self._on_image)
        self._sensor = sensor
        log.info(
            "sensor.spawned",
            type=self._BLUEPRINT_ID,
            width=self._cfg.get("width"),
            height=self._cfg.get("height"),
            fov=self._cfg.get("fov"),
        )

    def _destroy(self) -> None:
        if self._sensor is not None and self._sensor.is_alive:
            self._sensor.stop()
            self._sensor.destroy()
            self._sensor = None
        log.info("sensor.destroyed", type=self._BLUEPRINT_ID)

    def _on_image(self, raw_image: Any) -> None:
        """CARLA sensor callback — runs in CARLA's internal thread."""
        raise NotImplementedError


# ── Concrete cameras ───────────────────────────────────────────────────────────

class RGBCamera(_BaseCamera):
    """Standard RGB camera sensor.

    Produces frames with shape ``(H, W, 3)`` uint8 in BGR channel order
    (compatible with OpenCV).
    """

    _BLUEPRINT_ID = "sensor.camera.rgb"

    def _on_image(self, raw_image: Any) -> None:
        array = np.frombuffer(raw_image.raw_data, dtype=np.uint8)
        array = array.reshape((raw_image.height, raw_image.width, 4))
        bgr = array[:, :, :3]  # drop alpha channel

        frame = CameraFrame(
            image=bgr.copy(),
            frame_id=raw_image.frame,
            timestamp=raw_image.timestamp,
            sensor_transform=raw_image.transform,
        )
        try:
            self._frame_queue.put_nowait(frame)
        except queue.Full:
            log.warning("sensor.rgb.queue_full", frame_id=raw_image.frame)


class DepthCamera(_BaseCamera):
    """Depth camera sensor.

    Produces frames with shape ``(H, W, 1)`` float32 representing metric
    depth in metres (decoded from CARLA's logarithmic depth encoding).
    """

    _BLUEPRINT_ID = "sensor.camera.depth"

    def _on_image(self, raw_image: Any) -> None:
        raw_image.convert(carla.ColorConverter.Depth)  # type: ignore[union-attr]
        array = np.frombuffer(raw_image.raw_data, dtype=np.uint8)
        array = array.reshape((raw_image.height, raw_image.width, 4))

        # Decode to metric depth: depth = (R + G*256 + B*256*256) / (256^3 - 1) * 1000
        r = array[:, :, 0].astype(np.float32)
        g = array[:, :, 1].astype(np.float32)
        b = array[:, :, 2].astype(np.float32)
        depth_m = (r + g * 256.0 + b * 65536.0) / 16777215.0 * 1000.0

        frame = CameraFrame(
            image=depth_m[:, :, np.newaxis],
            frame_id=raw_image.frame,
            timestamp=raw_image.timestamp,
            sensor_transform=raw_image.transform,
            metadata={"unit": "metres"},
        )
        try:
            self._frame_queue.put_nowait(frame)
        except queue.Full:
            log.warning("sensor.depth.queue_full", frame_id=raw_image.frame)
