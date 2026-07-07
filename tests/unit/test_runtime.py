"""
tests/unit/test_runtime.py — Unit tests for Phase 1 runtime portability.

All tests run without CARLA, Docker, or a network connection.

Test classes:
    TestEnvOverrides          — CARLA_* env vars write into carla_connection
    TestRuntimeProfiles       — All four new profiles load and set runtime.mode
    TestDockerCommandBuilder  — build_docker_command returns expected list
    TestArchitectureDetection — is_apple_silicon() with injectable args
    TestErrorFormatters       — format_carla_* functions contain expected content
    TestSmokeTestCLI          — --help and graceful CARLA-unavailable exit
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ── Ensure src/ is importable from the repo root ───────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils.config import apply_env_overrides, load_config  # noqa: E402
from src.utils.runtime import (  # noqa: E402
    build_docker_command,
    format_carla_unavailable_message,
    format_carla_unreachable_message,
    is_apple_silicon,
    resolve_docker_image,
)

# ─────────────────────────────────────────────────────────────────────────────
# TestEnvOverrides
# ─────────────────────────────────────────────────────────────────────────────

class TestEnvOverrides:
    """apply_env_overrides() and load_config() honour CARLA_* env vars."""

    def test_carla_host_overrides_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CARLA_HOST env var sets carla_connection.host."""
        monkeypatch.setenv("CARLA_HOST", "10.0.0.1")
        monkeypatch.delenv("CARLA_PORT", raising=False)
        cfg = load_config()
        assert cfg["carla_connection"]["host"] == "10.0.0.1"

    def test_carla_port_coerced_to_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CARLA_PORT env var is coerced to int and sets carla_connection.port."""
        monkeypatch.setenv("CARLA_PORT", "9000")
        monkeypatch.delenv("CARLA_HOST", raising=False)
        cfg = load_config()
        assert cfg["carla_connection"]["port"] == 9000
        assert isinstance(cfg["carla_connection"]["port"], int)

    def test_unset_env_vars_leave_config_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When CARLA_HOST/PORT are not set, config values are unchanged."""
        monkeypatch.delenv("CARLA_HOST", raising=False)
        monkeypatch.delenv("CARLA_PORT", raising=False)
        monkeypatch.delenv("CARLA_VERSION", raising=False)
        monkeypatch.delenv("CARLA_PYTHON_API_PATH", raising=False)
        cfg = load_config()
        assert cfg["carla_connection"]["host"] == "localhost"
        assert cfg["carla_connection"]["port"] == 2000

    def test_invalid_carla_port_leaves_config_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-numeric CARLA_PORT is silently ignored; config value kept."""
        monkeypatch.setenv("CARLA_PORT", "not-a-number")
        monkeypatch.delenv("CARLA_HOST", raising=False)
        cfg = load_config()
        assert cfg["carla_connection"]["port"] == 2000

    def test_apply_env_overrides_does_not_mutate_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """apply_env_overrides returns a NEW dict; the input is not mutated."""
        monkeypatch.setenv("CARLA_HOST", "mutate-test")
        original: dict = {"carla_connection": {"host": "original", "port": 2000}}
        result = apply_env_overrides(original)
        assert result["carla_connection"]["host"] == "mutate-test"
        # Original untouched
        assert original["carla_connection"]["host"] == "original"

    def test_carla_python_api_path_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CARLA_PYTHON_API_PATH sets carla_connection.python_api_path."""
        monkeypatch.setenv("CARLA_PYTHON_API_PATH", "/opt/carla/dist")
        monkeypatch.delenv("CARLA_HOST", raising=False)
        monkeypatch.delenv("CARLA_PORT", raising=False)
        cfg = load_config()
        assert cfg["carla_connection"]["python_api_path"] == "/opt/carla/dist"


# ─────────────────────────────────────────────────────────────────────────────
# TestRuntimeProfiles
# ─────────────────────────────────────────────────────────────────────────────

