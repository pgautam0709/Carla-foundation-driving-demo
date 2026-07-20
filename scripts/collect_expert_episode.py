"""
scripts/collect_expert_episode.py — Expert driving data collection for Phase 2.

Collects a single driving episode from CARLA and writes it to:

    data/raw/episodes/<episode_id>/
        metadata.json
        route.json
        controls.jsonl
        telemetry.jsonl
        events.jsonl
        frames/front_camera/*.png
        manifest.json

In ``--dry-run`` mode, generates a complete episode structure with synthetic
data without connecting to CARLA.  Useful for testing downstream tooling.

Usage::

    # Dry run (no CARLA needed):
    make collect-dry-run
    python scripts/collect_expert_episode.py --dry-run

    # Live collection:
    CARLA_HOST=<ip> PROFILE=linux_local python scripts/collect_expert_episode.py

    # Custom options:
    python scripts/collect_expert_episode.py \\
        --host 127.0.0.1 --port 2000 \\
        --town Town01 --route routeB \\
        --ticks 200 --dry-run
"""

from __future__ import annotations

import importlib.util
import struct
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

# Ensure src/ is importable when running from repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data.episode import (  # noqa: E402
    EpisodeDirectory,
    compute_route_hash,
    generate_episode_id,
    get_git_commit,
)
from src.data.schemas import (  # noqa: E402
    SCHEMA_VERSION,
    ControlRecord,
    EpisodeMetadata,
    EventRecord,
    RouteDefinition,
    SensorConfig,
    TelemetryRecord,
)
from src.data.validation import EpisodeValidator  # noqa: E402
from src.data.writers import EpisodeWriter  # noqa: E402
from src.utils.config import get_nested, load_config  # noqa: E402
from src.utils.logging import configure_logging, get_logger  # noqa: E402
from src.utils.runtime import (  # noqa: E402
    format_carla_unavailable_message,
    format_carla_unreachable_message,
)

log = get_logger(__name__)


# ── CLI ────────────────────────────────────────────────────────────────────────

@click.command(
    name="collect-expert-episode",
    help="Collect a single expert driving episode from CARLA."
    " Use --dry-run to generate a mock episode without a CARLA server.",
)
@click.option("--profile", default=None, envvar="PROFILE",
              help="Runtime profile name (e.g. macos_docker).")
@click.option("--host", default=None, envvar="CARLA_HOST",
              help="CARLA server host. Overrides config and profile.")
@click.option("--port", default=None, type=int, envvar="CARLA_PORT",
              help="CARLA server port. Overrides config and profile.")
@click.option("--town", default=None,
              help="CARLA map name (e.g. Town03). Overrides expert_collection.town.")
@click.option("--route", default=None,
              help="Route label (e.g. routeA). Overrides expert_collection.route.")
@click.option("--ticks", default=None, type=int,
              help="Number of synchronous ticks to collect. Overrides expert_collection.ticks.")
@click.option("--output-dir", default=None, type=click.Path(),
              help="Parent directory for episodes. Defaults to expert_collection.output_dir.")
@click.option("--camera-width", default=None, type=int,
              help="Front camera width in pixels.")
@click.option("--camera-height", default=None, type=int,
              help="Front camera height in pixels.")
@click.option("--camera-fov", default=None, type=float,
              help="Front camera horizontal FOV in degrees.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Generate a synthetic episode without connecting to CARLA.")
