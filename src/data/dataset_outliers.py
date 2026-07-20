"""
src/data/dataset_outliers.py — Signal outlier detection for Phase 3b hardening.

Flags two collection-quality signals directly from an episode's
``controls.jsonl`` and ``telemetry.jsonl``, independent of alignment or
validity:

- **Steering spikes** — a large frame-to-frame jump in the steer signal,
  which usually indicates a control glitch rather than genuine driving
  input (steering does not teleport at 20 Hz).
- **Stuck throttle** — throttle held near maximum while speed stays near
  zero for a sustained run of ticks, indicating the vehicle is wedged
  against an obstacle rather than driving.

Detection is purely informational: it never excludes an episode from the
dataset, only records findings for :mod:`src.data.dataset_builder` to fold
into ``quality_report.json``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from src.data.dataset_io import read_jsonl_records


@dataclasses.dataclass
class OutlierThresholds:
    """Configurable thresholds for outlier detection.

    Args:
        steering_spike_delta: A per-tick ``|steer[t] - steer[t-1]|`` greater
            than this counts as a steering spike.
        stuck_throttle_min: Throttle at or above this value counts as
            "full throttle" for the stuck-throttle check.
        stuck_speed_max_kph: Speed at or below this value counts as
            "not moving" for the stuck-throttle check.
        stuck_throttle_min_ticks: Minimum length of a consecutive
            full-throttle-and-not-moving run before it is flagged.
    """

    steering_spike_delta: float = 0.6
    stuck_throttle_min: float = 0.9
    stuck_speed_max_kph: float = 1.0
    stuck_throttle_min_ticks: int = 40

    def to_dict(self) -> dict[str, float]:
        """Return a JSON-serializable dict (for embedding in the manifest)."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class OutlierResult:
    """Outlier findings for a single episode.

    Args:
        steering_spike_count: Number of ticks whose steering delta exceeded
            ``steering_spike_delta``.
        steering_spike_max_delta: Largest observed ``|Δsteer|``, or 0.0 if
            there were fewer than 2 control rows.
        stuck_throttle_max_run: Longest consecutive run of ticks meeting the
            stuck-throttle condition.
        issues: Human-readable descriptions of findings (empty if none).
    """

    steering_spike_count: int
    steering_spike_max_delta: float
    stuck_throttle_max_run: int
    issues: list[str]


def check_outliers(episode_dir: Path, thresholds: OutlierThresholds) -> OutlierResult:
    """Check one episode's controls/telemetry for steering spikes and stuck throttle.

    Args:
        episode_dir: Episode root directory.
        thresholds: Detection thresholds.

    Returns:
        An :class:`OutlierResult`. Empty/absent files simply yield no
        findings — this check does not require the episode to be valid or
        aligned.
    """
    controls = read_jsonl_records(episode_dir / "controls.jsonl")
    telemetry = read_jsonl_records(episode_dir / "telemetry.jsonl")

    steer = [float(c.get("steer", 0.0)) for c in controls]
    throttle = [float(c.get("throttle", 0.0)) for c in controls]
    speed = [float(t.get("speed_kph", 0.0)) for t in telemetry]

    spike_count = 0
    max_delta = 0.0
    for i in range(1, len(steer)):
        delta = abs(steer[i] - steer[i - 1])
        max_delta = max(max_delta, delta)
        if delta > thresholds.steering_spike_delta:
            spike_count += 1

    stuck_run = 0
    max_stuck_run = 0
    for i in range(min(len(throttle), len(speed))):
        full_throttle = throttle[i] >= thresholds.stuck_throttle_min
        not_moving = speed[i] <= thresholds.stuck_speed_max_kph
        if full_throttle and not_moving:
            stuck_run += 1
            max_stuck_run = max(max_stuck_run, stuck_run)
        else:
            stuck_run = 0

    issues: list[str] = []
    if spike_count > 0:
        issues.append(
            f"{spike_count} steering spike(s) exceeding |Δsteer| > "
            f"{thresholds.steering_spike_delta} (max observed {max_delta:.2f})"
        )
    if max_stuck_run >= thresholds.stuck_throttle_min_ticks:
        issues.append(
            f"stuck-throttle: {max_stuck_run} consecutive ticks with throttle >= "
            f"{thresholds.stuck_throttle_min} and speed <= {thresholds.stuck_speed_max_kph} kph"
        )

    return OutlierResult(
        steering_spike_count=spike_count,
        steering_spike_max_delta=max_delta,
        stuck_throttle_max_run=max_stuck_run,
        issues=issues,
    )
