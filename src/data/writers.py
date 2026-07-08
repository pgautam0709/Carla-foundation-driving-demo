"""
src/data/writers.py — Writers for Phase 2 episode data collection.

Three writer classes, each focused on a single concern:

- :class:`JSONLWriter` — append JSON objects to a ``.jsonl`` file.
- :class:`FrameWriter` — write deterministically named PNG frames.
- :class:`EpisodeWriter` — orchestrates all writers for a single episode.

All writers must be used as context managers.  ``EpisodeWriter`` is the
primary entry point for collection code.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from src.data.episode import EpisodeDirectory
from src.data.schemas import (
    SCHEMA_VERSION,
    ControlRecord,
    EpisodeManifest,
    EpisodeMetadata,
    EventRecord,
    RouteDefinition,
    TelemetryRecord,
)
from src.utils.logging import get_logger

log = get_logger(__name__)


# ── JSONLWriter ────────────────────────────────────────────────────────────────

class JSONLWriter:
    """Write one JSON object per line to a ``.jsonl`` file.

    Args:
        path: Destination file path.

    Usage::

        with JSONLWriter(path) as w:
            w.write({"key": "value"})
        print(w.count)  # 1
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[str] | None = None
        self._count: int = 0

    def __enter__(self) -> JSONLWriter:
        self._file = self._path.open("w", encoding="utf-8")
        return self

    def __exit__(self, *_: Any) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def write(self, record: dict[str, Any]) -> None:
        """Append a JSON object as a single line.

        Args:
            record: A JSON-serializable dict.

        Raises:
            RuntimeError: If called outside the context manager block.
        """
        if self._file is None:
            raise RuntimeError("JSONLWriter must be used as a context manager.")
        self._file.write(json.dumps(record, default=str) + "\n")
        self._count += 1

    @property
    def count(self) -> int:
        """Number of records written so far."""
        return self._count


# ── FrameWriter ────────────────────────────────────────────────────────────────

class FrameWriter:
    """Write deterministically named PNG frames to a camera directory.

    Frame names are zero-padded 6-digit indices: ``000000.png``, ``000001.png``.

    Args:
        camera_dir: Directory to write frames into (must exist).
    """

    def __init__(self, camera_dir: Path) -> None:
        self._dir = camera_dir
        self._count: int = 0

    def write_frame(self, image_bytes: bytes, frame_idx: int) -> Path:
        """Write a PNG frame at the given index position.

        Args:
            image_bytes: Raw PNG-encoded bytes.
            frame_idx: Zero-based frame index (determines filename).

        Returns:
            Absolute path of the written file.

        Raises:
            ValueError: If frame_idx is negative.
        """
        if frame_idx < 0:
            raise ValueError(f"frame_idx must be non-negative, got {frame_idx}")
        path = self._dir / f"{frame_idx:06d}.png"
        path.write_bytes(image_bytes)
        self._count += 1
        return path

    @property
    def count(self) -> int:
        """Number of frames written so far."""
        return self._count


# ── EpisodeWriter ──────────────────────────────────────────────────────────────

