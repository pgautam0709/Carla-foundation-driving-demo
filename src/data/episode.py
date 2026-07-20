"""
src/data/episode.py — Episode identity and directory layout for Phase 2.

Provides deterministic episode ID generation, route hashing, git context
capture, and a single class (EpisodeDirectory) that owns all paths within
an episode directory.  No external dependencies beyond the standard library.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION: str = "2.0"

# Separator used between episode ID components.
_SEP = "_"


# ── Episode ID ─────────────────────────────────────────────────────────────────

def generate_episode_id(
    town: str,
    route_name: str,
    profile: str,
    timestamp: datetime | None = None,
) -> str:
    """Generate a deterministic, URL-safe episode identifier.

    Format::

        episode_YYYYMMDD_HHMMSS_<town>_<route>_<profile>

    Examples::

        episode_20260707_143012_Town01_routeA_macos_docker
        episode_20260707_143012_Town03_highway_remote_carla

    Args:
        town: CARLA map name (e.g. ``"Town03"``).
        route_name: Human-readable route label.
        profile: Runtime profile name (e.g. ``"macos_docker"``).
        timestamp: Explicit UTC datetime; defaults to :func:`datetime.utcnow`.

    Returns:
        A URL-safe episode ID string.
    """
    ts = (timestamp or datetime.utcnow()).strftime("%Y%m%d_%H%M%S")

    # Strip non-alphanumeric chars; preserve underscores for profile
    town_clean = re.sub(r"[^a-zA-Z0-9]", "", town)
    route_clean = re.sub(r"[^a-zA-Z0-9]", "", route_name)[:16]
    # Allow underscores in profile (macos_docker → kept as-is)
    profile_clean = re.sub(r"[^a-zA-Z0-9_]", "_", profile).strip(_SEP)[:20]

    return f"episode_{ts}_{town_clean}_{route_clean}_{profile_clean}"


# ── Route hash ─────────────────────────────────────────────────────────────────

def compute_route_hash(route: dict[str, Any]) -> str:
    """Return the first 8 hex characters of the SHA-256 hash of the route dict.

    The dict is canonicalised (sorted keys) before hashing so that insertion
    order does not affect the result.

    Args:
        route: Any dict representing the route (start/end transforms, town,
            route name, etc.).

    Returns:
        8-character lowercase hex string (e.g. ``"a3f2b1c9"``).
    """
    canonical = json.dumps(route, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:8]


# ── Git context ────────────────────────────────────────────────────────────────

def get_git_commit() -> str | None:
    """Return the current git HEAD short hash, or None if unavailable.

    Returns:
        7-character hex string, or None when not in a git repo or git
        is not installed.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
            if commit:
                return commit
    except Exception:
        pass
    return None


# ── Episode directory layout ───────────────────────────────────────────────────

class EpisodeDirectory:
    """Owns and creates the canonical directory structure for a single episode.

    Directory layout::

        <base_dir>/<episode_id>/
            metadata.json
            route.json
            controls.jsonl
            telemetry.jsonl
            events.jsonl
            manifest.json
            frames/
                front_camera/
                    000000.png
                    000001.png
                    ...

    Args:
        base_dir: Parent directory (e.g. ``Path("data/raw/episodes")``).
        episode_id: Unique episode identifier (from :func:`generate_episode_id`).
    """

    #: Relative path to the main frames subdirectory.
    FRAMES_SUBDIR: str = "frames"
    #: Relative path to the front-camera subdirectory within frames.
    FRONT_CAMERA_SUBDIR: str = "frames/front_camera"

    def __init__(self, base_dir: Path, episode_id: str) -> None:
        self.episode_id = episode_id
        self.root: Path = Path(base_dir) / episode_id

    # ── Directory creation ─────────────────────────────────────────────────────

    def create(self) -> None:
        """Create all required subdirectories (idempotent).

        Raises:
            OSError: If the directory cannot be created.
        """
        (self.root / "frames" / "front_camera").mkdir(parents=True, exist_ok=True)

    # ── File paths ─────────────────────────────────────────────────────────────

    @property
    def metadata_path(self) -> Path:
        """Absolute path to ``metadata.json``."""
        return self.root / "metadata.json"

    @property
    def route_path(self) -> Path:
        """Absolute path to ``route.json``."""
        return self.root / "route.json"

    @property
    def controls_path(self) -> Path:
        """Absolute path to ``controls.jsonl``."""
        return self.root / "controls.jsonl"

    @property
    def telemetry_path(self) -> Path:
        """Absolute path to ``telemetry.jsonl``."""
        return self.root / "telemetry.jsonl"

    @property
    def events_path(self) -> Path:
        """Absolute path to ``events.jsonl``."""
        return self.root / "events.jsonl"

    @property
    def manifest_path(self) -> Path:
        """Absolute path to ``manifest.json``."""
        return self.root / "manifest.json"

    @property
    def front_camera_dir(self) -> Path:
        """Absolute path to the front-camera frame directory."""
        return self.root / "frames" / "front_camera"

    def frame_path(self, frame_idx: int) -> Path:
        """Absolute path to a specific front-camera frame PNG.

        Args:
            frame_idx: Zero-based frame index.

        Returns:
            Path like ``frames/front_camera/000042.png``.
        """
        return self.front_camera_dir / f"{frame_idx:06d}.png"

    def relative_frame_path(self, frame_idx: int) -> str:
        """Return the episode-relative path string for a frame.

        Args:
            frame_idx: Zero-based frame index.

        Returns:
            String like ``"frames/front_camera/000042.png"``.
        """
        return f"frames/front_camera/{frame_idx:06d}.png"

    def required_files(self) -> list[Path]:
        """Return the list of required top-level files (not including frames).

        Returns:
            List of :class:`Path` objects that must exist for a valid episode.
        """
        return [
            self.metadata_path,
            self.route_path,
            self.controls_path,
            self.telemetry_path,
            self.events_path,
            self.manifest_path,
        ]
