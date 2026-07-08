"""
src/data/dataset_discovery.py — Discover Phase 2 episode directories.

A directory counts as an episode candidate if it contains ``metadata.json``.
No other Phase 2 file is required to be present at discovery time — a
directory missing further files is still discovered and later reported as
invalid by :class:`~src.data.validation.EpisodeValidator`, rather than being
silently skipped.
"""

from __future__ import annotations

from pathlib import Path


def discover_episodes(raw_episodes_dir: Path) -> list[Path]:
    """Return episode directories under *raw_episodes_dir*, sorted by name.

    Sorting by name makes discovery deterministic across filesystems and
    operating systems; episode IDs are timestamp-prefixed so this also
    yields chronological order.

    Args:
        raw_episodes_dir: Parent directory containing one subdirectory per
            episode (e.g. ``data/raw/episodes``).

    Returns:
        Sorted list of episode directory paths. Empty list if
        *raw_episodes_dir* does not exist or contains no episode candidates.
    """
    if not raw_episodes_dir.is_dir():
        return []

    candidates = [
        entry for entry in raw_episodes_dir.iterdir()
        if entry.is_dir() and (entry / "metadata.json").exists()
    ]
    return sorted(candidates, key=lambda p: p.name)
