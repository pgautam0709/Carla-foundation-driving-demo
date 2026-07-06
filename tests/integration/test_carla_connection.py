"""
tests/integration/test_carla_connection.py — Integration tests for CARLA connectivity.

These tests are automatically skipped when no CARLA server is reachable.
Run with a live CARLA server:

    make test-all
    # or: pytest tests/integration/ -m integration
"""

from __future__ import annotations

import socket

import pytest

# ── Skip guard ─────────────────────────────────────────────────────────────────

def _carla_server_reachable(host: str = "localhost", port: int = 2000) -> bool:
    """Return True if a CARLA server is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=3.0):
            return True
    except (OSError, ConnectionRefusedError, TimeoutError):
        return False


_CARLA_AVAILABLE = _carla_server_reachable()

skip_no_carla = pytest.mark.skipif(
    not _CARLA_AVAILABLE,
    reason="CARLA server not reachable at localhost:2000",
)


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@skip_no_carla
class TestCARLAConnection:
    """Verifies the CARLAClient connects, configures, and cleans up correctly."""

    def test_client_connects_and_returns_world(self) -> None:
        from src.simulation.client import CARLAClient

        with CARLAClient(timeout_s=10.0) as client:
            world = client.world
            assert world is not None

    def test_client_reports_server_version(self) -> None:
        from src.simulation.client import CARLAClient

        with CARLAClient(timeout_s=10.0) as client:
            version = client.client.get_server_version()
            assert isinstance(version, str)
            assert len(version) > 0

    def test_synchronous_tick_advances_frame(self) -> None:
        from src.simulation.client import CARLAClient

        with CARLAClient(timeout_s=10.0, synchronous=True) as client:
            frame_a = client.tick()
            frame_b = client.tick()
            assert frame_b > frame_a

    def test_client_cleanup_destroys_actors(self) -> None:
        """Ensure spawned actors are destroyed on context exit."""
        from src.simulation.client import CARLAClient

        actor_id: int | None = None

        with CARLAClient(timeout_s=10.0) as client:
            world = client.world
            bp_lib = world.get_blueprint_library()
            vehicle_bp = bp_lib.filter("vehicle.lincoln.*")[0]
            spawn_point = world.get_map().get_spawn_points()[0]
            vehicle = world.spawn_actor(vehicle_bp, spawn_point)
            client.register_actor(vehicle)
            actor_id = vehicle.id

        # After context exit, actor should be gone
        from src.simulation.client import CARLAClient
        with CARLAClient(timeout_s=10.0) as client:
            world = client.world
            actor_ids = [a.id for a in world.get_actors()]
            assert actor_id not in actor_ids, "Actor should have been destroyed on exit"
