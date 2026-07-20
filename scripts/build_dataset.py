"""
scripts/build_dataset.py — Build the Phase 3 dataset index from Phase 2 episodes.

Discovers episode directories under ``dataset_engineering.raw_episodes_dir``,
validates and aligns each one, assigns deterministic train/val/test splits,
and writes these artifacts to a **versioned dataset directory**:

    data/processed/datasets/<dataset_id>/
        dataset_manifest.json   ← build summary (paths to every other artifact)
        episodes_index.jsonl    ← one row per discovered episode
        samples_index.jsonl     ← one row per usable (frame, control) tick
        stats.json              ← aggregate dataset statistics
        quality_report.json     ← validation/alignment issues per episode
        splits/train.jsonl      ← samples_index.jsonl rows filtered to split=train
        splits/val.jsonl        ← ... filtered to split=val
        splits/test.jsonl       ← ... filtered to split=test

``<dataset_id>`` defaults to a UTC timestamp (``dataset_YYYYMMDD_HHMMSS``),
overridable with ``--dataset-id``. Pass ``--output-dir`` to write to an
exact path instead of the versioned default — the dataset_id recorded in
the manifest then falls back to that directory's own name.

Alignment is strict by default: an episode whose frame/control/telemetry
counts or tick numbering disagree is excluded, not silently truncated. Pass
``--allow-partial-alignment`` to include such episodes truncated to their
usable prefix instead (recorded in quality_report.json either way).

Phase 3b hardening also runs by default and is purely informational (never
excludes anything): steering-spike/stuck-throttle detection and exact
duplicate-frame detection, both folded into quality_report.json, plus a
steering-angle histogram in stats.json. Disable either check with
``--no-outlier-detection`` / ``--no-duplicate-detection`` if not needed.

No CARLA, Docker, GPU, or PyTorch dependency — this only reads the flat
files Phase 2 already wrote to disk.

Usage::

    # Build from the default profile's raw episode directory. Writes to
    # data/processed/datasets/dataset_<timestamp>/:
    make build-dataset
    python scripts/build_dataset.py

    # Custom options:
    python scripts/build_dataset.py \\
        --raw-episodes-dir data/raw/episodes \\
        --dataset-id my_dataset \\
        --split-seed 7 \\
        --allow-partial-alignment

    # Exact output path instead of the versioned default:
    python scripts/build_dataset.py --output-dir data/processed/scratch
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

# Ensure src/ is importable when running from repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data.dataset_builder import build_dataset  # noqa: E402
from src.data.dataset_outliers import OutlierThresholds  # noqa: E402
from src.utils.config import get_nested, load_config  # noqa: E402
from src.utils.logging import configure_logging, get_logger  # noqa: E402

log = get_logger(__name__)


@click.command(
    name="build-dataset",
    help="Build the Phase 3 dataset index from collected Phase 2 episodes.",
)
@click.option("--profile", default=None, envvar="PROFILE",
              help="Runtime profile name (e.g. macos_docker).")
@click.option("--raw-episodes-dir", default=None, type=click.Path(path_type=Path),
              help="Directory containing Phase 2 episode directories."
                   " Overrides dataset_engineering.raw_episodes_dir.")
@click.option("--dataset-id", default=None,
              help="Identifier for this build; determines the versioned output"
                   " directory name (datasets_dir/<dataset_id>/). Defaults to a"
                   " UTC timestamp. Ignored if --output-dir is also given.")
@click.option("--output-dir", default=None, type=click.Path(path_type=Path),
              help="Write to this exact directory instead of the versioned"
                   " datasets_dir/<dataset_id>/ default. Overrides"
                   " dataset_engineering.output_dir.")
@click.option("--split-seed", default=None, type=int,
              help="Seed for deterministic split assignment.")
@click.option("--min-episode-ticks", default=None, type=int,
              help="Episodes with fewer usable ticks are excluded.")
@click.option("--require-valid/--no-require-valid", default=None,
              help="Exclude episodes that fail EpisodeValidator (default: true).")
@click.option("--allow-partial-alignment/--no-allow-partial-alignment", default=None,
              help="Include misaligned episodes truncated to their usable prefix"
                   " instead of excluding them (default: false — strict alignment).")
@click.option("--outlier-detection/--no-outlier-detection", default=None,
              help="Flag steering spikes and stuck throttle per episode"
                   " (default: true). Informational only — never excludes an episode.")
@click.option("--duplicate-detection/--no-duplicate-detection", default=None,
              help="Flag samples with byte-identical frame content"
                   " (default: true). Informational only — never excludes a sample.")
def main(
    profile: str | None,
    raw_episodes_dir: Path | None,
    dataset_id: str | None,
    output_dir: Path | None,
    split_seed: int | None,
    min_episode_ticks: int | None,
    require_valid: bool | None,
    allow_partial_alignment: bool | None,
    outlier_detection: bool | None,
    duplicate_detection: bool | None,
) -> None:
    cfg = load_config(profile=profile)
    configure_logging(
        level=get_nested(cfg, "logging", "level", default="INFO"),
        fmt=get_nested(cfg, "logging", "format", default="console"),
    )

    de = cfg.get("dataset_engineering", {})

    resolved_raw = Path(raw_episodes_dir or de.get("raw_episodes_dir", "data/raw/episodes"))
    resolved_seed = split_seed if split_seed is not None else de.get("split_seed", 42)
    resolved_min_ticks = (
        min_episode_ticks if min_episode_ticks is not None
        else de.get("min_episode_ticks", 1)
    )
    resolved_require_valid = (
        require_valid if require_valid is not None else de.get("require_valid", True)
    )
    resolved_allow_partial = (
        allow_partial_alignment if allow_partial_alignment is not None
        else de.get("allow_partial_alignment", False)
    )
    split_ratios = de.get("split_ratios", {"train": 0.8, "val": 0.1, "test": 0.1})

    od_cfg = de.get("outlier_detection", {})
    dd_cfg = de.get("duplicate_detection", {})
    resolved_outlier_detection = (
        outlier_detection if outlier_detection is not None else od_cfg.get("enabled", True)
    )
    resolved_duplicate_detection = (
        duplicate_detection if duplicate_detection is not None else dd_cfg.get("enabled", True)
    )
    resolved_thresholds = OutlierThresholds(
        steering_spike_delta=od_cfg.get("steering_spike_delta", 0.6),
        stuck_throttle_min=od_cfg.get("stuck_throttle_min", 0.9),
        stuck_speed_max_kph=od_cfg.get("stuck_speed_max_kph", 1.0),
        stuck_throttle_min_ticks=od_cfg.get("stuck_throttle_min_ticks", 40),
    )
    resolved_histogram_bins = de.get("steering_histogram_bins", 10)

    output_override = output_dir or de.get("output_dir")
    if output_override:
        resolved_out = Path(output_override)
        resolved_dataset_id = dataset_id or resolved_out.name
    else:
        resolved_dataset_id = dataset_id or de.get("dataset_id") or _generate_dataset_id()
        datasets_dir = Path(de.get("datasets_dir", "data/processed/datasets"))
        resolved_out = datasets_dir / resolved_dataset_id

    _print_header(resolved_raw, resolved_out, resolved_dataset_id, split_ratios,
                  resolved_seed, resolved_allow_partial)

    manifest = build_dataset(
        raw_episodes_dir=resolved_raw,
        output_dir=resolved_out,
        split_ratios=split_ratios,
        split_seed=resolved_seed,
        dataset_id=resolved_dataset_id,
        min_episode_ticks=resolved_min_ticks,
        require_valid=resolved_require_valid,
        allow_partial_alignment=resolved_allow_partial,
        outlier_detection=resolved_outlier_detection,
        outlier_thresholds=resolved_thresholds,
        duplicate_detection=resolved_duplicate_detection,
        steering_histogram_bins=resolved_histogram_bins,
    )
    stats = json.loads((resolved_out / manifest.statistics_path).read_text(encoding="utf-8"))
    split_counts = stats["split_counts"]
    quality = json.loads((resolved_out / manifest.quality_report_path).read_text(encoding="utf-8"))

    click.echo(f"  {_ok(f'Dataset built: {resolved_out}')}")
    click.echo(f"     Dataset ID: {manifest.dataset_id}")
    click.echo(f"     Episodes : {manifest.episode_count_included}/"
               f"{manifest.episode_count_discovered} included")
    click.echo(f"     Samples  : {manifest.sample_count}")
    click.echo(f"     Splits   : train={split_counts['train']}"
               f"  val={split_counts['val']}"
               f"  test={split_counts['test']}")
    if resolved_outlier_detection or resolved_duplicate_detection:
        click.echo(f"     Hardening: {quality['episodes_with_outliers']} episode(s) with outliers"
                   f"  ·  {quality['duplicate_frame_groups']} exact-duplicate frame group(s)")

    if manifest.episode_count_excluded:
        click.echo(
            f"  {_warn(f'{manifest.episode_count_excluded} episode(s) excluded')}"
            f" — see {resolved_out / manifest.quality_report_path}"
        )


def _generate_dataset_id() -> str:
    """Return a UTC-timestamped dataset identifier, e.g. ``dataset_20260708_030000``."""
    return f"dataset_{datetime.now(tz=timezone.utc):%Y%m%d_%H%M%S}"


# ── Display helpers ────────────────────────────────────────────────────────────

def _ok(msg: str) -> str:
    return f"\033[32m[ OK ]\033[0m  {msg}"


def _warn(msg: str) -> str:
    return f"\033[33m[WARN]\033[0m  {msg}"


def _print_header(
    raw_dir: Path, out_dir: Path, dataset_id: str, split_ratios: dict[str, float], seed: int,
    allow_partial_alignment: bool,
) -> None:
    width = 68
    print()
    print("─" * width)
    print("  \033[1mPhase 3 Dataset Engineering\033[0m")
    print("─" * width)
    print(f"  Dataset ID: {dataset_id}")
    print(f"  Source    : {raw_dir}")
    print(f"  Output    : {out_dir}")
    print(f"  Splits    : {split_ratios}  seed={seed}")
    print(f"  Alignment : {'partial (truncate)' if allow_partial_alignment else 'strict'}")
    print("─" * width)
    print()


if __name__ == "__main__":
    main()