class TestRuntimeProfiles:
    """All four new runtime profiles load cleanly and set expected runtime.mode."""

    def _load_clean(
        self, monkeypatch: pytest.MonkeyPatch, profile: str
    ) -> dict:
        """Load a profile with all CARLA env vars cleared."""
        for var in ("CARLA_HOST", "CARLA_PORT", "CARLA_VERSION", "CARLA_PYTHON_API_PATH"):
            monkeypatch.delenv(var, raising=False)
        return load_config(profile=profile)

    def test_macos_docker_profile_loads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = self._load_clean(monkeypatch, "macos_docker")
        assert cfg["runtime"]["mode"] == "docker"

    def test_windows_local_profile_loads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = self._load_clean(monkeypatch, "windows_local")
        assert cfg["runtime"]["mode"] == "local"

    def test_linux_local_profile_loads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = self._load_clean(monkeypatch, "linux_local")
        assert cfg["runtime"]["mode"] == "local"

    def test_remote_carla_profile_loads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = self._load_clean(monkeypatch, "remote_carla")
        assert cfg["runtime"]["mode"] == "remote"

    def test_macos_docker_host_is_loopback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """macos_docker profile uses 127.0.0.1 (not localhost) for Docker."""
        cfg = self._load_clean(monkeypatch, "macos_docker")
        assert cfg["carla_connection"]["host"] == "127.0.0.1"

    def test_macos_docker_render_is_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Docker container has no display — render must be false."""
        cfg = self._load_clean(monkeypatch, "macos_docker")
        assert cfg["simulation"]["render"] is False

    def test_remote_carla_has_longer_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Remote profile allows more time for network latency."""
        base = load_config()
        remote = self._load_clean(monkeypatch, "remote_carla")
        assert remote["carla_connection"]["timeout_s"] > base["carla_connection"]["timeout_s"]


# ─────────────────────────────────────────────────────────────────────────────
# TestDockerCommandBuilder
# ─────────────────────────────────────────────────────────────────────────────

class TestDockerCommandBuilder:
    """build_docker_command returns a well-formed docker run argument list."""

    def test_image_present_in_command(self) -> None:
        cmd = build_docker_command("carlasim/carla:0.9.15")
        assert "carlasim/carla:0.9.15" in cmd

    def test_default_port_mapping(self) -> None:
        cmd = build_docker_command("carlasim/carla:0.9.15")
        assert "-p" in cmd
        assert "2000-2002:2000-2002" in cmd

    def test_custom_port_mapping(self) -> None:
        cmd = build_docker_command("img:tag", ports="3000-3002:3000-3002")
        assert "3000-3002:3000-3002" in cmd

    def test_extra_args_split_into_tokens(self) -> None:
        cmd = build_docker_command("img:tag", extra_args="--gpus all")
        assert "--gpus" in cmd
        assert "all" in cmd

    def test_detach_flag_present_by_default(self) -> None:
        cmd = build_docker_command("img:tag")
        assert "-d" in cmd

    def test_detach_flag_absent_when_disabled(self) -> None:
        cmd = build_docker_command("img:tag", detach=False)
        assert "-d" not in cmd

    def test_remove_flag_present_by_default(self) -> None:
        cmd = build_docker_command("img:tag")
        assert "--rm" in cmd

    def test_command_starts_with_docker_run(self) -> None:
        cmd = build_docker_command("img:tag")
        assert cmd[0] == "docker"
        assert cmd[1] == "run"


# ─────────────────────────────────────────────────────────────────────────────
# TestArchitectureDetection
# ─────────────────────────────────────────────────────────────────────────────

