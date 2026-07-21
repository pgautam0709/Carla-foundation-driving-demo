"""
tests/unit/test_dataset_engineering.py — Unit tests for Phase 3 dataset engineering.

All tests are fully CARLA-free, Docker-free, GPU-free, and PyTorch-free. They
exercise:

  TestDiscovery            — discover_episodes over various directory layouts
  TestAlignment            — check_alignment pass/fail/truncation paths
  TestSplits                — assign_split determinism and distribution
  TestStatistics            — compute_statistics aggregation, incl. steering histogram
  TestOutlierDetection       — check_outliers steering-spike / stuck-throttle findings
  TestDuplicateDetection     — find_duplicate_frames exact-match grouping
  TestDatasetBuilder        — build_dataset end-to-end orchestration
  TestQualityReport         — exclusion reasons and quality issue reporting
  TestBuildDatasetCLI        — scripts/build_dataset.py CLI
  TestInspectDatasetCLI      — scripts/inspect_dataset.py CLI

--fix-manifest / write_validation_status tests live in test_episode.py
alongside the rest of EpisodeValidator's coverage.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

import pytest

# ── Ensure src/ and scripts/ are importable ────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.data.dataset_alignment import check_alignment  # noqa: E402
from src.data.dataset_builder import build_dataset  # noqa: E402
from src.data.dataset_discovery import discover_episodes  # noqa: E402
from src.data.dataset_duplicates import find_duplicate_frames  # noqa: E402
from src.data.dataset_outliers import OutlierThresholds, check_outliers  # noqa: E402
from src.data.dataset_schemas import EpisodeIndexEntry, SampleRecord  # noqa: E402
from src.data.dataset_splits import assign_splits  # noqa: E402
from src.data.dataset_statistics import compute_statistics  # noqa: E402
from src.data.episode import EpisodeDirectory  # noqa: E402
from src.data.schemas import (  # noqa: E402
    ControlRecord,
    EpisodeMetadata,
    EventRecord,
    RouteDefinition,
    SensorConfig,
    TelemetryRecord,
)
from src.data.writers import EpisodeWriter  # noqa: E402

_FIXED_DT = datetime(2026, 7, 7, 14, 30, 12, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_png_bytes(w: int = 2, h: int = 2, fill: int = 0) -> bytes:
    """Generate a tiny but valid PNG (2x2 by default) filled with one byte value.

    Varying *fill* produces frames with different byte content — used so
    ``_write_episode`` can generate non-duplicate frames per tick (real
    camera frames differ tick to tick), while duplicate-detection tests can
    still deliberately reuse one *fill* value to construct an actual
    duplicate.
    """
    import struct
    import zlib

    def chunk(ctype: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">II", w, h) + bytes([8, 2, 0, 0, 0]))
    row = bytes([0]) + bytes([fill % 256]) * (w * 3)
    idat = chunk(b"IDAT", zlib.compress(row * h, level=1))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _make_metadata(
    episode_id: str,
    town: str = "Town03",
    route_name: str = "routeA",
    weather: str | None = "ClearNoon",
) -> EpisodeMetadata:
    return EpisodeMetadata(
        episode_id=episode_id,
        created_at=_FIXED_DT.isoformat(),
        schema_version="2.0",
        runtime_profile="local_dev",
        carla_host="localhost",
        carla_port=2000,
        carla_version_expected="0.9.15",
        carla_version_server=None,
        carla_version_client=None,
        town=town,
        weather_preset=weather,
        route_name=route_name,
        route_hash="a3f2b1c9",
        tick_count_target=10,
        fixed_delta_seconds=0.05,
        sensors=[SensorConfig(
            name="front_camera", sensor_type="sensor.camera.rgb",
            width=640, height=480, fov=110.0,
            transform={"x": 1.5, "y": 0.0, "z": 2.4, "pitch": -15.0, "yaw": 0.0, "roll": 0.0},
        )],
        ego_vehicle_blueprint="vehicle.lincoln.mkz_2020",
        git_commit=None,
        collection_mode="dry_run",
        camera_width=640,
        camera_height=480,
        camera_fov=110.0,
    )


def _make_route(town: str = "Town03", route_name: str = "routeA") -> RouteDefinition:
    return RouteDefinition(
        town=town,
        route_name=route_name,
        route_hash="a3f2b1c9",
        start_transform={"x": 0.0, "y": 0.0, "z": 0.5,
                         "pitch": 0.0, "yaw": 0.0, "roll": 0.0},
        destination_transform=None,
        distance_estimate_m=None,
        generation_method="spawn_point",
    )


def _write_episode(
    base_dir: Path,
    episode_id: str,
    ticks: int,
    town: str = "Town03",
    throttle: float = 0.5,
    speed_kph: float = 18.0,
    weather: str | None = "ClearNoon",
) -> Path:
    """Write a fully valid, aligned Phase 2 episode directory and return its root.

    Each tick gets a distinct frame (varying fill byte) so episodes built by
    this helper are not, by construction, one giant duplicate-frame group —
    real camera frames differ tick to tick. Tests that specifically want
    duplicate frames write them explicitly instead.
    """
    ep_dir = EpisodeDirectory(base_dir, episode_id)

    with EpisodeWriter(ep_dir) as writer:
        writer.write_metadata(_make_metadata(episode_id, town=town, weather=weather))
        writer.write_route(_make_route(town=town))
        writer.write_event(EventRecord(
            tick=0, frame=0, timestamp_wall=time.monotonic(),
            event_type="episode_started", payload={},
        ))
        for i in range(ticks):
            writer.write_control(ControlRecord(
                tick=i, frame=i, timestamp_sim=i * 0.05, timestamp_wall=time.monotonic(),
                throttle=throttle, brake=0.0, steer=0.1,
                hand_brake=False, reverse=False, manual_gear_shift=False, gear=1,
            ))
            writer.write_telemetry(TelemetryRecord(
                tick=i, frame=i, timestamp_sim=i * 0.05,
                location={"x": 0.0, "y": 0.0, "z": 0.0},
                rotation={"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
                velocity={"x": 0.0, "y": 0.0, "z": 0.0},
                acceleration=None, speed_mps=speed_kph / 3.6, speed_kph=speed_kph,
                angular_velocity=None, traffic_light_state=None, speed_limit=None,
            ))
            writer.write_frame(_make_png_bytes(fill=i), frame_idx=i)
        writer.write_event(EventRecord(
            tick=max(ticks - 1, 0), frame=max(ticks - 1, 0),
            timestamp_wall=time.monotonic(),
            event_type="episode_completed", payload={"ticks_collected": ticks},
        ))
        writer.finalize_manifest(status="success")
    return ep_dir.root


def _append_extra_control_row(episode_root: Path, tick: int) -> None:
    """Append one control row beyond the frame/telemetry count.

    Produces an episode that is misaligned (frame_count != control_row_count)
    while still passing Phase 2's own EpisodeValidator — frame sequencing is
    untouched, so this isolates alignment-strictness behavior from validity.
    """
    with (episode_root / "controls.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "tick": tick, "frame": tick, "timestamp_sim": tick * 0.05, "timestamp_wall": 0.0,
            "throttle": 0.0, "brake": 0.0, "steer": 0.0,
            "hand_brake": False, "reverse": False,
            "manual_gear_shift": False, "gear": 0,
        }) + "\n")


def _rewrite_controls_steer(episode_root: Path, steer_values: list[float]) -> None:
    """Overwrite each row's ``steer`` field in ``controls.jsonl`` in place.

    Args:
        episode_root: Episode root directory.
        steer_values: New steer value per tick, in tick order. Must have the
            same length as the existing controls.jsonl.
    """
    path = episode_root / "controls.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == len(steer_values)
    for row, steer in zip(rows, steer_values, strict=True):
        row["steer"] = steer
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# TestDiscovery
# ─────────────────────────────────────────────────────────────────────────────

class TestDiscovery:
    def test_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        assert discover_episodes(tmp_path / "missing") == []

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        assert discover_episodes(tmp_path) == []

    def test_discovers_episode_dirs(self, tmp_path: Path) -> None:
        _write_episode(tmp_path, "episode_b", ticks=2)
        _write_episode(tmp_path, "episode_a", ticks=2)
        found = discover_episodes(tmp_path)
        assert [p.name for p in found] == ["episode_a", "episode_b"]

    def test_ignores_dirs_without_metadata(self, tmp_path: Path) -> None:
        _write_episode(tmp_path, "episode_real", ticks=1)
        (tmp_path / "not_an_episode").mkdir()
        found = discover_episodes(tmp_path)
        assert [p.name for p in found] == ["episode_real"]

    def test_ignores_files(self, tmp_path: Path) -> None:
        (tmp_path / "stray_file.txt").write_text("hello")
        assert discover_episodes(tmp_path) == []


# ─────────────────────────────────────────────────────────────────────────────
# TestAlignment
# ─────────────────────────────────────────────────────────────────────────────

class TestAlignment:
    def test_aligned_episode_reports_no_issues(self, tmp_path: Path) -> None:
        root = _write_episode(tmp_path, "episode_ok", ticks=5)
        result = check_alignment(root)
        assert result.aligned
        assert result.usable_tick_count == 5
        assert result.issues == []

    def test_empty_episode_dir_is_trivially_aligned(self, tmp_path: Path) -> None:
        root = tmp_path / "episode_empty"
        root.mkdir()
        result = check_alignment(root)
        assert result.aligned
        assert result.usable_tick_count == 0
        assert result.frame_count == 0

    def test_missing_frame_truncates_usable_count(self, tmp_path: Path) -> None:
        root = _write_episode(tmp_path, "episode_gap", ticks=5)
        (root / "frames" / "front_camera" / "000002.png").unlink()
        result = check_alignment(root)
        assert not result.aligned
        assert result.usable_tick_count == 2  # ticks 0, 1 remain contiguous

    def test_extra_control_row_flags_mismatch(self, tmp_path: Path) -> None:
        root = _write_episode(tmp_path, "episode_extra", ticks=5)
        with (root / "controls.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "tick": 5, "frame": 5, "timestamp_sim": 0.25, "timestamp_wall": 0.0,
                "throttle": 0.0, "brake": 0.0, "steer": 0.0,
                "hand_brake": False, "reverse": False,
                "manual_gear_shift": False, "gear": 0,
            }) + "\n")
        result = check_alignment(root)
        assert not result.aligned
        assert result.control_count == 6
        assert result.usable_tick_count == 5

    def test_corrupt_jsonl_line_truncates_ticks(self, tmp_path: Path) -> None:
        root = _write_episode(tmp_path, "episode_corrupt", ticks=5)
        lines = (root / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()
        lines[3] = "not valid json"
        (root / "telemetry.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = check_alignment(root)
        assert result.telemetry_count == 3
        assert result.usable_tick_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# TestSplits
# ─────────────────────────────────────────────────────────────────────────────

class TestSplits:
    """assign_splits() batch assignment: determinism, proportionality, guarantees."""

    RATIOS: ClassVar[dict[str, float]] = {"train": 0.8, "val": 0.1, "test": 0.1}

    def test_empty_episode_list_returns_empty(self) -> None:
        assert assign_splits([], self.RATIOS, seed=42) == {}

    def test_deterministic_for_same_inputs(self) -> None:
        ids = [f"episode_{i}" for i in range(10)]
        a = assign_splits(ids, self.RATIOS, seed=42)
        b = assign_splits(ids, self.RATIOS, seed=42)
        assert a == b

    def test_every_episode_gets_a_valid_split(self) -> None:
        ids = [f"episode_{i}" for i in range(17)]
        assignment = assign_splits(ids, self.RATIOS, seed=42)
        assert set(assignment) == set(ids)
        assert all(split in self.RATIOS for split in assignment.values())

    def test_assignment_independent_of_input_order(self) -> None:
        ids = [f"episode_{i}" for i in range(10)]
        forward = assign_splits(ids, self.RATIOS, seed=42)
        backward = assign_splits(list(reversed(ids)), self.RATIOS, seed=42)
        assert forward == backward

    def test_different_seeds_reshuffle_assignment(self) -> None:
        ids = [f"episode_{i}" for i in range(30)]
        a = assign_splits(ids, self.RATIOS, seed=1)
        b = assign_splits(ids, self.RATIOS, seed=2)
        assert a != b

    def test_train_never_empty_for_tiny_datasets(self) -> None:
        """The literal requirement: tiny datasets must not leave train empty."""
        for n in range(1, 8):
            ids = [f"episode_{i}" for i in range(n)]
            assignment = assign_splits(ids, self.RATIOS, seed=42)
            assert "train" in assignment.values(), f"train empty for n={n}: {assignment}"

    def test_exact_proportional_counts_for_evenly_divisible_n(self) -> None:
        ids = [f"episode_{i}" for i in range(100)]
        assignment = assign_splits(ids, self.RATIOS, seed=42)
        counts = {"train": 0, "val": 0, "test": 0}
        for split in assignment.values():
            counts[split] += 1
        assert counts == {"train": 80, "val": 10, "test": 10}

    def test_single_split_assigns_everyone(self) -> None:
        ids = [f"episode_{i}" for i in range(5)]
        assignment = assign_splits(ids, {"train": 1.0}, seed=1)
        assert all(split == "train" for split in assignment.values())

    def test_unnormalized_ratios_still_work(self) -> None:
        ids = [f"episode_{i}" for i in range(10)]
        assignment = assign_splits(ids, {"train": 8.0, "val": 1.0, "test": 1.0}, seed=42)
        assert all(split in ("train", "val", "test") for split in assignment.values())

    def test_empty_ratios_raises(self) -> None:
        with pytest.raises(ValueError):
            assign_splits(["episode_1"], {}, seed=42)

    def test_zero_sum_ratios_raises(self) -> None:
        with pytest.raises(ValueError):
            assign_splits(["episode_1"], {"train": 0.0, "val": 0.0}, seed=42)


# ─────────────────────────────────────────────────────────────────────────────
# TestStatistics
# ─────────────────────────────────────────────────────────────────────────────

class TestStatistics:
    def test_empty_inputs_produce_zero_counts_and_none_stats(self) -> None:
        stats = compute_statistics([], [])
        assert stats.episode_count == 0
        assert stats.sample_count == 0
        assert stats.throttle is None
        assert stats.brake is None
        assert stats.steer is None
        assert stats.speed_kph is None
        assert stats.steering_histogram == []

    def _episode(
        self, episode_id: str, town: str, included: bool, weather: str | None = "ClearNoon",
    ) -> EpisodeIndexEntry:
        return EpisodeIndexEntry(
            episode_id=episode_id, episode_dir=f"/tmp/{episode_id}",
            town=town, weather=weather, route_name="routeA", collection_mode="dry_run",
            created_at=_FIXED_DT.isoformat(),
            frame_count=2, control_row_count=2, telemetry_row_count=2,
            valid=True, validation_errors=[], aligned=True, alignment_issues=[],
            usable_tick_count=2, included=included,
            exclusion_reason=None if included else "test exclusion",
            truncated=False, split="train",
        )

    def _sample(
        self, episode_id: str, throttle: float, speed_kph: float, split: str,
        steer: float = 0.0,
    ) -> SampleRecord:
        return SampleRecord(
            sample_id=f"{episode_id}_000000", episode_id=episode_id, tick=0,
            frame_path=f"/tmp/{episode_id}/frames/front_camera/000000.png",
            throttle=throttle, brake=0.0, steer=steer, speed_kph=speed_kph, split=split,
        )

    def test_computes_mean_min_max(self) -> None:
        episodes = [self._episode("ep1", "Town03", included=True)]
        samples = [
            self._sample("ep1", throttle=0.0, speed_kph=10.0, split="train"),
            self._sample("ep1", throttle=1.0, speed_kph=20.0, split="val"),
        ]
        stats = compute_statistics(episodes, samples)
        assert stats.sample_count == 2
        assert stats.throttle is not None
        assert stats.throttle.mean == pytest.approx(0.5)
        assert stats.throttle.min == 0.0
        assert stats.throttle.max == 1.0
        assert stats.speed_kph is not None
        assert stats.speed_kph.mean == pytest.approx(15.0)

    def test_towns_counted_from_included_episodes_only(self) -> None:
        episodes = [
            self._episode("ep1", "Town01", included=True),
            self._episode("ep2", "Town03", included=False),
        ]
        stats = compute_statistics(episodes, [])
        assert stats.towns == {"Town01": 1}

    def test_weather_counted_from_included_episodes_only(self) -> None:
        episodes = [
            self._episode("ep1", "Town01", included=True, weather="ClearNoon"),
            self._episode("ep2", "Town03", included=False, weather="HardRainNoon"),
        ]
        stats = compute_statistics(episodes, [])
        assert stats.weather == {"ClearNoon": 1}

    def test_weather_omitted_when_not_recorded(self) -> None:
        episodes = [self._episode("ep1", "Town01", included=True, weather=None)]
        stats = compute_statistics(episodes, [])
        assert stats.weather == {}

    def test_split_counts_tally_correctly(self) -> None:
        episodes = [self._episode("ep1", "Town03", included=True)]
        samples = [
            self._sample("ep1", 0.1, 10.0, "train"),
            self._sample("ep1", 0.2, 10.0, "train"),
            self._sample("ep1", 0.3, 10.0, "val"),
        ]
        stats = compute_statistics(episodes, samples)
        assert stats.split_counts.train == 2
        assert stats.split_counts.val == 1
        assert stats.split_counts.test == 0

    def test_steering_histogram_bin_count_matches_requested_bins(self) -> None:
        episodes = [self._episode("ep1", "Town03", included=True)]
        samples = [self._sample("ep1", 0.0, 10.0, "train", steer=s) for s in (-0.9, 0.0, 0.9)]
        stats = compute_statistics(episodes, samples, steering_histogram_bins=5)
        assert len(stats.steering_histogram) == 5
        assert sum(b.count for b in stats.steering_histogram) == 3

    def test_steering_histogram_buckets_extremes_correctly(self) -> None:
        episodes = [self._episode("ep1", "Town03", included=True)]
        samples = [
            self._sample("ep1", 0.0, 10.0, "train", steer=-1.0),
            self._sample("ep1", 0.0, 10.0, "train", steer=1.0),
        ]
        stats = compute_statistics(episodes, samples, steering_histogram_bins=10)
        assert stats.steering_histogram[0].count == 1  # -1.0 falls in the first bin
        assert stats.steering_histogram[-1].count == 1  # +1.0 clamps into the last bin

    def test_steering_histogram_empty_when_no_samples(self) -> None:
        stats = compute_statistics([], [], steering_histogram_bins=10)
        assert stats.steering_histogram == []


# ─────────────────────────────────────────────────────────────────────────────
# TestOutlierDetection
# ─────────────────────────────────────────────────────────────────────────────

class TestOutlierDetection:
    """check_outliers(): steering-spike and stuck-throttle findings."""

    def test_no_findings_for_smooth_normal_episode(self, tmp_path: Path) -> None:
        root = _write_episode(tmp_path, "episode_normal", ticks=20)
        result = check_outliers(root, OutlierThresholds())
        assert result.issues == []
        assert result.steering_spike_count == 0
        assert result.stuck_throttle_max_run == 0

    def test_detects_steering_spike(self, tmp_path: Path) -> None:
        root = _write_episode(tmp_path, "episode_spike", ticks=5)
        # tick 1→2 jumps by 0.9 and then holds — exactly one qualifying delta.
        _rewrite_controls_steer(root, [0.0, 0.0, 0.9, 0.9, 0.9])
        result = check_outliers(root, OutlierThresholds(steering_spike_delta=0.6))
        assert result.steering_spike_count == 1
        assert result.steering_spike_max_delta == pytest.approx(0.9)
        assert any("spike" in issue for issue in result.issues)

    def test_no_spike_below_threshold(self, tmp_path: Path) -> None:
        root = _write_episode(tmp_path, "episode_small_delta", ticks=5)
        _rewrite_controls_steer(root, [0.0, 0.1, 0.2, 0.1, 0.0])
        result = check_outliers(root, OutlierThresholds(steering_spike_delta=0.6))
        assert result.steering_spike_count == 0

    def test_detects_stuck_throttle(self, tmp_path: Path) -> None:
        root = _write_episode(tmp_path, "episode_stuck", ticks=50, throttle=0.95, speed_kph=0.0)
        result = check_outliers(root, OutlierThresholds(
            stuck_throttle_min=0.9, stuck_speed_max_kph=1.0, stuck_throttle_min_ticks=40,
        ))
        assert result.stuck_throttle_max_run >= 40
        assert any("stuck-throttle" in issue for issue in result.issues)

    def test_no_stuck_throttle_when_moving(self, tmp_path: Path) -> None:
        root = _write_episode(tmp_path, "episode_moving", ticks=50, throttle=0.95, speed_kph=40.0)
        result = check_outliers(root, OutlierThresholds(
            stuck_throttle_min=0.9, stuck_speed_max_kph=1.0, stuck_throttle_min_ticks=40,
        ))
        assert result.stuck_throttle_max_run == 0

    def test_missing_files_yield_no_findings(self, tmp_path: Path) -> None:
        root = tmp_path / "episode_empty"
        root.mkdir()
        result = check_outliers(root, OutlierThresholds())
        assert result.issues == []


# ─────────────────────────────────────────────────────────────────────────────
# TestDuplicateDetection
# ─────────────────────────────────────────────────────────────────────────────

class TestDuplicateDetection:
    """find_duplicate_frames(): exact byte-identical frame grouping."""

    def _sample(self, episode_id: str, tick: int, frame_path: Path) -> SampleRecord:
        return SampleRecord(
            sample_id=f"{episode_id}_{tick:06d}", episode_id=episode_id, tick=tick,
            frame_path=str(frame_path), throttle=0.0, brake=0.0, steer=0.0,
            speed_kph=0.0, split="train",
        )

    def test_no_duplicates_for_distinct_frames(self, tmp_path: Path) -> None:
        samples = []
        for i in range(3):
            path = tmp_path / f"frame_{i}.png"
            path.write_bytes(_make_png_bytes(fill=i))
            samples.append(self._sample("ep1", i, path))
        assert find_duplicate_frames(samples) == []

    def test_finds_duplicate_within_one_episode(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared.png"
        shared.write_bytes(_make_png_bytes(fill=7))
        samples = [self._sample("ep1", 0, shared), self._sample("ep1", 1, shared)]
        groups = find_duplicate_frames(samples)
        assert len(groups) == 1
        assert set(groups[0].sample_ids) == {"ep1_000000", "ep1_000001"}
        assert groups[0].episode_ids == ["ep1"]

    def test_finds_duplicate_across_episodes(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared.png"
        shared.write_bytes(_make_png_bytes(fill=3))
        samples = [self._sample("ep1", 0, shared), self._sample("ep2", 0, shared)]
        groups = find_duplicate_frames(samples)
        assert len(groups) == 1
        assert groups[0].episode_ids == ["ep1", "ep2"]

    def test_missing_frame_file_skipped_not_raised(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.png"
        samples = [self._sample("ep1", 0, missing)]
        assert find_duplicate_frames(samples) == []

    def test_empty_sample_list_returns_empty(self) -> None:
        assert find_duplicate_frames([]) == []


# ─────────────────────────────────────────────────────────────────────────────
# TestDatasetBuilder
# ─────────────────────────────────────────────────────────────────────────────

class TestDatasetBuilder:
    RATIOS: ClassVar[dict[str, float]] = {"train": 0.8, "val": 0.1, "test": 0.1}

    def test_empty_raw_dir_produces_all_output_files(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        out = tmp_path / "out"
        manifest = build_dataset(
            raw_episodes_dir=raw, output_dir=out,
            split_ratios=self.RATIOS, split_seed=42,
        )
        assert manifest.episode_count_discovered == 0
        assert manifest.sample_count == 0
        for fname in ("dataset_manifest.json", "episodes_index.jsonl",
                      "samples_index.jsonl", "stats.json", "quality_report.json"):
            assert (out / fname).exists()
        for split_name in ("train", "val", "test"):
            assert (out / "splits" / f"{split_name}.jsonl").exists()

    def test_builds_all_output_files(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=5)
        _write_episode(raw, "episode_2", ticks=3)
        out = tmp_path / "out"
        manifest = build_dataset(
            raw_episodes_dir=raw, output_dir=out,
            split_ratios=self.RATIOS, split_seed=42,
        )
        assert manifest.episode_count_discovered == 2
        assert manifest.episode_count_included == 2
        assert manifest.sample_count == 8
        for fname in ("dataset_manifest.json", "episodes_index.jsonl",
                      "samples_index.jsonl", "stats.json", "quality_report.json"):
            assert (out / fname).exists()
        for split_name in ("train", "val", "test"):
            assert (out / "splits" / f"{split_name}.jsonl").exists()

    def test_episode_index_records_weather_from_metadata(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=3, weather="HardRainNoon")
        out = tmp_path / "out"
        build_dataset(raw_episodes_dir=raw, output_dir=out,
                       split_ratios=self.RATIOS, split_seed=42)
        rows = [json.loads(line) for line in
                (out / "episodes_index.jsonl").read_text().splitlines() if line]
        assert rows[0]["weather"] == "HardRainNoon"
        stats = json.loads((out / "stats.json").read_text())
        assert stats["weather"] == {"HardRainNoon": 1}  # 1 episode, not 3 (sample count)

    def test_splits_files_partition_samples_index(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=5)
        _write_episode(raw, "episode_2", ticks=5)
        _write_episode(raw, "episode_3", ticks=5)
        out = tmp_path / "out"
        build_dataset(raw_episodes_dir=raw, output_dir=out,
                       split_ratios=self.RATIOS, split_seed=42)
        all_ids = {
            json.loads(line)["sample_id"]
            for line in (out / "samples_index.jsonl").read_text().splitlines() if line
        }
        split_ids: set[str] = set()
        for split_name in ("train", "val", "test"):
            rows = [json.loads(line) for line in
                    (out / "splits" / f"{split_name}.jsonl").read_text().splitlines() if line]
            assert all(row["split"] == split_name for row in rows)
            split_ids.update(row["sample_id"] for row in rows)
        assert split_ids == all_ids

    def test_tiny_dataset_train_split_not_empty(self, tmp_path: Path) -> None:
        """Regression test for the empty-train bug found during closure review."""
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=20)
        _write_episode(raw, "episode_2", ticks=20)
        out = tmp_path / "out"
        build_dataset(raw_episodes_dir=raw, output_dir=out,
                       split_ratios=self.RATIOS, split_seed=42)
        stats = json.loads((out / "stats.json").read_text())
        assert stats["split_counts"]["train"] > 0

    def test_misaligned_episode_excluded_by_default(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        root = _write_episode(raw, "episode_misaligned", ticks=10)
        _append_extra_control_row(root, tick=10)
        out = tmp_path / "out"
        manifest = build_dataset(
            raw_episodes_dir=raw, output_dir=out,
            split_ratios=self.RATIOS, split_seed=42,
        )
        assert manifest.episode_count_included == 0
        assert manifest.episode_count_excluded == 1
        assert manifest.sample_count == 0

    def test_misaligned_episode_included_and_truncated_when_allowed(
        self, tmp_path: Path,
    ) -> None:
        raw = tmp_path / "raw"
        root = _write_episode(raw, "episode_misaligned", ticks=10)
        _append_extra_control_row(root, tick=10)
        out = tmp_path / "out"
        manifest = build_dataset(
            raw_episodes_dir=raw, output_dir=out,
            split_ratios=self.RATIOS, split_seed=42, allow_partial_alignment=True,
        )
        assert manifest.episode_count_included == 1
        assert manifest.sample_count == 10  # truncated to the usable prefix

        entries = [json.loads(line) for line in
                   (out / "episodes_index.jsonl").read_text().splitlines() if line]
        assert entries[0]["truncated"] is True
        assert entries[0]["aligned"] is False
        assert entries[0]["usable_tick_count"] == 10

    def test_samples_index_rows_have_valid_split(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=4)
        out = tmp_path / "out"
        build_dataset(raw_episodes_dir=raw, output_dir=out,
                       split_ratios=self.RATIOS, split_seed=42)
        rows = [json.loads(line) for line in
                (out / "samples_index.jsonl").read_text().splitlines() if line]
        assert len(rows) == 4
        assert all(row["split"] in self.RATIOS for row in rows)
        assert all(row["episode_id"] == "episode_1" for row in rows)

    def test_excludes_invalid_episode_when_require_valid_true(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        root = _write_episode(raw, "episode_bad", ticks=3)
        (root / "route.json").unlink()
        out = tmp_path / "out"
        manifest = build_dataset(
            raw_episodes_dir=raw, output_dir=out,
            split_ratios=self.RATIOS, split_seed=42, require_valid=True,
        )
        assert manifest.episode_count_included == 0
        assert manifest.episode_count_excluded == 1
        assert manifest.sample_count == 0

    def test_includes_invalid_episode_when_require_valid_false(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        root = _write_episode(raw, "episode_bad", ticks=3)
        (root / "route.json").unlink()
        out = tmp_path / "out"
        manifest = build_dataset(
            raw_episodes_dir=raw, output_dir=out,
            split_ratios=self.RATIOS, split_seed=42, require_valid=False,
        )
        assert manifest.episode_count_included == 1
        assert manifest.sample_count == 3

    def test_min_episode_ticks_excludes_short_episode(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_short", ticks=2)
        out = tmp_path / "out"
        manifest = build_dataset(
            raw_episodes_dir=raw, output_dir=out,
            split_ratios=self.RATIOS, split_seed=42, min_episode_ticks=10,
        )
        assert manifest.episode_count_included == 0
        assert manifest.episode_count_excluded == 1

    def test_deterministic_rebuild_same_splits(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=3)
        _write_episode(raw, "episode_2", ticks=3)
        out1 = tmp_path / "out1"
        out2 = tmp_path / "out2"
        build_dataset(raw_episodes_dir=raw, output_dir=out1,
                       split_ratios=self.RATIOS, split_seed=7)
        build_dataset(raw_episodes_dir=raw, output_dir=out2,
                       split_ratios=self.RATIOS, split_seed=7)
        idx1 = (out1 / "episodes_index.jsonl").read_text()
        idx2 = (out2 / "episodes_index.jsonl").read_text()
        # created_at/episode_dir differ only if paths differ; splits must match.
        splits1 = [json.loads(line)["split"] for line in idx1.splitlines() if line]
        splits2 = [json.loads(line)["split"] for line in idx2.splitlines() if line]
        assert splits1 == splits2

    def test_dataset_id_defaults_to_output_dir_name(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=3)
        out = tmp_path / "datasets" / "my_build"
        manifest = build_dataset(raw_episodes_dir=raw, output_dir=out,
                                  split_ratios=self.RATIOS, split_seed=42)
        assert manifest.dataset_id == "my_build"

    def test_explicit_dataset_id_overrides_output_dir_name(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=3)
        out = tmp_path / "datasets" / "some_folder"
        manifest = build_dataset(raw_episodes_dir=raw, output_dir=out,
                                  split_ratios=self.RATIOS, split_seed=42,
                                  dataset_id="explicit_id")
        assert manifest.dataset_id == "explicit_id"
        stored = json.loads((out / "dataset_manifest.json").read_text())
        assert stored["dataset_id"] == "explicit_id"

    def test_outlier_detection_flags_stuck_throttle_episode(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_stuck", ticks=50, throttle=0.95, speed_kph=0.0)
        out = tmp_path / "out"
        manifest = build_dataset(raw_episodes_dir=raw, output_dir=out,
                                  split_ratios=self.RATIOS, split_seed=42)
        assert manifest.outlier_detection_enabled is True
        assert manifest.outlier_thresholds is not None
        report = json.loads((out / "quality_report.json").read_text())
        assert report["episodes_with_outliers"] == 1
        assert any("stuck-throttle" in i["message"] for i in report["issues"])

    def test_outlier_detection_disabled_via_flag(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_stuck", ticks=50, throttle=0.95, speed_kph=0.0)
        out = tmp_path / "out"
        manifest = build_dataset(raw_episodes_dir=raw, output_dir=out,
                                  split_ratios=self.RATIOS, split_seed=42,
                                  outlier_detection=False)
        assert manifest.outlier_detection_enabled is False
        assert manifest.outlier_thresholds is None
        report = json.loads((out / "quality_report.json").read_text())
        assert report["episodes_with_outliers"] == 0

    def test_duplicate_detection_flags_identical_frames_across_episodes(
        self, tmp_path: Path,
    ) -> None:
        # _write_episode's frames depend only on tick index, so two episodes
        # with overlapping tick ranges naturally share identical frame bytes
        # per corresponding tick — mirroring how Phase 2 dry-run collection
        # always emits identical solid-black frames.
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_a", ticks=3)
        _write_episode(raw, "episode_b", ticks=3)
        out = tmp_path / "out"
        manifest = build_dataset(raw_episodes_dir=raw, output_dir=out,
                                  split_ratios=self.RATIOS, split_seed=42)
        assert manifest.duplicate_detection_enabled is True
        report = json.loads((out / "quality_report.json").read_text())
        assert report["duplicate_frame_groups"] > 0
        assert any(i["episode_id"] == "<dataset>" for i in report["issues"])

    def test_duplicate_sample_count_sums_group_sizes_not_group_count(
        self, tmp_path: Path,
    ) -> None:
        # 2 episodes x 3 overlapping ticks -> 3 duplicate groups of 2 samples
        # each: duplicate_frame_groups == 3, duplicate_sample_count == 6.
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_a", ticks=3)
        _write_episode(raw, "episode_b", ticks=3)
        out = tmp_path / "out"
        build_dataset(raw_episodes_dir=raw, output_dir=out,
                       split_ratios=self.RATIOS, split_seed=42)
        report = json.loads((out / "quality_report.json").read_text())
        assert report["duplicate_frame_groups"] == 3
        assert report["duplicate_sample_count"] == 6

    def test_duplicate_detection_disabled_via_flag(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_a", ticks=3)
        _write_episode(raw, "episode_b", ticks=3)
        out = tmp_path / "out"
        manifest = build_dataset(raw_episodes_dir=raw, output_dir=out,
                                  split_ratios=self.RATIOS, split_seed=42,
                                  duplicate_detection=False)
        assert manifest.duplicate_detection_enabled is False
        report = json.loads((out / "quality_report.json").read_text())
        assert report["duplicate_frame_groups"] == 0
        assert report["duplicate_sample_count"] == 0

    def test_steering_histogram_bins_configurable(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=5)
        out = tmp_path / "out"
        build_dataset(raw_episodes_dir=raw, output_dir=out,
                       split_ratios=self.RATIOS, split_seed=42, steering_histogram_bins=4)
        stats = json.loads((out / "stats.json").read_text())
        assert len(stats["steering_histogram"]) == 4


# ─────────────────────────────────────────────────────────────────────────────
# TestQualityReport
# ─────────────────────────────────────────────────────────────────────────────

class TestQualityReport:
    RATIOS: ClassVar[dict[str, float]] = {"train": 0.8, "val": 0.1, "test": 0.1}

    def test_issue_recorded_for_invalid_episode(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        root = _write_episode(raw, "episode_bad", ticks=3)
        (root / "manifest.json").unlink()
        out = tmp_path / "out"
        build_dataset(raw_episodes_dir=raw, output_dir=out,
                       split_ratios=self.RATIOS, split_seed=42)
        report = json.loads((out / "quality_report.json").read_text())
        assert report["episodes_invalid"] == 1
        assert any(
            issue["episode_id"] == "episode_bad" and issue["severity"] == "error"
            for issue in report["issues"]
        )

    def test_error_recorded_for_misaligned_episode_excluded_by_default(
        self, tmp_path: Path,
    ) -> None:
        raw = tmp_path / "raw"
        root = _write_episode(raw, "episode_misaligned", ticks=5)
        _append_extra_control_row(root, tick=5)
        out = tmp_path / "out"
        build_dataset(raw_episodes_dir=raw, output_dir=out,
                       split_ratios=self.RATIOS, split_seed=42)
        report = json.loads((out / "quality_report.json").read_text())
        assert report["episodes_misaligned"] == 1
        assert report["episodes_truncated"] == 0
        assert any(
            issue["episode_id"] == "episode_misaligned" and issue["severity"] == "error"
            for issue in report["issues"]
        )

    def test_warning_recorded_for_truncated_episode_when_partial_allowed(
        self, tmp_path: Path,
    ) -> None:
        raw = tmp_path / "raw"
        root = _write_episode(raw, "episode_misaligned", ticks=5)
        _append_extra_control_row(root, tick=5)
        out = tmp_path / "out"
        build_dataset(raw_episodes_dir=raw, output_dir=out,
                       split_ratios=self.RATIOS, split_seed=42, allow_partial_alignment=True)
        report = json.loads((out / "quality_report.json").read_text())
        assert report["episodes_truncated"] == 1
        assert any(
            issue["episode_id"] == "episode_misaligned" and issue["severity"] == "warning"
            and "truncation" in issue["message"]
            for issue in report["issues"]
        )

    def test_split_coverage_warning_recorded_for_tiny_dataset(self, tmp_path: Path) -> None:
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=5)
        out = tmp_path / "out"
        build_dataset(raw_episodes_dir=raw, output_dir=out,
                       split_ratios=self.RATIOS, split_seed=42)
        report = json.loads((out / "quality_report.json").read_text())
        dataset_level = [i for i in report["issues"] if i["episode_id"] == "<dataset>"]
        assert any("val" in i["message"] for i in dataset_level)
        assert any("test" in i["message"] for i in dataset_level)


# ─────────────────────────────────────────────────────────────────────────────
# TestBuildDatasetCLI
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildDatasetCLI:
    def test_help_shows_options(self) -> None:
        from click.testing import CliRunner

        from build_dataset import main
        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--raw-episodes-dir" in result.output
        assert "--dataset-id" in result.output
        assert "--output-dir" in result.output
        assert "--split-seed" in result.output
        assert "--allow-partial-alignment" in result.output
        assert "--outlier-detection" in result.output
        assert "--duplicate-detection" in result.output

    def test_full_run_creates_files(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from build_dataset import main
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=3)
        out = tmp_path / "out"
        result = CliRunner().invoke(main, [
            "--raw-episodes-dir", str(raw),
            "--output-dir", str(out),
            "--split-seed", "1",
        ])
        assert result.exit_code == 0, result.output
        assert (out / "dataset_manifest.json").exists()
        assert (out / "stats.json").exists()
        for split_name in ("train", "val", "test"):
            assert (out / "splits" / f"{split_name}.jsonl").exists()

    def test_allow_partial_alignment_flag_includes_misaligned_episode(
        self, tmp_path: Path,
    ) -> None:
        from click.testing import CliRunner

        from build_dataset import main
        raw = tmp_path / "raw"
        root = _write_episode(raw, "episode_misaligned", ticks=5)
        _append_extra_control_row(root, tick=5)
        out = tmp_path / "out"
        result = CliRunner().invoke(main, [
            "--raw-episodes-dir", str(raw),
            "--output-dir", str(out),
            "--allow-partial-alignment",
        ])
        assert result.exit_code == 0, result.output
        manifest = json.loads((out / "dataset_manifest.json").read_text())
        assert manifest["episode_count_included"] == 1
        assert manifest["allow_partial_alignment"] is True

    def test_no_outlier_and_no_duplicate_detection_flags(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from build_dataset import main
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_stuck", ticks=50, throttle=0.95, speed_kph=0.0)
        out = tmp_path / "out"
        result = CliRunner().invoke(main, [
            "--raw-episodes-dir", str(raw),
            "--output-dir", str(out),
            "--no-outlier-detection",
            "--no-duplicate-detection",
        ])
        assert result.exit_code == 0, result.output
        manifest = json.loads((out / "dataset_manifest.json").read_text())
        assert manifest["outlier_detection_enabled"] is False
        assert manifest["duplicate_detection_enabled"] is False
        report = json.loads((out / "quality_report.json").read_text())
        assert report["episodes_with_outliers"] == 0
        assert report["duplicate_frame_groups"] == 0

    def test_default_output_is_versioned_under_datasets_dir(self, tmp_path: Path) -> None:
        """No --output-dir given: writes under data/processed/datasets/<dataset_id>/."""
        from click.testing import CliRunner

        from build_dataset import main
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=3)
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["--raw-episodes-dir", str(raw)])
            assert result.exit_code == 0, result.output
            datasets_dir = Path("data/processed/datasets")
            subdirs = [p for p in datasets_dir.iterdir() if p.is_dir()]
            assert len(subdirs) == 1
            assert subdirs[0].name.startswith("dataset_")
            assert (subdirs[0] / "dataset_manifest.json").exists()
            manifest = json.loads((subdirs[0] / "dataset_manifest.json").read_text())
            assert manifest["dataset_id"] == subdirs[0].name

    def test_explicit_dataset_id_used_for_default_path(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from build_dataset import main
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=3)
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, [
                "--raw-episodes-dir", str(raw),
                "--dataset-id", "my_dataset",
            ])
            assert result.exit_code == 0, result.output
            expected = Path("data/processed/datasets/my_dataset/dataset_manifest.json")
            assert expected.exists()

    def test_explicit_output_dir_bypasses_datasets_dir_nesting(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from build_dataset import main
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=3)
        runner = CliRunner()
        with runner.isolated_filesystem():
            out = Path("scratch_output")
            result = runner.invoke(main, [
                "--raw-episodes-dir", str(raw),
                "--output-dir", str(out),
            ])
            assert result.exit_code == 0, result.output
            assert (out / "dataset_manifest.json").exists()
            assert not Path("data/processed/datasets").exists()


# ─────────────────────────────────────────────────────────────────────────────
# TestInspectDatasetCLI
# ─────────────────────────────────────────────────────────────────────────────

class TestInspectDatasetCLI:
    def test_help(self) -> None:
        from click.testing import CliRunner

        from inspect_dataset import main
        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--dataset-dir" in result.output

    def test_fails_gracefully_when_no_manifest(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from inspect_dataset import main
        result = CliRunner().invoke(main, ["--dataset-dir", str(tmp_path)])
        assert result.exit_code == 1

    def test_prints_summary_after_build(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from inspect_dataset import main
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=3)
        out = tmp_path / "out"
        build_dataset(
            raw_episodes_dir=raw, output_dir=out,
            split_ratios={"train": 0.8, "val": 0.1, "test": 0.1}, split_seed=1,
        )
        result = CliRunner().invoke(main, ["--dataset-dir", str(out), "--verbose"])
        assert result.exit_code == 0, result.output
        assert "Dataset Inspection" in result.output
        assert "Samples" in result.output
        assert "Misaligned" in result.output
        assert "Truncated" in result.output
        assert "Outliers" in result.output
        assert "Duplicates" in result.output

    def test_shows_steering_histogram(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from inspect_dataset import main
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=5)
        out = tmp_path / "out"
        build_dataset(
            raw_episodes_dir=raw, output_dir=out,
            split_ratios={"train": 0.8, "val": 0.1, "test": 0.1}, split_seed=1,
        )
        result = CliRunner().invoke(main, ["--dataset-dir", str(out)])
        assert result.exit_code == 0, result.output
        assert "Steering histogram" in result.output

    def test_shows_truncated_count_for_partial_alignment_dataset(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from inspect_dataset import main
        raw = tmp_path / "raw"
        root = _write_episode(raw, "episode_misaligned", ticks=5)
        _append_extra_control_row(root, tick=5)
        out = tmp_path / "out"
        build_dataset(
            raw_episodes_dir=raw, output_dir=out,
            split_ratios={"train": 0.8, "val": 0.1, "test": 0.1}, split_seed=1,
            allow_partial_alignment=True,
        )
        result = CliRunner().invoke(main, ["--dataset-dir", str(out), "--verbose"])
        assert result.exit_code == 0, result.output
        assert "Truncated   : 1" in result.output
        assert "truncation" in result.output

    def test_fails_gracefully_when_no_dataset_under_datasets_dir(self) -> None:
        from click.testing import CliRunner

        from inspect_dataset import main
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, [])
            assert result.exit_code == 1
            assert "No dataset found" in result.output

    def test_defaults_to_most_recently_built_dataset(self, tmp_path: Path) -> None:
        """No --dataset-dir given: picks the most recent build under datasets_dir."""
        import time

        from click.testing import CliRunner

        from build_dataset import main as build_main
        from inspect_dataset import main as inspect_main
        raw = tmp_path / "raw"
        _write_episode(raw, "episode_1", ticks=3)
        runner = CliRunner()
        with runner.isolated_filesystem():
            first = runner.invoke(build_main, [
                "--raw-episodes-dir", str(raw), "--dataset-id", "ds_first",
            ])
            assert first.exit_code == 0, first.output
            time.sleep(0.05)
            second = runner.invoke(build_main, [
                "--raw-episodes-dir", str(raw), "--dataset-id", "ds_second",
            ])
            assert second.exit_code == 0, second.output

            result = runner.invoke(inspect_main, [])
            assert result.exit_code == 0, result.output
            assert "ds_second" in result.output
            assert "ds_first" not in result.output
