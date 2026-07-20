"""
src/data/recorder.py — Episode recorder writing to HDF5.

Each episode produces a single HDF5 file at:
    data/raw/<episode_id>.hdf5

File structure::

    /
    ├── metadata/
    │   ├── episode_id          (str)
    │   ├── map                 (str)
    │   ├── weather             (str)
    │   ├── start_timestamp     (float)
    │   └── config_json         (str, JSON dump of active config)
    └── frames/
        ├── frame_id            (int64[N])
        ├── timestamp           (float64[N])
        ├── rgb                 (uint8[N, H, W, 3])
        ├── throttle            (float32[N])
        ├── steer               (float32[N])
        ├── brake               (float32[N])
        └── speed_kmh           (float32[N])

Usage::

    from src.data.recorder import EpisodeRecorder, EpisodeFrame

    with EpisodeRecorder(output_dir="data/raw", episode_id="ep_001") as rec:
        for frame_id, cam_frame, control, speed in ...:
            rec.record(EpisodeFrame(
                frame_id=frame_id,
                timestamp=cam_frame.timestamp,
                rgb=cam_frame.image,
                throttle=control.throttle,
                steer=control.steer,
                brake=control.brake,
                speed_kmh=speed,
            ))

    print(rec.path)   # path to the written HDF5 file
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

import numpy as np
import numpy.typing as npt

from src.utils.logging import get_logger

log = get_logger(__name__)

try:
    import h5py
    _H5PY_AVAILABLE = True
except ImportError:
    _H5PY_AVAILABLE = False
    h5py = None


# ── Data containers ────────────────────────────────────────────────────────────

@dataclass
class EpisodeFrame:
    """All data captured in a single simulation timestep."""

    frame_id: int
    timestamp: float
    rgb: npt.NDArray[np.uint8]  # (H, W, 3) uint8
    throttle: float             # [0, 1]
    steer: float                # [-1, 1]
    brake: float                # [0, 1]
    speed_kmh: float
    extra: dict[str, Any] = field(default_factory=dict)


# ── Recorder ───────────────────────────────────────────────────────────────────

class EpisodeRecorder:
    """Writes episode frames incrementally to a compressed HDF5 file.

    Uses resizable datasets so frames can be appended without knowing the
    final episode length in advance.

    Args:
        output_dir: Directory where HDF5 files are written.
        episode_id: Unique identifier for this episode. If ``None``, a UUID4
                    is generated.
        config: Configuration dict to embed as JSON metadata.
        compress: Whether to use gzip compression (recommended).
        chunk_frames: HDF5 chunk size along the frame axis.
    """

    def __init__(
        self,
        output_dir: str | Path = "data/raw",
        episode_id: str | None = None,
        config: dict[str, Any] | None = None,
        compress: bool = True,
        chunk_frames: int = 64,
    ) -> None:
        if not _H5PY_AVAILABLE:
            raise ImportError(
                "h5py is required for recording. Install via: uv pip install h5py"
            )

        self._output_dir = Path(output_dir)
        self._episode_id = episode_id or f"ep_{uuid.uuid4().hex[:8]}"
        self._config = config or {}
        self._compress = compress
        self._chunk_frames = chunk_frames

        self._hf: Any = None
        self._datasets: dict[str, Any] = {}
        self._n_frames = 0
        self._start_time: float = 0.0
        self._path: Path | None = None

    # ── Context manager ────────────────────────────────────────────────────────

    def __enter__(self) -> EpisodeRecorder:
        self._open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._close()

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        """Path to the HDF5 file being written."""
        if self._path is None:
            raise RuntimeError("Recorder not open. Use as a context manager.")
        return self._path

    @property
    def n_frames(self) -> int:
        """Number of frames recorded so far."""
        return self._n_frames

    def record(self, frame: EpisodeFrame) -> None:
        """Append a single frame to the episode.

        Args:
            frame: The :class:`EpisodeFrame` to persist.
        """
        if self._hf is None:
            raise RuntimeError("Recorder is not open.")

        idx = self._n_frames
        self._resize_all(idx + 1)

        self._datasets["frame_id"][idx] = frame.frame_id
        self._datasets["timestamp"][idx] = frame.timestamp
        self._datasets["rgb"][idx] = frame.rgb
        self._datasets["throttle"][idx] = frame.throttle
        self._datasets["steer"][idx] = frame.steer
        self._datasets["brake"][idx] = frame.brake
        self._datasets["speed_kmh"][idx] = frame.speed_kmh

        self._n_frames += 1

        if self._n_frames % 100 == 0:
            log.debug(
                "recorder.progress",
                episode_id=self._episode_id,
                n_frames=self._n_frames,
            )

    # ── Internal ───────────────────────────────────────────────────────────────

    def _open(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._output_dir / f"{self._episode_id}.hdf5"
        self._start_time = time.time()

        compression = "gzip" if self._compress else None
        compression_opts = 4 if self._compress else None

        self._hf = h5py.File(str(self._path), "w")

        # Metadata group
        meta = self._hf.create_group("metadata")
        meta.create_dataset("episode_id", data=self._episode_id)
        meta.create_dataset("start_timestamp", data=self._start_time)
        meta.create_dataset("config_json", data=json.dumps(self._config))

        # Frame datasets (maxshape=None → resizable)
        chunks_scalar = (self._chunk_frames,)
        img_h = self._config.get("sensors", {}).get("rgb_camera", {}).get("height", 480)
        img_w = self._config.get("sensors", {}).get("rgb_camera", {}).get("width", 640)
        chunks_img = (min(self._chunk_frames, 8), img_h, img_w, 3)

        frames = self._hf.create_group("frames")

        self._datasets["frame_id"] = frames.create_dataset(
            "frame_id", shape=(0,), maxshape=(None,),
            dtype=np.int64, chunks=chunks_scalar,
        )
        self._datasets["timestamp"] = frames.create_dataset(
            "timestamp", shape=(0,), maxshape=(None,),
            dtype=np.float64, chunks=chunks_scalar,
        )
        self._datasets["rgb"] = frames.create_dataset(
            "rgb", shape=(0, img_h, img_w, 3), maxshape=(None, img_h, img_w, 3),
            dtype=np.uint8, chunks=chunks_img,
            compression=compression, compression_opts=compression_opts,
        )
        for name, dtype in [
            ("throttle", np.float32),
            ("steer", np.float32),
            ("brake", np.float32),
            ("speed_kmh", np.float32),
        ]:
            self._datasets[name] = frames.create_dataset(
                name, shape=(0,), maxshape=(None,),
                dtype=dtype, chunks=chunks_scalar,
            )

        log.info(
            "recorder.opened",
            episode_id=self._episode_id,
            path=str(self._path),
        )

    def _resize_all(self, new_size: int) -> None:
        for _name, ds in self._datasets.items():
            current_shape = list(ds.shape)
            current_shape[0] = new_size
            ds.resize(current_shape)

    def _close(self) -> None:
        if self._hf is not None:
            duration = time.time() - self._start_time
            # Write final metadata
            self._hf["metadata"].create_dataset("n_frames", data=self._n_frames)
            self._hf["metadata"].create_dataset("duration_s", data=duration)
            self._hf.close()
            self._hf = None
            log.info(
                "recorder.closed",
                episode_id=self._episode_id,
                n_frames=self._n_frames,
                duration_s=round(duration, 2),
                path=str(self._path),
            )