def main(
    profile: str | None,
    host: str | None,
    port: int | None,
    town: str | None,
    route: str | None,
    ticks: int | None,
    output_dir: str | None,
    camera_width: int | None,
    camera_height: int | None,
    camera_fov: float | None,
    dry_run: bool,
) -> None:
    cfg = load_config(profile=profile)
    configure_logging(
        level=get_nested(cfg, "logging", "level", default="INFO"),
        fmt=get_nested(cfg, "logging", "format", default="console"),
    )

    ec  = cfg.get("expert_collection", {})
    cam = ec.get("camera", {})
    conn = cfg.get("carla_connection", {})
    rt   = cfg.get("runtime", {})

    # Resolve all parameters: CLI > config
    resolved_host   = host or conn.get("host", "localhost")
    resolved_port   = port or conn.get("port", 2000)
    resolved_town   = town or ec.get("town", "Town03")
    resolved_route  = route or ec.get("route", "routeA")
    resolved_ticks  = ticks or ec.get("ticks", 500)
    resolved_out    = Path(output_dir or ec.get("output_dir", "data/raw/episodes"))
    resolved_width  = camera_width  or cam.get("width", 640)
    resolved_height = camera_height or cam.get("height", 480)
    resolved_fov    = camera_fov    or cam.get("fov", 110.0)
    resolved_profile = profile or "default"
    carla_version   = conn.get("version", "0.9.15")
    runtime_mode    = rt.get("mode", "local")
    ego_blueprint   = ec.get("ego_vehicle", "vehicle.lincoln.mkz_2020")
    delta_s         = ec.get("fixed_delta_seconds",
                              get_nested(cfg, "simulation", "fixed_delta_seconds", default=0.05))

    # Generate episode ID
    now = datetime.now(tz=timezone.utc)
    episode_id = generate_episode_id(
        town=resolved_town,
        route_name=resolved_route,
        profile=resolved_profile,
        timestamp=now,
    )

    _print_header(episode_id, resolved_host, resolved_port, resolved_town,
                  resolved_route, resolved_ticks, dry_run)

    if dry_run:
        _run_dry_run(
            episode_id=episode_id,
            output_dir=resolved_out,
            profile=resolved_profile,
            host=resolved_host,
            port=resolved_port,
            town=resolved_town,
            route=resolved_route,
            ticks=resolved_ticks,
            camera_width=resolved_width,
            camera_height=resolved_height,
            camera_fov=resolved_fov,
            ego_blueprint=ego_blueprint,
            delta_s=delta_s,
            carla_version=carla_version,
            now=now,
        )
        _validate_and_report(resolved_out / episode_id)
        return

    # ── Live collection guard ──────────────────────────────────────────────────
    if importlib.util.find_spec("carla") is None:
        click.echo(_fail("CARLA Python package is not installed."), err=True)
        for line in format_carla_unavailable_message(carla_version).splitlines():
            click.echo(f"  {line}", err=True)
        sys.exit(1)

    try:
        _run_live_collection(
            episode_id=episode_id,
            output_dir=resolved_out,
            profile=resolved_profile,
            host=resolved_host,
            port=resolved_port,
            town=resolved_town,
            route=resolved_route,
            ticks=resolved_ticks,
            camera_width=resolved_width,
            camera_height=resolved_height,
            camera_fov=resolved_fov,
            ego_blueprint=ego_blueprint,
            delta_s=delta_s,
            carla_version=carla_version,
            runtime_mode=runtime_mode,
            conn=conn,
            now=now,
        )
    except (ConnectionRefusedError, OSError, TimeoutError):
        click.echo(_fail(f"Cannot connect to CARLA at {resolved_host}:{resolved_port}"), err=True)
        for line in format_carla_unreachable_message(
            resolved_host, resolved_port, runtime_mode
        ).splitlines():
            click.echo(f"  {line}", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(_fail(f"Collection failed: {exc}"), err=True)
        log.exception("collect.unexpected_error")
        sys.exit(1)

    _validate_and_report(resolved_out / episode_id)


# ── Dry-run implementation ─────────────────────────────────────────────────────

def _run_dry_run(
    *,
    episode_id: str,
    output_dir: Path,
    profile: str,
    host: str,
    port: int,
    town: str,
    route: str,
    ticks: int,
    camera_width: int,
    camera_height: int,
    camera_fov: float,
    ego_blueprint: str,
    delta_s: float,
    carla_version: str,
    now: datetime,
) -> None:
    """Generate a complete synthetic episode without connecting to CARLA.

    Args:
        episode_id: Pre-generated episode identifier.
        output_dir: Base directory for episodes.
        profile: Active runtime profile name.
        host: CARLA host (recorded in metadata only).
        port: CARLA port (recorded in metadata only).
        town: Town name for the episode.
        route: Route label.
        ticks: Number of synthetic ticks to generate.
        camera_width: Frame width in pixels.
        camera_height: Frame height in pixels.
        camera_fov: Horizontal FOV in degrees.
        ego_blueprint: Ego vehicle blueprint ID.
        delta_s: Simulation fixed timestep.
        carla_version: Expected CARLA version string.
        now: UTC creation timestamp.
    """
    click.echo("  [dry-run] Generating synthetic episode (no CARLA required)")

    ep_dir = EpisodeDirectory(output_dir, episode_id)

    # Build route definition
    route_dict: dict[str, Any] = {
        "town": town,
        "route_name": route,
        "start_x": 0.0, "start_y": 0.0,
    }
    route_hash = compute_route_hash(route_dict)
    route_def = RouteDefinition(
        town=town,
        route_name=route,
        route_hash=route_hash,
        start_transform={"x": 0.0, "y": 0.0, "z": 0.5,
                         "pitch": 0.0, "yaw": 0.0, "roll": 0.0},
        destination_transform=None,
        distance_estimate_m=None,
        generation_method="spawn_point",
    )

    # Build metadata
    sensor_cfg = SensorConfig(
        name="front_camera",
        sensor_type="sensor.camera.rgb",
        width=camera_width,
        height=camera_height,
        fov=camera_fov,
        transform={"x": 1.5, "y": 0.0, "z": 2.4,
                   "pitch": -15.0, "yaw": 0.0, "roll": 0.0},
    )
    metadata = EpisodeMetadata(
        episode_id=episode_id,
        created_at=now.isoformat(),
        schema_version=SCHEMA_VERSION,
        runtime_profile=profile,
        carla_host=host,
        carla_port=port,
        carla_version_expected=carla_version,
        carla_version_server=None,   # dry-run: no server
        carla_version_client=None,
        town=town,
        weather_preset=None,
        route_name=route,
        route_hash=route_hash,
        tick_count_target=ticks,
        fixed_delta_seconds=delta_s,
        sensors=[sensor_cfg],
        ego_vehicle_blueprint=ego_blueprint,
        git_commit=get_git_commit(),
        collection_mode="dry_run",
        camera_width=camera_width,
        camera_height=camera_height,
        camera_fov=camera_fov,
    )

    # Synthesise frames (solid black PNG) and per-tick records
    black_png = _make_black_png(camera_width, camera_height)
    wall_start = time.monotonic()

    with EpisodeWriter(ep_dir) as writer:
        writer.write_metadata(metadata)
        writer.write_route(route_def)

        writer.write_event(EventRecord(
            tick=0, frame=0,
            timestamp_wall=time.monotonic(),
            event_type="episode_started",
            payload={"episode_id": episode_id, "mode": "dry_run"},
        ))

        for i in range(ticks):
            ts_sim  = i * delta_s
            ts_wall = time.monotonic()

            writer.write_control(ControlRecord(
                tick=i, frame=i,
                timestamp_sim=ts_sim,
                timestamp_wall=ts_wall,
                throttle=0.0, brake=0.0, steer=0.0,
                hand_brake=False, reverse=False,
                manual_gear_shift=False, gear=0,
            ))
            writer.write_telemetry(TelemetryRecord(
                tick=i, frame=i,
                timestamp_sim=ts_sim,
                location={"x": 0.0, "y": 0.0, "z": 0.0},
                rotation={"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
                velocity={"x": 0.0, "y": 0.0, "z": 0.0},
                acceleration={"x": 0.0, "y": 0.0, "z": 0.0},
                speed_mps=0.0,
                speed_kph=0.0,
                angular_velocity={"x": 0.0, "y": 0.0, "z": 0.0},
                traffic_light_state=None,
                speed_limit=None,
            ))
            writer.write_frame(black_png, frame_idx=i)

        writer.write_event(EventRecord(
            tick=ticks - 1, frame=ticks - 1,
            timestamp_wall=time.monotonic(),
            event_type="episode_completed",
            payload={
                "ticks_collected": ticks,
                "duration_s": round(time.monotonic() - wall_start, 3),
            },
        ))

        manifest = writer.finalize_manifest(status="success")

    click.echo(f"  {_ok(f'Episode written: {ep_dir.root}')}")
    click.echo(f"     Frames  : {manifest.frame_count}")
    click.echo(f"     Controls: {manifest.control_row_count}")
    click.echo(f"     Telemetry: {manifest.telemetry_row_count}")
    log.info("collect.dry_run_complete", episode_id=episode_id,
             frames=manifest.frame_count)


# ── Live collection implementation ─────────────────────────────────────────────

def _run_live_collection(
    *,
    episode_id: str,
    output_dir: Path,
    profile: str,
    host: str,
    port: int,
    town: str,
    route: str,
    ticks: int,
    camera_width: int,
    camera_height: int,
    camera_fov: float,
    ego_blueprint: str,
    delta_s: float,
    carla_version: str,
    runtime_mode: str,
    conn: dict[str, Any],
    now: datetime,
) -> None:
    """Connect to CARLA and collect a live episode.

    Args: (see :func:`main` for parameter descriptions)

    Raises:
        ConnectionRefusedError / OSError / TimeoutError: If CARLA is unreachable.
        RuntimeError: On any other collection failure.
    """
    from src.simulation.client import CARLAClient
    from src.simulation.expert_driver import ExpertDriver

    ep_dir = EpisodeDirectory(output_dir, episode_id)

    route_dict: dict[str, Any] = {"town": town, "route_name": route}
    route_hash = compute_route_hash(route_dict)

    timeout = conn.get("timeout_s", 30.0)

    with CARLAClient(
        host=host,
        port=port,
        timeout_s=timeout,
        synchronous=True,
        fixed_delta_seconds=delta_s,
        render=False,
    ) as client:
        server_version = client.client.get_server_version()
        client_version = client.client.get_client_version()
        click.echo(f"  {_ok(f'Connected  server={server_version}')}")

        if server_version != carla_version:
            click.echo(
                f"  {_warn(f'Version mismatch: server={server_version} expected={carla_version}')}"
            )

        # Load map
        current_map = client.world.get_map().name.split("/")[-1]
        if current_map != town:
            click.echo(f"  Loading map: {town} ...")
            client.load_map(town)
        click.echo(f"  {_ok(f'Map: {town}')}")

        world = client.world
        bp_lib = world.get_blueprint_library()

        # Spawn ego vehicle
        vehicle_bp = next(iter(bp_lib.filter(ego_blueprint) or bp_lib.filter("vehicle.*")))
        spawn_points = world.get_map().get_spawn_points()
        if not spawn_points:
            raise RuntimeError(f"No spawn points on {town}")

        vehicle = world.try_spawn_actor(vehicle_bp, spawn_points[0])
        if vehicle is None:
            raise RuntimeError("Failed to spawn ego vehicle")
        client.register_actor(vehicle)
        click.echo(f"  {_ok(f'Ego: {vehicle_bp.id}  id={vehicle.id}')}")

        # Spawn sensor
        camera_bp = bp_lib.find("sensor.camera.rgb")
        camera_bp.set_attribute("image_size_x", str(camera_width))
        camera_bp.set_attribute("image_size_y", str(camera_height))
        camera_bp.set_attribute("fov", str(camera_fov))
        import carla
        cam_transform = carla.Transform(
            carla.Location(x=1.5, y=0.0, z=2.4),
            carla.Rotation(pitch=-15.0),
        )
        camera = world.spawn_actor(camera_bp, cam_transform, attach_to=vehicle)
        client.register_actor(camera)

        import queue
        image_queue: queue.Queue[Any] = queue.Queue()
        camera.listen(image_queue.put)

        # Warm-up
        client.tick()
        click.echo(f"  {_ok('Camera attached, warm-up tick done')}")

        # Build metadata and route
        sensor_cfg = SensorConfig(
            name="front_camera",
            sensor_type="sensor.camera.rgb",
            width=camera_width, height=camera_height, fov=camera_fov,
            transform={"x": 1.5, "y": 0.0, "z": 2.4,
                       "pitch": -15.0, "yaw": 0.0, "roll": 0.0},
        )
        sp = spawn_points[0]
        route_def = RouteDefinition(
            town=town, route_name=route, route_hash=route_hash,
            start_transform={
                "x": float(sp.location.x), "y": float(sp.location.y),
                "z": float(sp.location.z),
                "pitch": float(sp.rotation.pitch), "yaw": float(sp.rotation.yaw),
                "roll": float(sp.rotation.roll),
            },
            destination_transform=None,
            distance_estimate_m=None,
            generation_method="spawn_point",
        )
        metadata = EpisodeMetadata(
            episode_id=episode_id,
            created_at=now.isoformat(),
            schema_version=SCHEMA_VERSION,
            runtime_profile=profile,
            carla_host=host,
            carla_port=port,
            carla_version_expected=carla_version,
            carla_version_server=server_version,
            carla_version_client=client_version,
            town=town,
            weather_preset=None,
            route_name=route,
            route_hash=route_hash,
            tick_count_target=ticks,
            fixed_delta_seconds=delta_s,
            sensors=[sensor_cfg],
            ego_vehicle_blueprint=vehicle_bp.id,
            git_commit=get_git_commit(),
            collection_mode="live",
            camera_width=camera_width,
            camera_height=camera_height,
            camera_fov=camera_fov,
        )

        # Start expert driver
        driver = ExpertDriver(vehicle, cfg={})
        driver.start()
        wall_start = time.monotonic()
        tick_sim = 0.0

        click.echo(f"\n  Collecting {ticks} ticks ...")

        with EpisodeWriter(ep_dir) as writer:
            writer.write_metadata(metadata)
            writer.write_route(route_def)
            writer.write_event(EventRecord(
                tick=0, frame=0, timestamp_wall=time.monotonic(),
                event_type="episode_started",
                payload={"episode_id": episode_id},
            ))
            writer.write_event(EventRecord(
                tick=0, frame=0, timestamp_wall=time.monotonic(),
                event_type="ego_spawned",
                payload={"blueprint": vehicle_bp.id, "id": vehicle.id},
            ))
            writer.write_event(EventRecord(
                tick=0, frame=0, timestamp_wall=time.monotonic(),
                event_type="camera_attached",
                payload={"width": camera_width, "height": camera_height, "fov": camera_fov},
            ))

            try:
                for tick_i in range(ticks):
                    frame_id = client.tick()
                    ts_wall = time.monotonic()
                    tick_sim = tick_i * delta_s

                    # Drain camera frame
                    try:
                        img = image_queue.get(timeout=1.0)
                        frame_bytes = bytes(img.raw_data)
                        # Convert BGRA (CARLA default) → PNG via minimal encoder
                        png_bytes = _bgra_to_png(frame_bytes, camera_width, camera_height)
                        writer.write_frame(png_bytes, tick_i)
                    except Exception as exc:
                        log.warning("collect.frame_skip", tick=tick_i, error=str(exc))
                        writer.write_event(EventRecord(
                            tick=tick_i, frame=frame_id,
                            timestamp_wall=ts_wall,
                            event_type="tick_warning",
                            payload={"reason": f"frame capture failed: {exc}"},
                        ))

                    writer.write_control(
                        driver.get_control_record(tick_i, frame_id, tick_sim, ts_wall)
                    )
                    writer.write_telemetry(
                        driver.get_telemetry_record(tick_i, frame_id, tick_sim)
                    )

                writer.write_event(EventRecord(
                    tick=ticks - 1, frame=0,
                    timestamp_wall=time.monotonic(),
                    event_type="episode_completed",
                    payload={
                        "ticks_collected": ticks,
                        "duration_s": round(time.monotonic() - wall_start, 3),
                    },
                ))
                manifest = writer.finalize_manifest(status="success")

            except KeyboardInterrupt:
                writer.write_event(EventRecord(
                    tick=0, frame=0, timestamp_wall=time.monotonic(),
                    event_type="episode_failed",
                    payload={"reason": "KeyboardInterrupt"},
                ))
                writer.finalize_manifest(status="partial")
                raise

            finally:
                driver.stop()
                writer.write_event(EventRecord(
                    tick=0, frame=0, timestamp_wall=time.monotonic(),
                    event_type="cleanup_completed",
                    payload={},
                ))

        click.echo(f"  {_ok(f'Episode written: {ep_dir.root}')}")
        click.echo(f"     Frames   : {manifest.frame_count}")
        click.echo(f"     Controls : {manifest.control_row_count}")
        click.echo(f"     Telemetry: {manifest.telemetry_row_count}")


# ── Validation ─────────────────────────────────────────────────────────────────

def _validate_and_report(episode_dir: Path) -> None:
    """Validate the episode and print a summary."""
    result = EpisodeValidator().validate(episode_dir)
    print()
    if result.valid:
        click.echo(_ok(f"Validation passed ({len(result.checks)} checks)"))
    else:
        click.echo(_fail(f"Validation found {len(result.errors)} error(s):"))
        for err in result.errors:
            click.echo(f"    • {err}", err=True)


# ── PNG helpers ────────────────────────────────────────────────────────────────

def _make_black_png(width: int, height: int) -> bytes:
    """Return bytes of a minimal valid solid-black RGB PNG.

    Uses only :mod:`struct` and :mod:`zlib` — no external dependencies.

    Args:
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        PNG-encoded bytes.
    """
    def _chunk(ctype: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">II", width, height) + bytes([8, 2, 0, 0, 0])
    ihdr = _chunk(b"IHDR", ihdr_data)

    # Each row: filter byte (0=None) + width * 3 bytes RGB (all zero = black)
    raw_row = bytes(1 + width * 3)
    idat = _chunk(b"IDAT", zlib.compress(raw_row * height, level=1))
    iend = _chunk(b"IEND", b"")

    return signature + ihdr + idat + iend


def _bgra_to_png(raw_bgra: bytes, width: int, height: int) -> bytes:
    """Convert CARLA BGRA raw data to a minimal PNG.

    CARLA camera sensor outputs raw BGRA bytes. This converts to RGB PNG
    using only stdlib (no opencv/PIL required for data recording).

    Args:
        raw_bgra: Raw BGRA bytes from a CARLA camera image.
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        PNG-encoded bytes, or a black PNG on error.
    """
    try:
        def _chunk(ctype: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)

        signature = b"\x89PNG\r\n\x1a\n"
        ihdr_data = struct.pack(">II", width, height) + bytes([8, 2, 0, 0, 0])
        ihdr = _chunk(b"IHDR", ihdr_data)

        # Convert BGRA → RGB, prepend filter byte per scanline
        raw_rows = bytearray()
        stride = width * 4
        for row in range(height):
            raw_rows.append(0)  # filter: None
            for col in range(width):
                offset = row * stride + col * 4
                b, g, r = raw_bgra[offset], raw_bgra[offset + 1], raw_bgra[offset + 2]
                raw_rows += bytes([r, g, b])

        idat = _chunk(b"IDAT", zlib.compress(bytes(raw_rows), level=1))
        iend = _chunk(b"IEND", b"")
        return signature + ihdr + idat + iend
    except Exception:
        return _make_black_png(width, height)


# ── Display helpers ────────────────────────────────────────────────────────────

def _ok(msg: str) -> str:
    return f"\033[32m[ OK ]\033[0m  {msg}"

def _fail(msg: str) -> str:
    return f"\033[31m[FAIL]\033[0m  {msg}"

def _warn(msg: str) -> str:
    return f"\033[33m[WARN]\033[0m  {msg}"

def _print_header(
    episode_id: str, host: str, port: int, town: str,
    route: str, ticks: int, dry_run: bool,
) -> None:
    width = 68
    mode = "[DRY RUN]" if dry_run else "[LIVE]"
    print()
    print("─" * width)
    print(f"  \033[1mPhase 2 Expert Data Collection  {mode}\033[0m")
    print("─" * width)
    print(f"  Episode  : {episode_id}")
    print(f"  Target   : {host}:{port}")
    print(f"  Town     : {town}  route={route}")
    print(f"  Ticks    : {ticks}")
    print("─" * width)
    print()


if __name__ == "__main__":
    main()
