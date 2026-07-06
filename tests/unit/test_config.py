"""
tests/unit/test_config.py — Unit tests for src/utils/config.py

Tests run without CARLA, without a GPU, and without any network access.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.utils.config import _deep_merge, _load_yaml, get_nested, load_config

# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def base_yaml(tmp_path: Path) -> Path:
    """Write a minimal base config YAML and return its path."""
    data = {
        "project": {"name": "test", "version": "0.0.1"},
        "simulation": {"host": "localhost", "port": 2000, "timeout_s": 10.0},
        "training": {"epochs": 10, "batch_size": 32, "device": "cpu"},
        "logging": {"level": "INFO", "format": "console"},
    }
    path = tmp_path / "base.yaml"
    path.write_text(yaml.dump(data))
    return path


@pytest.fixture()
def override_yaml(tmp_path: Path) -> Path:
    """Write a profile override YAML."""
    data = {
        "simulation": {"host": "remote-host", "timeout_s": 5.0},
        "training": {"epochs": 2, "device": "mps"},
    }
    path = tmp_path / "override.yaml"
    path.write_text(yaml.dump(data))
    return path


# ── _load_yaml ─────────────────────────────────────────────────────────────────

class TestLoadYaml:
    def test_loads_valid_file(self, base_yaml: Path) -> None:
        cfg = _load_yaml(base_yaml)
        assert cfg["project"]["name"] == "test"

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            _load_yaml(tmp_path / "nonexistent.yaml")

    def test_empty_yaml_returns_empty_dict(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("")
        cfg = _load_yaml(path)
        assert cfg == {}


# ── _deep_merge ────────────────────────────────────────────────────────────────

class TestDeepMerge:
    def test_top_level_override(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 99}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 99}

    def test_nested_override_partial(self) -> None:
        base = {"sim": {"host": "localhost", "port": 2000}}
        override = {"sim": {"host": "remote"}}
        result = _deep_merge(base, override)
        assert result["sim"]["host"] == "remote"
        assert result["sim"]["port"] == 2000, "Non-overridden nested keys must survive"

    def test_deep_override_does_not_mutate_base(self) -> None:
        base = {"a": {"b": 1}}
        override = {"a": {"b": 2}}
        result = _deep_merge(base, override)
        assert base["a"]["b"] == 1, "Original base must not be mutated"
        assert result["a"]["b"] == 2

    def test_override_adds_new_key(self) -> None:
        base = {"a": 1}
        override = {"z": 99}
        result = _deep_merge(base, override)
        assert result["a"] == 1
        assert result["z"] == 99

    def test_scalar_overrides_dict(self) -> None:
        """If override replaces a dict with a scalar, scalar wins."""
        base = {"key": {"nested": 1}}
        override = {"key": "flat_value"}
        result = _deep_merge(base, override)
        assert result["key"] == "flat_value"


# ── load_config ────────────────────────────────────────────────────────────────

class TestLoadConfig:
    def test_loads_default_config(self) -> None:
        """The default config must load without errors."""
        cfg = load_config()
        assert "simulation" in cfg
        assert "training" in cfg
        assert "logging" in cfg

    def test_profile_override_merges(self, base_yaml: Path, override_yaml: Path) -> None:
        cfg = load_config(base_path=base_yaml, profile_path=override_yaml)
        assert cfg["simulation"]["host"] == "remote-host"    # overridden
        assert cfg["simulation"]["port"] == 2000             # inherited from base
        assert cfg["training"]["epochs"] == 2                # overridden
        assert cfg["training"]["batch_size"] == 32           # inherited

    def test_raises_on_both_profile_and_profile_path(
        self, base_yaml: Path, override_yaml: Path
    ) -> None:
        with pytest.raises(ValueError, match="not both"):
            load_config(base_path=base_yaml, profile="local_dev", profile_path=override_yaml)

    def test_builtin_profile_local_dev(self) -> None:
        """Built-in local_dev profile must merge cleanly."""
        cfg = load_config(profile="local_dev")
        assert cfg["training"]["device"] == "cpu"

    def test_builtin_profile_ci(self) -> None:
        cfg = load_config(profile="ci")
        assert cfg["training"]["epochs"] == 1


# ── get_nested ─────────────────────────────────────────────────────────────────

class TestGetNested:
    def test_retrieves_deep_key(self) -> None:
        cfg = {"a": {"b": {"c": 42}}}
        assert get_nested(cfg, "a", "b", "c") == 42

    def test_returns_default_on_missing(self) -> None:
        cfg: dict = {}
        assert get_nested(cfg, "missing", "key", default="fallback") == "fallback"

    def test_returns_none_default(self) -> None:
        cfg: dict = {"a": 1}
        assert get_nested(cfg, "b") is None

    def test_non_dict_intermediate_returns_default(self) -> None:
        cfg = {"a": "not-a-dict"}
        assert get_nested(cfg, "a", "b", default="x") == "x"
