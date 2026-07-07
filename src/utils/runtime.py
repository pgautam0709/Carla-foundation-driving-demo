"""
src/utils/runtime.py — Runtime environment detection and Docker command builders.

Provides pure functions for detecting the host OS, building Docker run
commands, resolving Docker image names, and formatting CARLA error messages.
All functions are side-effect-free and easily unit-testable.

Usage::

    from src.utils.runtime import (
        detect_os,
        is_apple_silicon,
        build_docker_command,
        resolve_docker_image,
        format_carla_unavailable_message,
        format_carla_unreachable_message,
    )
"""

from __future__ import annotations

import os
import platform
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# ── OS detection ───────────────────────────────────────────────────────────────

def detect_os() -> str:
    """Return the current OS name as reported by the platform module.

    Returns:
        One of ``"Darwin"``, ``"Windows"``, or ``"Linux"``.
    """
    return platform.system()


def is_apple_silicon(
    system: str | None = None,
    machine: str | None = None,
) -> bool:
    """Return ``True`` if running on Apple Silicon (arm64 macOS).

    Args:
        system: Override the OS string (e.g. ``"Darwin"``). Defaults to
            ``platform.system()``.
        machine: Override the machine string (e.g. ``"arm64"``). Defaults to
            ``platform.machine()``.

    Returns:
        ``True`` only when *system* is ``"Darwin"`` and *machine* is
        ``"arm64"``.
    """
    _system = system if system is not None else platform.system()
    _machine = machine if machine is not None else platform.machine()
    return _system == "Darwin" and _machine == "arm64"


# ── Docker helpers ─────────────────────────────────────────────────────────────

def build_docker_command(
    image: str,
    ports: str = "2000-2002:2000-2002",
    extra_args: str = "",
    *,
    detach: bool = True,
    remove: bool = True,
) -> list[str]:
    """Build a ``docker run`` command list for a CARLA server container.

    The returned list can be passed directly to ``subprocess.run`` or
    joined with spaces for display.

    Args:
        image: Docker image name and tag (e.g. ``"carlasim/carla:0.9.15"``).
        ports: Port mapping string (e.g. ``"2000-2002:2000-2002"``).
        extra_args: Additional flags as a space-separated string
            (e.g. ``"--gpus all"`` on Linux GPU hosts).
        detach: If ``True``, add ``-d`` (run in background).
        remove: If ``True``, add ``--rm`` (remove on exit).

    Returns:
        List of strings suitable for ``subprocess.run(cmd, ...)``.
    """
    cmd: list[str] = ["docker", "run"]
    if remove:
        cmd.append("--rm")
    if detach:
        cmd.append("-d")
    cmd.extend(["-p", ports])
    if extra_args:
        cmd.extend(extra_args.split())
    cmd.append(image)
    cmd.extend([
        "/bin/bash",
        "-c",
        "/home/carla/CarlaUE4.sh -RenderOffScreen -nosound -carla-port=2000",
    ])
    return cmd


def resolve_docker_image(cfg: dict[str, Any]) -> str:
    """Resolve the CARLA Docker image from env var or config.

    Env var ``CARLA_DOCKER_IMAGE`` takes precedence over config.

    Args:
        cfg: Merged configuration dict (from ``load_config``).

    Returns:
        Docker image name and tag string.
    """
    env_image = os.environ.get("CARLA_DOCKER_IMAGE")
    if env_image:
        log.debug("runtime.docker_image_from_env", image=env_image)
        return env_image
    image: str = cfg.get("runtime", {}).get("docker_image", "carlasim/carla:0.9.15")
    return image


# ── Error message formatters ───────────────────────────────────────────────────

def format_carla_unavailable_message(version: str = "0.9.15") -> str:
    """Format a human-readable CARLA package-not-installed error message.

    Args:
        version: CARLA version string used to construct the wheel filename hint.

    Returns:
        Multi-line error string with install instructions.
    """
    return (
        f"The 'carla' Python package is not installed.\n"
        f"Install it from the CARLA {version} release tarball:\n"
        f"  pip install <CARLA_ROOT>/PythonAPI/carla/dist/"
        f"carla-{version}-cp310-*.whl\n"
        f"Or follow: docs/SETUP.md"
    )


def format_carla_unreachable_message(
    host: str,
    port: int,
    runtime_mode: str = "local",
) -> str:
    """Format a human-readable CARLA server-not-reachable error message.

    The remediation hint adapts to the active runtime mode.

    Args:
        host: The CARLA host that was tried.
        port: The CARLA port that was tried.
        runtime_mode: One of ``"docker"``, ``"local"``, or ``"remote"``.

    Returns:
        Multi-line error string with startup instructions.
    """
    if runtime_mode == "docker":
        hint = "make carla-docker   # start the CARLA Docker container"
    elif runtime_mode == "remote":
        hint = (
            f"Ensure CARLA is running on {host} and port {port} is reachable.\n"
            "  On the remote server: ./CarlaUE4.sh -RenderOffScreen -carla-port=2000"
        )
    else:
        hint = "./CarlaUE4.sh -RenderOffScreen   # start local CARLA"

    return (
        f"Could not connect to CARLA at {host}:{port}.\n"
        f"  {hint}\n"
        f"See: docs/PHASE1_SMOKE_TEST.md"
    )