class EpisodeWriter:
    """Orchestrates all writers for a single data collection episode.

    Opens all JSONL writers, creates the directory structure, and provides a
    unified interface for writing controls, telemetry, events, and frames.
    Finalize the episode by calling :meth:`finalize_manifest`.

    Args:
        episode_dir: An :class:`~src.data.episode.EpisodeDirectory` instance
            that controls path layout.

    Usage::

        with EpisodeWriter(ep_dir) as writer:
            writer.write_metadata(metadata)
            writer.write_route(route)
            writer.write_event(EventRecord(...))
            for tick in range(ticks):
                writer.write_control(ctrl_record)
                writer.write_telemetry(telem_record)
                writer.write_frame(frame_bytes, tick)
            manifest = writer.finalize_manifest(status="success")
    """

    def __init__(self, episode_dir: EpisodeDirectory) -> None:
        self._dir = episode_dir
        self._controls_w: JSONLWriter | None = None
        self._telemetry_w: JSONLWriter | None = None
        self._events_w: JSONLWriter | None = None
        self._frames_w: FrameWriter | None = None

    def __enter__(self) -> EpisodeWriter:
        self._dir.create()
        log.info("episode_writer.open", episode_id=self._dir.episode_id)

        self._controls_w  = JSONLWriter(self._dir.controls_path).__enter__()
        self._telemetry_w = JSONLWriter(self._dir.telemetry_path).__enter__()
        self._events_w    = JSONLWriter(self._dir.events_path).__enter__()
        self._frames_w    = FrameWriter(self._dir.front_camera_dir)
        return self

    def __exit__(self, *args: Any) -> None:
        for writer in (self._controls_w, self._telemetry_w, self._events_w):
            if writer is not None:
                try:
                    writer.__exit__(*args)
                except Exception as exc:
                    log.warning("episode_writer.close_error", error=str(exc))
        log.info("episode_writer.closed", episode_id=self._dir.episode_id)

    # ── Write helpers ──────────────────────────────────────────────────────────

    def write_metadata(self, metadata: EpisodeMetadata) -> None:
        """Serialise and write ``metadata.json``.

        Args:
            metadata: The episode metadata record.
        """
        self._dir.metadata_path.write_text(
            json.dumps(dataclasses.asdict(metadata), indent=2, default=str),
            encoding="utf-8",
        )

    def write_route(self, route: RouteDefinition) -> None:
        """Serialise and write ``route.json``.

        Args:
            route: The route definition record.
        """
        self._dir.route_path.write_text(
            json.dumps(dataclasses.asdict(route), indent=2, default=str),
            encoding="utf-8",
        )

    def write_control(self, record: ControlRecord) -> None:
        """Append a control record to ``controls.jsonl``.

        Args:
            record: The tick's control state.
        """
        self._assert_open()
        self._controls_w.write(record.to_dict())  # type: ignore[union-attr]

    def write_telemetry(self, record: TelemetryRecord) -> None:
        """Append a telemetry record to ``telemetry.jsonl``.

        Args:
            record: The tick's vehicle state.
        """
        self._assert_open()
        self._telemetry_w.write(record.to_dict())  # type: ignore[union-attr]

    def write_event(self, record: EventRecord) -> None:
        """Append an event record to ``events.jsonl``.

        Args:
            record: The event.
        """
        self._assert_open()
        self._events_w.write(record.to_dict())  # type: ignore[union-attr]

    def write_frame(self, image_bytes: bytes, frame_idx: int) -> Path:
        """Write a PNG frame with a deterministic filename.

        Args:
            image_bytes: PNG-encoded bytes.
            frame_idx: Zero-based frame index.

        Returns:
            Path of the written file.
        """
        self._assert_open()
        return self._frames_w.write_frame(image_bytes, frame_idx)  # type: ignore[union-attr]

    # ── Manifest ───────────────────────────────────────────────────────────────

    def finalize_manifest(self, status: str = "success") -> EpisodeManifest:
        """Write ``manifest.json`` and return the manifest object.

        Collects the final file list and counts from the writers, then writes
        the manifest.  Call this **before** exiting the context manager so
        the counts are accurate.

        Args:
            status: Episode outcome: ``"success"``, ``"partial"``, or
                ``"failed"``.

        Returns:
            The :class:`~src.data.schemas.EpisodeManifest` that was written.
        """
        # Collect all files relative to episode root
        files = sorted(
            str(p.relative_to(self._dir.root))
            for p in self._dir.root.rglob("*")
            if p.is_file() and p.name != "manifest.json"
        )

        manifest = EpisodeManifest(
            episode_id=self._dir.episode_id,
            schema_version=SCHEMA_VERSION,
            files=files,
            frame_count=self._frames_w.count if self._frames_w else 0,
            control_row_count=self._controls_w.count if self._controls_w else 0,
            telemetry_row_count=self._telemetry_w.count if self._telemetry_w else 0,
            event_count=self._events_w.count if self._events_w else 0,
            status=status,
            validation_status="unchecked",
            completed_at=datetime.now(tz=timezone.utc).isoformat(),
        )

        self._dir.manifest_path.write_text(
            json.dumps(dataclasses.asdict(manifest), indent=2, default=str),
            encoding="utf-8",
        )
        log.info(
            "episode_writer.manifest_written",
            episode_id=self._dir.episode_id,
            frames=manifest.frame_count,
            controls=manifest.control_row_count,
            telemetry=manifest.telemetry_row_count,
            status=status,
        )
        return manifest

    # ── Internal ───────────────────────────────────────────────────────────────

    def _assert_open(self) -> None:
        if self._controls_w is None:
            raise RuntimeError("EpisodeWriter must be used as a context manager.")