class TestArchitectureDetection:
    """is_apple_silicon() uses injectable system/machine args for testability."""

    def test_darwin_arm64_is_apple_silicon(self) -> None:
        assert is_apple_silicon(system="Darwin", machine="arm64") is True

    def test_darwin_x86_64_is_not_apple_silicon(self) -> None:
        assert is_apple_silicon(system="Darwin", machine="x86_64") is False

    def test_linux_arm64_is_not_apple_silicon(self) -> None:
        assert is_apple_silicon(system="Linux", machine="arm64") is False

    def test_windows_arm64_is_not_apple_silicon(self) -> None:
        assert is_apple_silicon(system="Windows", machine="arm64") is False

    def test_resolve_docker_image_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CARLA_DOCKER_IMAGE env var overrides config image."""
        monkeypatch.setenv("CARLA_DOCKER_IMAGE", "my/custom:image")
        result = resolve_docker_image({})
        assert result == "my/custom:image"

    def test_resolve_docker_image_from_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without env var, image comes from config."""
        monkeypatch.delenv("CARLA_DOCKER_IMAGE", raising=False)
        cfg = {"runtime": {"docker_image": "carlasim/carla:0.9.15"}}
        result = resolve_docker_image(cfg)
        assert result == "carlasim/carla:0.9.15"

    def test_resolve_docker_image_default_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty config and no env var → hardcoded default."""
        monkeypatch.delenv("CARLA_DOCKER_IMAGE", raising=False)
        result = resolve_docker_image({})
        assert "carlasim/carla" in result


# ─────────────────────────────────────────────────────────────────────────────
# TestErrorFormatters
# ─────────────────────────────────────────────────────────────────────────────

class TestErrorFormatters:
    """format_carla_* functions return messages with expected content."""

    def test_unavailable_message_contains_version(self) -> None:
        msg = format_carla_unavailable_message("0.9.15")
        assert "0.9.15" in msg

    def test_unavailable_message_contains_wheel_hint(self) -> None:
        msg = format_carla_unavailable_message("0.9.15")
        assert ".whl" in msg

    def test_unavailable_message_contains_docs_reference(self) -> None:
        msg = format_carla_unavailable_message()
        assert "SETUP.md" in msg

    def test_unreachable_message_contains_host_and_port(self) -> None:
        msg = format_carla_unreachable_message("192.168.1.5", 2000)
        assert "192.168.1.5" in msg
        assert "2000" in msg

    def test_docker_mode_suggests_make_carla_docker(self) -> None:
        msg = format_carla_unreachable_message("localhost", 2000, runtime_mode="docker")
        assert "carla-docker" in msg

    def test_remote_mode_mentions_server_ip(self) -> None:
        msg = format_carla_unreachable_message("10.0.0.5", 2000, runtime_mode="remote")
        assert "10.0.0.5" in msg

    def test_local_mode_suggests_carlaue4(self) -> None:
        msg = format_carla_unreachable_message("localhost", 2000, runtime_mode="local")
        assert "CarlaUE4" in msg

    def test_unreachable_message_contains_docs_reference(self) -> None:
        msg = format_carla_unreachable_message("localhost", 2000)
        assert "PHASE1_SMOKE_TEST" in msg


# ─────────────────────────────────────────────────────────────────────────────
# TestSmokeTestCLI
# ─────────────────────────────────────────────────────────────────────────────

class TestSmokeTestCLI:
    """Smoke test CLI argument parsing and graceful failure modes."""

    def test_help_shows_host_option(self) -> None:
        from click.testing import CliRunner
        from scripts.smoke_test import main
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--host" in result.output

    def test_help_shows_port_option(self) -> None:
        from click.testing import CliRunner
        from scripts.smoke_test import main
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--port" in result.output

    def test_help_shows_ticks_option(self) -> None:
        from click.testing import CliRunner
        from scripts.smoke_test import main
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--ticks" in result.output

    def test_help_shows_profile_option(self) -> None:
        from click.testing import CliRunner
        from scripts.smoke_test import main
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--profile" in result.output

    @pytest.mark.skipif(
        importlib.util.find_spec("carla") is not None,
        reason="carla package is installed — CARLA unavailable path not exercised",
    )
    def test_exits_1_when_carla_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the carla package, smoke_test exits 1 with a clear message."""
        from click.testing import CliRunner
        from scripts.smoke_test import main
        monkeypatch.delenv("CARLA_HOST", raising=False)
        runner = CliRunner()
        result = runner.invoke(main, ["--host", "localhost", "--port", "2000"])
        assert result.exit_code == 1
