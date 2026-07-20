"""
src/simulation/client.py — CARLA client context manager.

Provides a safe, resource-managed interface to the CARLA server.
Always uses synchronous mode to guarantee deterministic frame ordering
during data collection and evaluation.

Usage::

    from src.simulation.client import CARLAClient

    with CARLAClient(host="localhost", port=2000) as client:
        world = client.world
        blueprint_library = world.get_blueprint_library()
        # ... spawn actors, attach sensors, tick ...

    # All actors destroyed and client disconnected on context exit.

Note:
    CARLA must be installed separately. Install the Python wheel from the
    CARLA tarball::

        pip install <CARLA_ROOT>/PythonAPI/carla/dist/carla-0.9.15-cp310-*.whl

    If the ``carla`` package is not importable, this module raises an
    ``ImportError`` with a remediation message.
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any, cast

from src.utils.logging import get_logger

log = get_logger(__name__)

try:
    import carla
    _CARLA_AVAILABLE = True
except ImportError:
    _CARLA_AVAILABLE = False
    carla = None


class CARLAUnavailableError(RuntimeError):
    """Raised when the carla Python package is not installed."""


class CARLAConnectionError(RuntimeError):
    """Raised when the CARLA server is unreachable."""


class CARLAClient:
    """Context manager for a CARLA simulation session.

    Handles:
    - Server connection with timeout and retry
    - Synchronous mode activation (required for deterministic data collection)
    - World settings management
    - Automatic actor cleanup on exit

    Args:
        host: CARLA server hostname or IP address.
        port: CARLA server port (default 2000).
        timeout_s: Connection timeout in seconds.
        synchronous: Whether to enable synchronous mode.
        fixed_delta_seconds: Simulation timestep in synchronous mode.
        render: Whether to enable the server-side renderer (False = headless).
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 2000,
        timeout_s: float = 30.0,
        synchronous: bool = True,
        fixed_delta_seconds: float = 0.05,
        render: bool = True,
    ) -> None:
        if not _CARLA_AVAILABLE:
            raise CARLAUnavailableError(
                "The 'carla' Python package is not installed.\n"
                "Install it from the CARLA tarball:\n"
                "  pip install <CARLA_ROOT>/PythonAPI/carla/dist/"
                "carla-0.9.15-cp310-*.whl\n"
                "Or run: make diagnose"
            )

        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self.synchronous = synchronous
        self.fixed_delta_seconds = fixed_delta_seconds
        self.render = render

        self._client: Any = None
        self._world: Any = None
        self._original_settings: Any = None
        self._spawned_actors: list[Any] = []

    # ── Context manager ────────────────────────────────────────────────────────

    def __enter__(self) -> CARLAClient:
        self._connect()
        self._apply_settings()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._cleanup()

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def client(self) -> Any:
        """The raw ``carla.Client`` instance."""
        if self._client is None:
            raise RuntimeError("CARLAClient is not connected. Use as a context manager.")
        return self._client

    @property
    def world(self) -> Any:
        """The current CARLA world."""
        if self._world is None:
            raise RuntimeError("CARLAClient is not connected. Use as a context manager.")
        return self._world

    def tick(self) -> int:
        """Advance the simulation by one tick (synchronous mode only).

        Returns:
            Frame ID of the new frame.
        """
        if not self.synchronous:
            raise RuntimeError("tick() requires synchronous mode.")
        # carla.World.tick() is untyped (no stubs); CARLA's own docs guarantee
        # it returns the new frame number as an int.
        return cast(int, self._world.tick())

    def register_actor(self, actor: Any) -> Any:
        """Register an actor for automatic cleanup on context exit.

        Args:
            actor: A carla.Actor instance.

        Returns:
            The same actor (allows chaining).
        """
        self._spawned_actors.append(actor)
        return actor

    def load_map(self, map_name: str) -> None:
        """Load a CARLA map and re-apply settings.

        Args:
            map_name: CARLA map name (e.g. ``"Town03"``).
        """
        log.info("simulation.loading_map", map=map_name)
        self._world = self._client.load_world(map_name)
        self._apply_settings()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _connect(self) -> None:
        log.info("simulation.connecting", host=self.host, port=self.port)
        deadline = time.monotonic() + self.timeout_s
        last_exc: Exception | None = None
        attempts = 0

        while time.monotonic() < deadline:
            try:
                client = carla.Client(self.host, self.port)
                client.set_timeout(min(5.0, self.timeout_s))
                # Calling get_world() proves the server is alive.
                world = client.get_world()
                self._client = client
                self._world = world
                log.info(
                    "simulation.connected",
                    server_version=client.get_server_version(),
                    client_version=client.get_client_version(),
                    map=world.get_map().name,
                )
                return
            except Exception as exc:
                last_exc = exc
                attempts += 1
                log.debug(
                    "simulation.connection_retry",
                    attempt=attempts,
                    error=str(exc),
                )
                time.sleep(1.0)

        raise CARLAConnectionError(
            f"Could not connect to CARLA server at {self.host}:{self.port} "
            f"after {self.timeout_s:.0f}s.\n"
            f"Last error: {last_exc}\n"
            "Ensure the CARLA server is running: ./CarlaUE4.sh -RenderOffScreen"
        )

    def _apply_settings(self) -> None:
        settings = self._world.get_settings()
        self._original_settings = settings  # save for restore

        settings.synchronous_mode = self.synchronous
        if self.synchronous:
            settings.fixed_delta_seconds = self.fixed_delta_seconds
        if not self.render:
            settings.no_rendering_mode = True

        self._world.apply_settings(settings)
        log.info(
            "simulation.settings_applied",
            synchronous=self.synchronous,
            fixed_delta_seconds=self.fixed_delta_seconds if self.synchronous else None,
            render=self.render,
        )

    def _cleanup(self) -> None:
        log.info("simulation.cleanup_start", num_actors=len(self._spawned_actors))

        # Destroy actors in reverse spawn order
        for actor in reversed(self._spawned_actors):
            try:
                if actor.is_alive:
                    actor.destroy()
            except Exception as exc:
                log.warning("simulation.actor_destroy_failed", error=str(exc))
        self._spawned_actors.clear()

        # Restore original settings
        if self._world is not None and self._original_settings is not None:
            try:
                self._world.apply_settings(self._original_settings)
            except Exception as exc:
                log.warning("simulation.settings_restore_failed", error=str(exc))

        log.info("simulation.disconnected")
        self._client = None
        self._world = None
