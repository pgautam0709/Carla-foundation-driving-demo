"""
src/data/dataset_alignment.py — Frame/control/telemetry alignment checks.

An episode is "aligned" when its front-camera frames, ``controls.jsonl``
rows, and ``telemetry.jsonl`` rows all agree in count and share the same
contiguous ``0..N-1`` tick numbering. Misaligned episodes are not rejected
outright — the dataset builder truncates them to the common usable tick
count (see :mod:`src.data.dataset_builder`), which is why this module
reports ``usable_tick_count`` rather than only a pass/fail verdict.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path


@dataclasses.dataclass
class AlignmentResult:
    """Result of checking one episode's frame/control/telemetry alignment.

    Args:
        frame_count: Number of front-camera PNG frames found on disk.
        control_count: Number of rows in ``controls.jsonl``.
        telemetry_count: Number of rows in ``telemetry.jsonl``.
        usable_tick_count: ``min(frame_count, control_count,
            telemetry_count)`` truncated further to the longest contiguous
            ``0..N-1`` run shared by all three sources.
        aligned: True if frame/control/telemetry counts and tick numbering
            agree exactly with no truncation required.
        issues: Human-readable list of discrepancies (empty when aligned).
    """

    frame_count: int
    control_count: int
    telemetry_count: int
    usable_tick_count: int
    aligned: bool
    issues: list[str]


def check_alignment(episode_dir: Path) -> AlignmentResult:
    """Check frame/control/telemetry alignment for one episode directory.

    Args:
        episode_dir: Episode root directory (see
            :class:`~src.data.episode.EpisodeDirectory`).

    Returns:
        An :class:`AlignmentResult` describing counts, the usable tick
        count, and any discrepancies found.
    """
    camera_dir = episode_dir / "frames" / "front_camera"
    frame_indices = _read_frame_indices(camera_dir)
    control_ticks = _read_jsonl_ticks(episode_dir / "controls.jsonl")
    telemetry_ticks = _read_jsonl_ticks(episode_dir / "telemetry.jsonl")

    frame_count = len(frame_indices)
    control_count = len(control_ticks)
    telemetry_count = len(telemetry_ticks)

    issues: list[str] = []
    if frame_count != control_count:
        issues.append(f"frame_count ({frame_count}) != control_row_count ({control_count})")
    if frame_count != telemetry_count:
        issues.append(f"frame_count ({frame_count}) != telemetry_row_count ({telemetry_count})")
    if control_count != telemetry_count:
        issues.append(
            f"control_row_count ({control_count}) != telemetry_row_count ({telemetry_count})"
        )

    raw_min = min(frame_count, control_count, telemetry_count)
    usable = _contiguous_prefix_length(raw_min, frame_indices, control_ticks, telemetry_ticks)
    if usable < raw_min:
        issues.append(
            f"tick numbering is not contiguous from 0 — usable prefix is {usable}"
            f" of {raw_min} common rows"
        )

    return AlignmentResult(
        frame_count=frame_count,
        control_count=control_count,
        telemetry_count=telemetry_count,
        usable_tick_count=usable,
        aligned=len(issues) == 0,
        issues=issues,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _read_frame_indices(camera_dir: Path) -> list[int]:
    """Return sorted frame indices parsed from ``NNNNNN.png`` filenames.

    Args:
        camera_dir: The ``frames/front_camera`` directory.

    Returns:
        Sorted list of integer frame indices. Empty if the directory is
        absent or contains no frames.
    """
    if not camera_dir.exists():
        return []
    indices: list[int] = []
    for path in camera_dir.glob("*.png"):
        try:
            indices.append(int(path.stem))
        except ValueError:
            continue
    return sorted(indices)


def _read_jsonl_ticks(path: Path) -> list[int]:
    """Return the ``tick`` field from each row of a JSONL file, in file order.

    Stops at the first line that fails to parse — the remaining rows are
    treated as absent so the alignment check surfaces the truncation rather
    than raising.

    Args:
        path: Path to ``controls.jsonl`` or ``telemetry.jsonl``.

    Returns:
        List of tick values in file order. Empty if the file is missing.
    """
    if not path.exists():
        return []
    ticks: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            break
        tick = record.get("tick")
        if not isinstance(tick, int):
            break
        ticks.append(tick)
    return ticks


def _contiguous_prefix_length(
    limit: int,
    frame_indices: list[int],
    control_ticks: list[int],
    telemetry_ticks: list[int],
) -> int:
    """Return the length of the shared ``0..N-1`` prefix across all three sources.

    Args:
        limit: Upper bound to check (typically the raw minimum count).
        frame_indices: Sorted frame indices.
        control_ticks: Tick values from ``controls.jsonl``, in file order.
        telemetry_ticks: Tick values from ``telemetry.jsonl``, in file order.

    Returns:
        The number of leading ticks ``0, 1, 2, ...`` for which a frame, a
        control row, and a telemetry row all exist at that exact index.
    """
    n = 0
    while (
        n < limit
        and n < len(frame_indices) and frame_indices[n] == n
        and n < len(control_ticks) and control_ticks[n] == n
        and n < len(telemetry_ticks) and telemetry_ticks[n] == n
    ):
        n += 1
    return n
