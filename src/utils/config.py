"""
src/utils/config.py — Configuration loader with deep-merge profile support.

Usage::

    from src.utils.config import load_config

    cfg = load_config()                          # default only
    cfg = load_config(profile="local_dev")       # default + profile override
    cfg = load_config(profile="linux_gpu")

    # Access nested keys
    host = cfg["simulation"]["host"]
"""

from __future__ import annotations

import copy
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

    Args:
        base_path: Explicit path to a base YAML file.
        profile: Name of a built-in profile (e.g. ``"local_dev"``).
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

    return cfg


def get_nested(cfg: ConfigDict, *keys: str, default: Any = None) -> Any:
    """Safely retrieve a deeply nested value.

    Example::

        host = get_nested(cfg, "simulation", "host", default="localhost")
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
    """Load a YAML file and return its contents as a dict."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def _deep_merge(base: ConfigDict, override: ConfigDict) -> ConfigDict:
    """Recursively merge *override* into *base*. Returns a new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
