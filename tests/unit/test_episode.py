"""
tests/unit/test_episode.py — Unit tests for Phase 2 data collection modules.

All tests are fully CARLA-free.  They exercise:

  TestEpisodeID            — generate_episode_id format and safety
  TestEpisodeDirectory     — path layout and frame naming
  TestRouteHash            — compute_route_hash determinism
  TestGitCommit            — get_git_commit (presence/type check)
  TestJSONLWriter          — write, count, context manager
  TestFrameWriter          — path naming, count
  TestEpisodeWriter        — full orchestration, manifest counts
  TestSchemas              — dataclass serialisation
  TestEpisodeValidator     — all check types (pass + fail paths)
  TestDryRunCollection     — end-to-end dry-run via _run_dry_run()
  TestCLIParsing           — Click --help and --dry-run exit codes
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ── Ensure src/ and scripts/ are importable ────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.data.episode import (  # noqa: E402
    EpisodeDirectory,
    compute_route_hash,
    generate_episode_id,
    get_git_commit,
)
from src.data.schemas import (  # noqa: E402
    SCHEMA_VERSION,
    ControlRecord,
    EpisodeManifest,
    EpisodeMetadata,
    EventRecord,
    RouteDefinition,
    SensorConfig,
    TelemetryRecord,
)
from src.data.validation import EpisodeValidator, write_validation_status  # noqa: E402
from src.data.writers import EpisodeWriter, FrameWriter, JSONLWriter  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

_FIXED_DT = datetime(2026, 7, 7, 14, 30, 12, tzinfo=timezone.utc)


def _make_sensor() -> SensorConfig:
    return SensorConfig(
        name="front_camera",
        sensor_type="sensor.camera.rgb",
        width=640, height=480, fov=110.0,
        transform={"x": 1.5, "y": 0.0, "z": 2.4,
                   "pitch": -15.0, "yaw": 0.0, "roll": 0.0},
    )


def _make_metadata(episode_id: str) -> EpisodeMetadata:
    return EpisodeMetadata(
        episode_id=episode_id,
        created_at=_FIXED_DT.isoformat(),
        schema_version=SCHEMA_VERSION,
        runtime_profile="local_dev",
        carla_host="localhost",
        carla_port=2000,
        carla_version_expected="0.9.15",
        carla_version_server=None,
        carla_version_client=None,
        town="Town03",
        weather_preset=None,
        route_name="routeA",
        route_hash="a3f2b1c9",
        tick_count_target=10,
        fixed_delta_seconds=0.05,
        sensors=[_make_sensor()],
        ego_vehicle_blueprint="vehicle.lincoln.mkz_2020",
        git_commit=None,
        collection_mode="dry_run",
        camera_width=640,
        camera_height=480,
        camera_fov=110.0,
    )


def _make_route(episode_id: str) -> RouteDefinition:
    route_hash = compute_route_hash({"town": "Town03", "route_name": "routeA"})
    return RouteDefinition(
        town="Town03",
        route_name="routeA",
        route_hash=route_hash,
        start_transform={"x": 0.0, "y": 0.0, "z": 0.5,
                         "pitch": 0.0, "yaw": 0.0, "roll": 0.0},
        destination_transform=None,
        distance_estimate_m=None,
        generation_method="spawn_point",
    )


def _make_black_png_bytes(w: int = 2, h: int = 2) -> bytes:
    """Generate a tiny but valid black PNG (2x2) for testing."""
    import struct
    import zlib
    def chunk(ctype: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">II", w, h) + bytes([8, 2, 0, 0, 0]))
    idat = chunk(b"IDAT", zlib.compress(bytes(1 + w * 3) * h, level=1))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _build_valid_episode(tmp_path: Path, episode_id: str, ticks: int = 3) -> Path:
    """Create a complete valid episode in tmp_path and return episode root."""
    ep_dir = EpisodeDirectory(tmp_path, episode_id)
    meta = _make_metadata(episode_id)
    route = _make_route(episode_id)
    png = _make_black_png_bytes()

    with EpisodeWriter(ep_dir) as writer:
        writer.write_metadata(meta)
        writer.write_route(route)
        writer.write_event(EventRecord(
            tick=0, frame=0, timestamp_wall=time.monotonic(),
            event_type="episode_started", payload={},
        ))
        for i in range(ticks):
            writer.write_control(ControlRecord(
                tick=i, frame=i, timestamp_sim=float(i) * 0.05,
                timestamp_wall=time.monotonic(),
                throttle=0.0, brake=0.0, steer=0.0,
                hand_brake=False, reverse=False,
                manual_gear_shift=False, gear=0,
            ))
            writer.write_telemetry(TelemetryRecord(
                tick=i, frame=i, timestamp_sim=float(i) * 0.05,
                location={"x": 0.0, "y": 0.0, "z": 0.0},
                rotation={"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
                velocity={"x": 0.0, "y": 0.0, "z": 0.0},
                acceleration={"x": 0.0, "y": 0.0, "z": 0.0},
                speed_mps=0.0, speed_kph=0.0,
                angular_velocity={"x": 0.0, "y": 0.0, "z": 0.0},
                traffic_light_state=None, speed_limit=None,
            ))
            writer.write_frame(png, frame_idx=i)
        writer.write_event(EventRecord(
            tick=ticks - 1, frame=ticks - 1,
            timestamp_wall=time.monotonic(),
            event_type="episode_completed",
            payload={"ticks_collected": ticks},
        ))
        writer.finalize_manifest(status="success")
    return ep_dir.root


# ─────────────────────────────────────────────────────────────────────────────
# TestEpisodeID
# ─────────────────────────────────────────────────────────────────────────────

class TestEpisodeID:
    """generate_episode_id format, safety, and component presence."""

    def test_starts_with_episode_prefix(self) -> None:
        eid = generate_episode_id("Town03", "routeA", "local_dev", _FIXED_DT)
        assert eid.startswith("episode_")

    def test_contains_town(self) -> None:
        eid = generate_episode_id("Town03", "routeA", "local_dev", _FIXED_DT)
        assert "Town03" in eid

    def test_contains_route(self) -> None:
        eid = generate_episode_id("Town03", "routeA", "local_dev", _FIXED_DT)
        assert "routeA" in eid

    def test_contains_profile(self) -> None:
        eid = generate_episode_id("Town03", "routeA", "macos_docker", _FIXED_DT)
        assert "macos_docker" in eid or "macos" in eid

    def test_contains_date_prefix(self) -> None:
        eid = generate_episode_id("Town03", "routeA", "local_dev", _FIXED_DT)
        assert "20260707" in eid

    def test_contains_time_component(self) -> None:
        eid = generate_episode_id("Town03", "routeA", "local_dev", _FIXED_DT)
        assert "143012" in eid

    def test_url_safe_characters_only(self) -> None:
        """ID must contain only alphanumerics and underscores."""
        eid = generate_episode_id("Town03", "route-A/test", "macos_docker", _FIXED_DT)
        assert re.match(r"^[a-zA-Z0-9_]+$", eid), f"Non-URL-safe chars in: {eid}"

    def test_deterministic_for_same_inputs(self) -> None:
        eid1 = generate_episode_id("Town01", "routeB", "linux_local", _FIXED_DT)
        eid2 = generate_episode_id("Town01", "routeB", "linux_local", _FIXED_DT)
        assert eid1 == eid2

    def test_different_towns_produce_different_ids(self) -> None:
        eid1 = generate_episode_id("Town01", "routeA", "local_dev", _FIXED_DT)
        eid2 = generate_episode_id("Town03", "routeA", "local_dev", _FIXED_DT)
        assert eid1 != eid2


# ─────────────────────────────────────────────────────────────────────────────
# TestEpisodeDirectory
# ─────────────────────────────────────────────────────────────────────────────

class TestEpisodeDirectory:
    """EpisodeDirectory path layout and frame naming."""

    def test_creates_expected_subdirectory(self, tmp_path: Path) -> None:
        ep = EpisodeDirectory(tmp_path, "episode_test")
        ep.create()
        assert (tmp_path / "episode_test" / "frames" / "front_camera").is_dir()

    def test_frame_path_000000(self, tmp_path: Path) -> None:
        ep = EpisodeDirectory(tmp_path, "ep")
        assert ep.frame_path(0).name == "000000.png"

    def test_frame_path_000099(self, tmp_path: Path) -> None:
        ep = EpisodeDirectory(tmp_path, "ep")
        assert ep.frame_path(99).name == "000099.png"

    def test_frame_path_large_index(self, tmp_path: Path) -> None:
        ep = EpisodeDirectory(tmp_path, "ep")
        assert ep.frame_path(123456).name == "123456.png"

    def test_relative_frame_path_string(self, tmp_path: Path) -> None:
        ep = EpisodeDirectory(tmp_path, "ep")
        assert ep.relative_frame_path(0) == "frames/front_camera/000000.png"

    def test_required_files_list(self, tmp_path: Path) -> None:
        ep = EpisodeDirectory(tmp_path, "ep")
        names = {p.name for p in ep.required_files()}
        assert names == {
            "metadata.json", "route.json", "controls.jsonl",
            "telemetry.jsonl", "events.jsonl", "manifest.json",
        }


# ─────────────────────────────────────────────────────────────────────────────
# TestRouteHash
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteHash:
    """compute_route_hash determinism and length."""

    def test_hash_is_8_characters(self) -> None:
        h = compute_route_hash({"town": "Town03"})
        assert len(h) == 8

    def test_hash_is_hex(self) -> None:
        h = compute_route_hash({"town": "Town03"})
        assert re.match(r"^[0-9a-f]{8}$", h)

    def test_same_dict_same_hash(self) -> None:
        d = {"town": "Town01", "route": "A"}
        assert compute_route_hash(d) == compute_route_hash(d)

    def test_insertion_order_irrelevant(self) -> None:
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 2, "a": 1}
        assert compute_route_hash(d1) == compute_route_hash(d2)

    def test_different_dicts_produce_different_hashes(self) -> None:
        h1 = compute_route_hash({"town": "Town01"})
        h2 = compute_route_hash({"town": "Town03"})
        assert h1 != h2


# ─────────────────────────────────────────────────────────────────────────────
# TestGitCommit
# ─────────────────────────────────────────────────────────────────────────────

class TestGitCommit:
    def test_returns_string_or_none(self) -> None:
        result = get_git_commit()
        assert result is None or isinstance(result, str)

    def test_string_not_empty_when_returned(self) -> None:
        result = get_git_commit()
        if result is not None:
            assert len(result) > 0


# ─────────────────────────────────────────────────────────────────────────────
# TestJSONLWriter
# ─────────────────────────────────────────────────────────────────────────────

class TestJSONLWriter:
    def test_writes_single_record(self, tmp_path: Path) -> None:
        p = tmp_path / "test.jsonl"
        with JSONLWriter(p) as w:
            w.write({"key": "value"})
        lines = [line for line in p.read_text().splitlines() if line]
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"key": "value"}

    def test_writes_multiple_records(self, tmp_path: Path) -> None:
        p = tmp_path / "test.jsonl"
        with JSONLWriter(p) as w:
            w.write({"n": 1})
            w.write({"n": 2})
            w.write({"n": 3})
        lines = [line for line in p.read_text().splitlines() if line]
        assert len(lines) == 3

    def test_each_line_is_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "test.jsonl"
        with JSONLWriter(p) as w:
            for i in range(5):
                w.write({"i": i, "val": float(i) * 0.1})
        for line in p.read_text().splitlines():
            if line:
                json.loads(line)  # must not raise

    def test_count_reflects_writes(self, tmp_path: Path) -> None:
        p = tmp_path / "test.jsonl"
        with JSONLWriter(p) as w:
            for _ in range(7):
                w.write({"x": 1})
            assert w.count == 7

    def test_raises_outside_context(self, tmp_path: Path) -> None:
        p = tmp_path / "test.jsonl"
        w = JSONLWriter(p)
        with pytest.raises(RuntimeError):
            w.write({"bad": True})


# ─────────────────────────────────────────────────────────────────────────────
# TestFrameWriter
# ─────────────────────────────────────────────────────────────────────────────

class TestFrameWriter:
    def test_frame_path_is_000000(self, tmp_path: Path) -> None:
        fw = FrameWriter(tmp_path)
        p = fw.write_frame(_make_black_png_bytes(), 0)
        assert p.name == "000000.png"

    def test_frame_path_sequential(self, tmp_path: Path) -> None:
        fw = FrameWriter(tmp_path)
        paths = [fw.write_frame(_make_black_png_bytes(), i) for i in range(3)]
        assert [p.name for p in paths] == ["000000.png", "000001.png", "000002.png"]

    def test_count_reflects_writes(self, tmp_path: Path) -> None:
        fw = FrameWriter(tmp_path)
        for i in range(4):
            fw.write_frame(_make_black_png_bytes(), i)
        assert fw.count == 4

    def test_written_file_exists(self, tmp_path: Path) -> None:
        fw = FrameWriter(tmp_path)
        p = fw.write_frame(_make_black_png_bytes(), 0)
        assert p.exists()

    def test_negative_index_raises(self, tmp_path: Path) -> None:
        fw = FrameWriter(tmp_path)
        with pytest.raises(ValueError):
            fw.write_frame(_make_black_png_bytes(), -1)


# ─────────────────────────────────────────────────────────────────────────────
# TestEpisodeWriter
# ─────────────────────────────────────────────────────────────────────────────

class TestEpisodeWriter:
    def test_enters_and_exits_cleanly(self, tmp_path: Path) -> None:
        ep = EpisodeDirectory(tmp_path, "ep_test")
        with EpisodeWriter(ep) as w:
            assert w is not None

    def test_creates_directory_structure(self, tmp_path: Path) -> None:
        ep = EpisodeDirectory(tmp_path, "ep_test")
        with EpisodeWriter(ep):
            pass
        assert (ep.front_camera_dir).is_dir()

    def test_writes_metadata_json(self, tmp_path: Path) -> None:
        ep = EpisodeDirectory(tmp_path, "ep_test")
        meta = _make_metadata("ep_test")
        with EpisodeWriter(ep) as w:
            w.write_metadata(meta)
        data = json.loads(ep.metadata_path.read_text())
        assert data["episode_id"] == "ep_test"
        assert data["schema_version"] == SCHEMA_VERSION

    def test_writes_control_records(self, tmp_path: Path) -> None:
        ep = EpisodeDirectory(tmp_path, "ep_test")
        with EpisodeWriter(ep) as w:
            for i in range(5):
                w.write_control(ControlRecord(
                    tick=i, frame=i, timestamp_sim=0.0, timestamp_wall=0.0,
                    throttle=0.0, brake=0.0, steer=0.0,
                    hand_brake=False, reverse=False,
                    manual_gear_shift=False, gear=0,
                ))
        rows = [line for line in ep.controls_path.read_text().splitlines() if line]
        assert len(rows) == 5

    def test_writes_telemetry_records(self, tmp_path: Path) -> None:
        ep = EpisodeDirectory(tmp_path, "ep_test")
        with EpisodeWriter(ep) as w:
            w.write_telemetry(TelemetryRecord(
                tick=0, frame=0, timestamp_sim=0.0,
                location={"x": 1.0, "y": 2.0, "z": 3.0},
                rotation={"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
                velocity={"x": 0.0, "y": 0.0, "z": 0.0},
                acceleration=None, speed_mps=0.0, speed_kph=0.0,
                angular_velocity=None,
                traffic_light_state="Green", speed_limit=30.0,
            ))
        row = json.loads(ep.telemetry_path.read_text().strip())
        assert row["location"]["x"] == 1.0
        assert row["speed_limit"] == 30.0

    def test_finalizes_manifest_with_correct_counts(self, tmp_path: Path) -> None:
        ep = EpisodeDirectory(tmp_path, "ep_test")
        meta = _make_metadata("ep_test")
        with EpisodeWriter(ep) as w:
            w.write_metadata(meta)
            w.write_route(_make_route("ep_test"))
            for i in range(4):
                w.write_control(ControlRecord(
                    tick=i, frame=i, timestamp_sim=0.0, timestamp_wall=0.0,
                    throttle=0.0, brake=0.0, steer=0.0,
                    hand_brake=False, reverse=False,
                    manual_gear_shift=False, gear=0,
                ))
                w.write_telemetry(TelemetryRecord(
                    tick=i, frame=i, timestamp_sim=0.0,
                    location={"x": 0.0, "y": 0.0, "z": 0.0},
                    rotation={"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
                    velocity={"x": 0.0, "y": 0.0, "z": 0.0},
                    acceleration=None, speed_mps=0.0, speed_kph=0.0,
                    angular_velocity=None, traffic_light_state=None, speed_limit=None,
                ))
                w.write_frame(_make_black_png_bytes(), i)
            manifest = w.finalize_manifest(status="success")

        assert manifest.frame_count == 4
        assert manifest.control_row_count == 4
        assert manifest.telemetry_row_count == 4
        assert manifest.status == "success"
        assert manifest.schema_version == SCHEMA_VERSION


# ─────────────────────────────────────────────────────────────────────────────
# TestSchemas
# ─────────────────────────────────────────────────────────────────────────────

class TestSchemas:
    def test_control_record_serializes(self) -> None:
        r = ControlRecord(
            tick=0, frame=0, timestamp_sim=0.0, timestamp_wall=0.0,
            throttle=0.5, brake=0.0, steer=-0.1,
            hand_brake=False, reverse=False,
            manual_gear_shift=False, gear=1,
        )
        d = r.to_dict()
        assert d["throttle"] == 0.5
        assert d["steer"] == -0.1
        # Must be round-trippable through JSON
        json.dumps(d)

    def test_episode_metadata_has_schema_version(self) -> None:
        m = _make_metadata("test_id")
        d = m.to_dict()
        assert d["schema_version"] == SCHEMA_VERSION

    def test_episode_manifest_serializes(self) -> None:
        m = EpisodeManifest(
            episode_id="ep1",
            schema_version=SCHEMA_VERSION,
            files=["metadata.json", "route.json"],
            frame_count=10,
            control_row_count=10,
            telemetry_row_count=10,
            event_count=2,
            status="success",
            validation_status="unchecked",
            completed_at="2026-07-07T14:30:00+00:00",
        )
        d = m.to_dict()
        assert d["frame_count"] == 10
        json.dumps(d)


# ─────────────────────────────────────────────────────────────────────────────
# TestEpisodeValidator
# ─────────────────────────────────────────────────────────────────────────────

class TestEpisodeValidator:
    def test_valid_episode_passes(self, tmp_path: Path) -> None:
        episode_id = "episode_valid"
        root = _build_valid_episode(tmp_path, episode_id, ticks=3)
        result = EpisodeValidator().validate(root)
        assert result.valid, f"Expected valid, errors: {result.errors}"

    def test_missing_metadata_fails(self, tmp_path: Path) -> None:
        episode_id = "episode_no_meta"
        root = _build_valid_episode(tmp_path, episode_id, ticks=2)
        (root / "metadata.json").unlink()
        result = EpisodeValidator().validate(root)
        assert not result.valid
        assert any("metadata.json" in e for e in result.errors)

    def test_missing_manifest_fails(self, tmp_path: Path) -> None:
        episode_id = "episode_no_manifest"
        root = _build_valid_episode(tmp_path, episode_id, ticks=2)
        (root / "manifest.json").unlink()
        result = EpisodeValidator().validate(root)
        assert not result.valid
        assert any("manifest.json" in e for e in result.errors)

    def test_invalid_jsonl_fails(self, tmp_path: Path) -> None:
        episode_id = "episode_bad_jsonl"
        root = _build_valid_episode(tmp_path, episode_id, ticks=2)
        (root / "controls.jsonl").write_text("this is not json\n{\"ok\": true}\n")
        result = EpisodeValidator().validate(root)
        assert not result.valid
        assert any("parse" in e.lower() or "JSON" in e for e in result.errors)

    def test_sequential_frames_valid(self, tmp_path: Path) -> None:
        episode_id = "episode_seq"
        root = _build_valid_episode(tmp_path, episode_id, ticks=3)
        result = EpisodeValidator().validate(root)
        seq_checks = [c for c in result.checks if "sequential" in c.name]
        assert all(c.passed for c in seq_checks)

    def test_nonsequential_frames_fails(self, tmp_path: Path) -> None:
        episode_id = "episode_nonseq"
        root = _build_valid_episode(tmp_path, episode_id, ticks=3)
        # Rename frame 1 to break sequencing
        old = root / "frames" / "front_camera" / "000001.png"
        new = root / "frames" / "front_camera" / "000099.png"
        old.rename(new)
        result = EpisodeValidator().validate(root)
        seq_checks = [c for c in result.checks if "sequential" in c.name]
        assert any(not c.passed for c in seq_checks)

    def test_zero_telemetry_rows_fails(self, tmp_path: Path) -> None:
        episode_id = "episode_no_telem"
        root = _build_valid_episode(tmp_path, episode_id, ticks=2)
        (root / "telemetry.jsonl").write_text("")
        result = EpisodeValidator().validate(root)
        assert not result.valid
        assert any("telemetry" in e.lower() for e in result.errors)

    def test_errors_list_populated_on_failure(self, tmp_path: Path) -> None:
        episode_id = "episode_errors"
        root = _build_valid_episode(tmp_path, episode_id, ticks=1)
        (root / "metadata.json").unlink()
        (root / "route.json").unlink()
        result = EpisodeValidator().validate(root)
        assert len(result.errors) >= 2


# ─────────────────────────────────────────────────────────────────────────────
# TestDryRunCollection
# ─────────────────────────────────────────────────────────────────────────────

class TestDryRunCollection:
    """End-to-end dry-run via _run_dry_run() — no CARLA required."""

    def _run(self, tmp_path: Path, ticks: int = 5) -> Path:
        from collect_expert_episode import _run_dry_run
        now = datetime(2026, 7, 7, 14, 30, tzinfo=timezone.utc)
        from src.data.episode import generate_episode_id
        episode_id = generate_episode_id("Town03", "routeA", "local_dev", now)
        _run_dry_run(
            episode_id=episode_id,
            output_dir=tmp_path,
            profile="local_dev",
            host="localhost",
            port=2000,
            town="Town03",
            route="routeA",
            ticks=ticks,
            camera_width=4,
            camera_height=4,
            camera_fov=110.0,
            ego_blueprint="vehicle.lincoln.mkz_2020",
            delta_s=0.05,
            carla_version="0.9.15",
            now=now,
        )
        return tmp_path / episode_id

    def test_generates_required_files(self, tmp_path: Path) -> None:
        root = self._run(tmp_path)
        for fname in ("metadata.json", "route.json", "controls.jsonl",
                       "telemetry.jsonl", "events.jsonl", "manifest.json"):
            assert (root / fname).exists(), f"Missing: {fname}"

    def test_generates_frames(self, tmp_path: Path) -> None:
        root = self._run(tmp_path, ticks=3)
        frames = sorted((root / "frames" / "front_camera").glob("*.png"))
        assert len(frames) == 3

    def test_frame_names_are_sequential(self, tmp_path: Path) -> None:
        root = self._run(tmp_path, ticks=3)
        frames = sorted((root / "frames" / "front_camera").glob("*.png"))
        assert [f.name for f in frames] == ["000000.png", "000001.png", "000002.png"]

    def test_metadata_has_dry_run_mode(self, tmp_path: Path) -> None:
        root = self._run(tmp_path)
        meta = json.loads((root / "metadata.json").read_text())
        assert meta["collection_mode"] == "dry_run"

    def test_metadata_has_correct_schema_version(self, tmp_path: Path) -> None:
        root = self._run(tmp_path)
        meta = json.loads((root / "metadata.json").read_text())
        assert meta["schema_version"] == SCHEMA_VERSION

    def test_controls_row_count_matches_ticks(self, tmp_path: Path) -> None:
        root = self._run(tmp_path, ticks=7)
        rows = [line for line in (root / "controls.jsonl").read_text().splitlines() if line]
        assert len(rows) == 7

    def test_validates_cleanly(self, tmp_path: Path) -> None:
        root = self._run(tmp_path, ticks=3)
        result = EpisodeValidator().validate(root)
        assert result.valid, f"Dry-run episode invalid: {result.errors}"

    def test_frames_are_valid_png(self, tmp_path: Path) -> None:
        root = self._run(tmp_path, ticks=2)
        frame = (root / "frames" / "front_camera" / "000000.png").read_bytes()
        # PNG signature: first 8 bytes
        assert frame[:8] == b"\x89PNG\r\n\x1a\n"


# ─────────────────────────────────────────────────────────────────────────────
# TestCLIParsing
# ─────────────────────────────────────────────────────────────────────────────

class TestCLIParsing:
    """Click CLI option presence and --dry-run behaviour."""

    def test_help_shows_dry_run_option(self) -> None:
        from click.testing import CliRunner

        from collect_expert_episode import main
        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output

    def test_help_shows_profile_option(self) -> None:
        from click.testing import CliRunner

        from collect_expert_episode import main
        result = CliRunner().invoke(main, ["--help"])
        assert "--profile" in result.output

    def test_help_shows_ticks_option(self) -> None:
        from click.testing import CliRunner

        from collect_expert_episode import main
        result = CliRunner().invoke(main, ["--help"])
        assert "--ticks" in result.output

    def test_help_shows_town_option(self) -> None:
        from click.testing import CliRunner

        from collect_expert_episode import main
        result = CliRunner().invoke(main, ["--help"])
        assert "--town" in result.output

    def test_dry_run_exits_0(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from collect_expert_episode import main
        result = CliRunner().invoke(main, [
            "--dry-run",
            "--ticks", "2",
            "--output-dir", str(tmp_path),
            "--town", "Town03",
            "--route", "routeA",
        ])
        assert result.exit_code == 0, result.output

    def test_validate_script_help(self) -> None:
        from click.testing import CliRunner

        from validate_episode import main as vmain
        result = CliRunner().invoke(vmain, ["--help"])
        assert result.exit_code == 0
        assert "--verbose" in result.output
        assert "--fix-manifest" in result.output


# ─────────────────────────────────────────────────────────────────────────────
# TestFixManifest
# ─────────────────────────────────────────────────────────────────────────────

class TestFixManifest:
    """write_validation_status() and validate_episode.py --fix-manifest."""

    def test_writes_valid_status(self, tmp_path: Path) -> None:
        episode_id = "episode_fix_valid"
        root = _build_valid_episode(tmp_path, episode_id, ticks=3)
        write_validation_status(root, valid=True)
        manifest = json.loads((root / "manifest.json").read_text())
        assert manifest["validation_status"] == "valid"

    def test_writes_invalid_status(self, tmp_path: Path) -> None:
        episode_id = "episode_fix_invalid"
        root = _build_valid_episode(tmp_path, episode_id, ticks=3)
        write_validation_status(root, valid=False)
        manifest = json.loads((root / "manifest.json").read_text())
        assert manifest["validation_status"] == "invalid"

    def test_preserves_other_manifest_fields(self, tmp_path: Path) -> None:
        episode_id = "episode_fix_preserve"
        root = _build_valid_episode(tmp_path, episode_id, ticks=3)
        before = json.loads((root / "manifest.json").read_text())
        write_validation_status(root, valid=True)
        after = json.loads((root / "manifest.json").read_text())
        assert after["episode_id"] == before["episode_id"]
        assert after["frame_count"] == before["frame_count"]
        assert after["schema_version"] == before["schema_version"]

    def test_raises_when_manifest_missing(self, tmp_path: Path) -> None:
        episode_id = "episode_fix_no_manifest"
        root = _build_valid_episode(tmp_path, episode_id, ticks=2)
        (root / "manifest.json").unlink()
        with pytest.raises(FileNotFoundError):
            write_validation_status(root, valid=True)

    def test_cli_fix_manifest_updates_status(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from validate_episode import main
        episode_id = "episode_cli_fix"
        root = _build_valid_episode(tmp_path, episode_id, ticks=3)
        result = CliRunner().invoke(main, [str(root), "--fix-manifest"])
        assert result.exit_code == 0, result.output
        assert "validation_status" in result.output
        manifest = json.loads((root / "manifest.json").read_text())
        assert manifest["validation_status"] == "valid"

    def test_cli_without_fix_manifest_leaves_status_unchecked(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from validate_episode import main
        episode_id = "episode_cli_no_fix"
        root = _build_valid_episode(tmp_path, episode_id, ticks=3)
        result = CliRunner().invoke(main, [str(root)])
        assert result.exit_code == 0, result.output
        manifest = json.loads((root / "manifest.json").read_text())
        assert manifest["validation_status"] == "unchecked"

    def test_cli_fix_manifest_warns_when_manifest_missing(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from validate_episode import main
        episode_id = "episode_cli_fix_missing"
        root = _build_valid_episode(tmp_path, episode_id, ticks=2)
        (root / "manifest.json").unlink()
        result = CliRunner().invoke(main, [str(root), "--fix-manifest"])
        assert "skipped" in result.output
