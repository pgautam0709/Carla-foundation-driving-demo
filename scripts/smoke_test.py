#!/usr/bin/env python3
"""
scripts/smoke_test.py — Phase 1 portable CARLA smoke test.

Works against any CARLA server regardless of how it was started:
Docker (macOS), native Linux, native Windows, or a remote host.

Usage::

    python scripts/smoke_test.py
    python scripts/smoke_test.py --host 192.168.1.5 --port 2000
    python scripts/smoke_test.py --profile macos_docker --ticks 50
    python scripts/smoke_test.py --map Town01

    CARLA_HOST=127.0.0.1 make smoke
    CARLA_HOST=<remote-ip> PROFILE=remote_carla make smoke

Exit codes:
    0  Smoke test passed
    1  CARLA package missing, server unreachable, or runtime error

Environment variables:
    CARLA_HOST   Override host (higher priority than --host and config)
    CARLA_PORT   Override port
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

# Ensure repo root is on sys.path so src/ imports work
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import click  # noqa: E402

from src.utils.config import get_nested, load_config  # noqa: E402
from src.utils.logging import configure_logging, get_logger  # noqa: E402
from src.utils.runtime import (  # noqa: E402
    format_carla_unavailable_message,
    format_carla_unreachable_message,
)

if TYPE_CHECKING:
    # Only needed for the client_cls: type[CARLAClient] annotation below —
    # the runtime import happens inside main() to give a friendlier error
    # message if src.simulation.client itself fails to import.
    from src.simulation.client import CARLAClient

log = get_logger(__name__)


# ── CLI ────────────────────────────────────────────────────────────────────────

@click.command()
@click.option(
    "--host", default=None,
    help="CARLA server host. Overrides CARLA_HOST env var and config.",
)
@click.option(
    "--port", default=None, type=int,
    help="CARLA server port. Overrides CARLA_PORT env var and config.",
)
@click.option(
    "--ticks", default=100, show_default=True,
    help="Number of synchronous simulation ticks to run.",
)
@click.option(
    "--profile", default="local_dev", show_default=True,
    help="Config profile to load (e.g. macos_docker, remote_carla).",
)
@click.option(
    "--map", "map_name", default=None,
    help="CARLA map to load. Defaults to config simulation.map.",
)
def main(
    host: str | None,
    port: int | None,
    ticks: int,
    profile: str,
    map_name: str | None,
) -> None:
    """Phase 1 smoke test — connect to CARLA and run N synchronous ticks.

    Reports connection time, server version, tick rate (Hz), and spawned
    actor info. All actors are destroyed and world settings restored on exit.
    """
    # ── Load config ───────────────────────────────────────────────────────────
    cfg = load_config(profile=profile)
    configure_logging(
        level=get_nested(cfg, "logging", "level", default="INFO"),
        fmt=get_nested(cfg, "logging", "format", default="console"),
    )

    conn = cfg.get("carla_connection", {})
    rt   = cfg.get("runtime", {})

    # Resolve host/port: CLI arg > env var (via apply_env_overrides in cfg) > config
    resolved_host = host or conn.get("host", "localhost")
    resolved_port = port or conn.get("port", 2000)
    timeout       = conn.get("timeout_s", 30.0)
    runtime_mode  = rt.get("mode", "local")
    resolved_map  = map_name or get_nested(cfg, "simulation", "map", default="Town03")
    carla_version = conn.get("version", "0.9.15")

    _print_header(resolved_host, resolved_port, profile, resolved_map, ticks, carla_version)

    # ── Guard: carla package available? ──────────────────────────────────────
    try:
        from src.simulation.client import CARLAClient
    except ImportError:
        click.echo(
            _fail("CARLA client module could not be imported from src.simulation.client"),
            err=True,
        )
        sys.exit(1)

    # Check if carla package is installed before trying to connect
    import importlib.util
    if importlib.util.find_spec("carla") is None:
        click.echo(_fail("CARLA Python package is not installed."), err=True)
        for line in format_carla_unavailable_message(carla_version).splitlines():
            click.echo(f"  {line}", err=True)
        sys.exit(1)

    # ── Run smoke test ────────────────────────────────────────────────────────
    try:
        _run_smoke_test(
            client_cls=CARLAClient,
            host=resolved_host,
            port=resolved_port,
            timeout=timeout,
            map_name=resolved_map,
            ticks=ticks,
            expected_version=carla_version,
        )
    except _CARLAConnectionError:
        click.echo(_fail(f"Cannot connect to CARLA at {resolved_host}:{resolved_port}"), err=True)
        for line in format_carla_unreachable_message(
            resolved_host, resolved_port, runtime_mode
        ).splitlines():
            click.echo(f"  {line}", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(_fail(f"Smoke test failed: {exc}"), err=True)
        log.exception("smoke_test.unexpected_error")
        sys.exit(1)


# ── Implementation ─────────────────────────────────────────────────────────────

class _CARLAConnectionError(RuntimeError):
    """Raised when we cannot connect to the CARLA server."""


def _run_smoke_test(
    *,
    client_cls: type[CARLAClient],
    host: str,
    port: int,
    timeout: float,
    map_name: str,
    ticks: int,
    expected_version: str = "0.9.15",
) -> None:
    """Connect to CARLA, spawn a vehicle, run ticks, and report results.

    Args:
        client_cls: CARLAClient class (injectable for testing).
        host: CARLA server hostname or IP.
        port: CARLA server port.
        timeout: Connection timeout in seconds.
        map_name: CARLA map name to load.
        ticks: Number of synchronous ticks to run.
        expected_version: Expected CARLA version from config (for mismatch check).

    Raises:
        _CARLAConnectionError: If the server is not reachable.
        RuntimeError: If any other unexpected error occurs.
    """
    connect_start = time.monotonic()
    try:
        with client_cls(
            host=host,
            port=port,
            timeout_s=timeout,
            synchronous=True,
            render=False,
        ) as client:
            connect_ms = (time.monotonic() - connect_start) * 1000
            server_version = client.client.get_server_version()
            client_version = client.client.get_client_version()
            click.echo(f"  {_ok(f'Connected  {connect_ms:.0f}ms  server={server_version}')}")

            # ── Version mismatch check ────────────────────────────────────────
            if server_version != expected_version:
                click.echo(
                    f"  {YELLOW('[WARN]')} Server version {server_version!r} != "
                    f"expected {expected_version!r} (from config carla_connection.version)"
                )
                click.echo(
                    "         API/server mismatch may cause runtime errors. "
                    "Update carla_connection.version or reinstall the wheel."
                )
            if client_version != server_version:
                click.echo(
                    f"  {YELLOW('[WARN]')} Python wheel version {client_version!r} != "
                    f"server {server_version!r}"
                )

            # Load map via client.load_map() so settings are re-applied correctly
            current_map = client.world.get_map().name.split("/")[-1]
            if current_map != map_name:
                click.echo(f"  Loading map: {map_name} ...")
                client.load_map(map_name)
            world = client.world
            bp_lib = world.get_blueprint_library()
            click.echo(f"  {_ok(f'Map: {map_name}')}")
            vehicle_bps = bp_lib.filter("vehicle.lincoln.*")

            if not vehicle_bps:
                vehicle_bps = bp_lib.filter("vehicle.*")
            vehicle_bp = vehicle_bps[0]
            spawn_points = world.get_map().get_spawn_points()
            if not spawn_points:
                raise RuntimeError(f"No spawn points available on map {map_name}")

            vehicle = world.try_spawn_actor(vehicle_bp, spawn_points[0])
            if vehicle is None:
                raise RuntimeError("Failed to spawn ego vehicle (spawn point occupied?)")
            client.register_actor(vehicle)
            click.echo(f"  {_ok(f'Ego vehicle: {vehicle_bp.id}  id={vehicle.id}')}")

            # Warm-up tick
            client.tick()

            # Timed ticks
            click.echo(f"\n  Running {ticks} synchronous ticks ...")
            tick_start = time.monotonic()
            for _ in range(ticks):
                client.tick()
            tick_duration = time.monotonic() - tick_start

            hz = ticks / tick_duration if tick_duration > 0 else 0.0

            _print_results(ticks, tick_duration, hz, server_version)

    except (ConnectionRefusedError, OSError, TimeoutError) as exc:
        raise _CARLAConnectionError(str(exc)) from exc


def _print_header(
    host: str, port: int, profile: str, map_name: str, ticks: int,
    expected_version: str = "0.9.15",
) -> None:
    width = 60
    print()
    print(BOLD("─" * width))
    print(BOLD(CYAN("  Phase 1 Smoke Test")))
    print(BOLD("─" * width))
    print(DIM(f"  Target  : {host}:{port}"))
    print(DIM(f"  Profile : {profile}"))
    print(DIM(f"  Map     : {map_name}"))
    print(DIM(f"  Ticks   : {ticks}"))
    print(DIM(f"  CARLA   : expected v{expected_version}"))
    print(BOLD("─" * width))
    print()



def _print_results(ticks: int, duration: float, hz: float, server_version: str) -> None:
    width = 60
    print()
    print(BOLD("─" * width))
    print(BOLD("  Results"))
    print(BOLD("─" * width))
    print(f"  Ticks        : {ticks}")
    print(f"  Duration     : {duration:.2f}s")
    print(f"  Tick rate    : {hz:.1f} Hz")
    print(f"  Server ver.  : {server_version}")
    print()

    if hz >= 15.0:
        print(GREEN("  ✓ Phase 1 smoke test passed"))
    else:
        print(YELLOW(f"  ⚠ Tick rate {hz:.1f} Hz is below 15 Hz target"))
        print(YELLOW("    Consider a native or GPU-accelerated CARLA instance"))

    print(BOLD("─" * width))
    print()


# ── Colour helpers (no dependencies) ──────────────────────────────────────────

import os as _os  # noqa: E402

_NO_COLOUR = not sys.stdout.isatty() or _os.environ.get("NO_COLOR")


def _c(text: str, code: str) -> str:
    return text if _NO_COLOUR else f"\033[{code}m{text}\033[0m"


def GREEN(t: str) -> str:  # noqa: N802
    return _c(t, "32")


def YELLOW(t: str) -> str:  # noqa: N802
    return _c(t, "33")


def RED(t: str) -> str:  # noqa: N802
    return _c(t, "31")


def BOLD(t: str) -> str:  # noqa: N802
    return _c(t, "1")


def DIM(t: str) -> str:  # noqa: N802
    return _c(t, "2")


def CYAN(t: str) -> str:  # noqa: N802
    return _c(t, "36")


def _ok(msg: str) -> str:
    return f"{GREEN('[ OK ]')} {msg}"


def _fail(msg: str) -> str:
    return f"{RED('[FAIL]')} {msg}"


if __name__ == "__main__":
    main()
