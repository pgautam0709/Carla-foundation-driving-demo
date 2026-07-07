"""
src/utils/config.py — Configuration loader with deep-merge profile support.

Usage::

    from src.utils.config import load_config, get_nested

    cfg = load_config()                           # default only
    cfg = load_config(profile="macos_docker")     # default + profile override
    cfg = load_config(profile="remote_carla")

    # CARLA connection — also overridable by env vars CARLA_HOST / CARLA_PORT
    host = get_nested(cfg, "carla_connection", "host", default="localhost")
    port = get_nested(cfg, "carla_connection", "port", default=2000)

Environment variable overrides (applied automatically by load_config):
    CARLA_HOST              → cfg["carla_connection"]["host"]
    CARLA_PORT              → cfg["carla_connection"]["port"]  (coerced to int)
    CARLA_VERSION           → cfg["carla_connection"]["version"]
    CARLA_PYTHON_API_PATH   → cfg["carla_connection"]["python_api_path"]
"""

from __future__ import annotations

import contextlib
import copy
import os
from pathlib import Path
from typing import Any

import yaml

# ── Types ──────────────────────────────────────────────────────────────────────
ConfigDict = dict[str, Any]

# ── Constants ──────────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _REPO_ROOT / "config"
_DEFAULT_CONFIG = _CONFIG_DIR / "default.yaml"
_PROFILES_DIR = _CONFIG_DIR / "profiles"


# ── Public API ─────────────────────────────────────────────────────────────────

def load_config(
    base_path: Path | str | None = None,
    profile: str | None = None,
    profile_path: Path | str | None = None,
) -> ConfigDict:
    """Load and return the merged configuration dictionary.

    Merge order (later values win):
        1. ``config/default.yaml``  (or *base_path* if provided)
        2. ``config/profiles/<profile>.yaml``  (if *profile* is set)
        3. ``profile_path``  (if provided directly)
        4. Environment variable overrides (CARLA_HOST, CARLA_PORT, …)

    Args:
        base_path: Explicit path to a base YAML file.
        profile: Name of a built-in profile (e.g. ``"macos_docker"``).
        profile_path: Explicit path to a profile YAML override file.

    Returns:
        Merged configuration as a plain Python dict.

    Raises:
        FileNotFoundError: If any specified config file does not exist.
        ValueError: If both *profile* and *profile_path* are supplied.
    """
    if profile is not None and profile_path is not None:
        raise ValueError("Specify either 'profile' or 'profile_path', not both.")

    # 1. Load base config
    resolved_base = Path(base_path) if base_path else _DEFAULT_CONFIG
    cfg = _load_yaml(resolved_base)

    # 2. Load and merge profile
    if profile is not None:
        resolved_profile = _PROFILES_DIR / f"{profile}.yaml"
        override = _load_yaml(resolved_profile)
        cfg = _deep_merge(cfg, override)

    if profile_path is not None:
        override = _load_yaml(Path(profile_path))
        cfg = _deep_merge(cfg, override)

    # 3. Apply environment variable overrides (always win over YAML)
    cfg = apply_env_overrides(cfg)

    return cfg


def apply_env_overrides(cfg: ConfigDict) -> ConfigDict:
    """Apply CARLA_* environment variables into cfg["carla_connection"].

    Environment variables take precedence over all YAML values. This is
    the standard 12-factor approach: config files carry defaults, the
    deployment environment supplies the runtime address.

    Supported variables:
        CARLA_HOST            → carla_connection.host
        CARLA_PORT            → carla_connection.port  (coerced to int)
        CARLA_VERSION         → carla_connection.version
        CARLA_PYTHON_API_PATH → carla_connection.python_api_path

    Args:
        cfg: Configuration dict produced by load_config or deep_merge.

    Returns:
        New dict with env var overrides applied (input is not mutated).
    """
    result = copy.deepcopy(cfg)
    conn: ConfigDict = result.setdefault("carla_connection", {})

    if host := os.environ.get("CARLA_HOST"):
        conn["host"] = host

    if port_str := os.environ.get("CARLA_PORT"):
        with contextlib.suppress(ValueError):
            conn["port"] = int(port_str)

    if version := os.environ.get("CARLA_VERSION"):
        conn["version"] = version

    if api_path := os.environ.get("CARLA_PYTHON_API_PATH"):
        conn["python_api_path"] = api_path

    return result


def get_nested(cfg: ConfigDict, *keys: str, default: Any = None) -> Any:
    """Safely retrieve a deeply nested value.

    Example::

        host = get_nested(cfg, "carla_connection", "host", default="localhost")
        port = get_nested(cfg, "carla_connection", "port", default=2000)

    Args:
        cfg: The configuration dict.
        *keys: Sequence of nested keys to traverse.
        default: Value to return if any key is missing.

    Returns:
        The value at the nested key path, or *default* if not found.
    """
    current: Any = cfg
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, None)
        if current is None:
            return default
    return current


# ── Internal helpers ───────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> ConfigDict:
    """Load a YAML file and return its contents as a dict.

    Args:
        path: Path to the YAML file.

    Returns:
        Parsed YAML as a dict. Empty dict for empty/null files.

    Raises:
        FileNotFoundError: If *path* does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def _deep_merge(base: ConfigDict, override: ConfigDict) -> ConfigDict:
    """Recursively merge *override* into *base*. Returns a new dict.

    Args:
        base: The base configuration dict.
        override: Values that take precedence over *base*.

    Returns:
        Merged dict. Neither input is mutated.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
