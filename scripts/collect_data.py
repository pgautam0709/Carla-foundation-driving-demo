#!/usr/bin/env python3
"""
scripts/collect_data.py — Data collection entry point.

Connects to a running CARLA server, spawns an ego vehicle with the
built-in autopilot, attaches an RGB camera, and records episodes to HDF5.

Usage::

    python scripts/collect_data.py
    python scripts/collect_data.py --profile linux_gpu
    python scripts/collect_data.py --episodes 10 --map Town05
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import click

from src.agents.autopilot import AutopilotAgent
from src.data.recorder import EpisodeFrame, EpisodeRecorder
from src.sensors.camera import RGBCamera
from src.simulation.client import CARLAClient, CARLAConnectionError, CARLAUnavailableError
from src.utils.config import load_config
from src.utils.logging import configure_logging, get_logger

log = get_logger(__name__)


def collect_episode(
    client: CARLAClient,
    cfg: dict[str, Any],
    episode_id: str,
) -> int:
    """Run a single data collection episode.

    Returns:
        Number of frames collected.
    """
    veh_cfg   = cfg["vehicle"]
    data_cfg  = cfg["data_collection"]
    sens_cfg  = cfg["sensors"]

    world = client.world
    bp_lib = world.get_blueprint_library()

    # ── Spawn ego vehicle ──────────────────────────────────────────────────────
    vehicle_bp = bp_lib.find(veh_cfg["blueprint"])
    spawn_points = world.get_map().get_spawn_points()
    spawn_idx = veh_cfg.get("spawn_index", 0)
    spawn_idx = spawn_idx % len(spawn_points)
    vehicle = world.spawn_actor(vehicle_bp, spawn_points[spawn_idx])
    client.register_actor(vehicle)
    log.info("collection.vehicle_spawned", blueprint=veh_cfg["blueprint"])

    # ── Attach camera + agent + recorder ──────────────────────────────────────
    max_frames = data_cfg.get("max_frames_per_episode", 2000)
    save_every = data_cfg.get("save_every_n_frames", 1)
    output_dir = data_cfg.get("output_dir", "data/raw")

    with (
        RGBCamera(client, vehicle, sens_cfg["rgb_camera"]) as cam,
        EpisodeRecorder(output_dir=output_dir, episode_id=episode_id, config=cfg) as rec,
    ):
        agent = AutopilotAgent(client, vehicle)
        agent.start()

        # Warm-up: let autopilot stabilise
        for _ in range(20):
            client.tick()
        cam.drain()

        frames_saved = 0
        for frame_idx in range(max_frames):
            client.tick()

            try:
                cam_frame = cam.get_frame(timeout=1.0)
            except Exception:
                log.warning("collection.frame_timeout", frame_idx=frame_idx)
                continue

            if frame_idx % save_every != 0:
                continue

            control = agent.get_control()
            speed   = agent.get_velocity_kmh()

            rec.record(EpisodeFrame(
                frame_id=cam_frame.frame_id,
                timestamp=cam_frame.timestamp,
                rgb=cam_frame.image,
                throttle=control.throttle,
                steer=control.steer,
                brake=control.brake,
                speed_kmh=speed,
            ))
            frames_saved += 1

        agent.stop()
        log.info(
            "collection.episode_done",
            episode_id=episode_id,
            frames_saved=frames_saved,
            path=str(rec.path),
        )
        return frames_saved


@click.command()
@click.option("--config",  default="config/default.yaml", help="Base config file")
@click.option("--profile", default="local_dev",           help="Config profile")
@click.option("--episodes", type=int, default=None,        help="Override number of episodes")
@click.option("--map",      default=None,                  help="Override CARLA map")
def main(
    config: str,
    profile: str,
    episodes: int | None,
    map: str | None,
) -> None:
    """Collect driving data from CARLA using the built-in autopilot."""
    cfg = load_config(profile=profile)
    configure_logging(
        level=cfg["logging"]["level"],
        fmt=cfg["logging"]["format"],
    )

    if episodes is not None:
        cfg["data_collection"]["episodes"] = episodes
    if map is not None:
        cfg["simulation"]["map"] = map

    n_episodes  = cfg["data_collection"]["episodes"]
    sim_cfg     = cfg["simulation"]
    conn_cfg    = cfg.get("carla_connection", {})

    log.info(
        "collection.start",
        episodes=n_episodes,
        map=sim_cfg["map"],
        host=conn_cfg.get("host", "localhost"),
        port=conn_cfg.get("port", 2000),
        profile=profile,
    )

    try:
        with CARLAClient(
            host=conn_cfg.get("host", "localhost"),
            port=conn_cfg.get("port", 2000),
            timeout_s=conn_cfg.get("timeout_s", 30.0),
            synchronous=sim_cfg["synchronous_mode"],
            fixed_delta_seconds=sim_cfg["fixed_delta_seconds"],
            render=sim_cfg["render"],
        ) as client:
            client.load_map(sim_cfg["map"])
            total_frames = 0
            for ep_idx in range(n_episodes):
                episode_id = f"ep_{ep_idx:04d}"
                log.info("collection.episode_start", episode=ep_idx + 1, total=n_episodes)
                frames = collect_episode(client, cfg, episode_id)
                total_frames += frames

            log.info(
                "collection.complete",
                total_episodes=n_episodes,
                total_frames=total_frames,
            )

    except CARLAUnavailableError as exc:
        click.echo(f"\n[FAIL] {exc}", err=True)
        sys.exit(1)
    except CARLAConnectionError as exc:
        click.echo(f"\n[FAIL] {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
