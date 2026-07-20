"""
scripts/inspect_dataset.py — Print a human-readable summary of a built dataset.

Reads ``dataset_manifest.json``, ``stats.json``, and ``quality_report.json``
written by ``scripts/build_dataset.py`` and prints episode/sample counts,
split counts, per-signal statistics, alignment/truncation counts, and
quality issues. Read-only — no CARLA, Docker, GPU, or PyTorch dependency.

``build_dataset.py`` writes each build to its own versioned directory under
``dataset_engineering.datasets_dir`` (default ``data/processed/datasets``).
When ``--dataset-dir`` is not given, this script inspects the most recently
built dataset under that directory (by modification time) — mirroring
``make validate-episode``'s "most recent episode" default.

Usage::

    make inspect-dataset
    python scripts/inspect_dataset.py
    python scripts/inspect_dataset.py --dataset-dir data/processed/datasets/<dataset_id> --verbose
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

# Ensure src/ is importable when running from repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.utils.config import load_config  # noqa: E402


@click.command(
    name="inspect-dataset",
    help="Print a human-readable summary of a dataset built by build_dataset.py.",
)
@click.option("--profile", default=None, envvar="PROFILE",
              help="Runtime profile name (e.g. macos_docker).")
@click.option("--dataset-dir", default=None, type=click.Path(path_type=Path),
              help="Directory containing dataset_manifest.json, e.g."
                   " data/processed/datasets/<dataset_id>. Defaults to the most"
                   " recently built dataset under dataset_engineering.datasets_dir.")
@click.option("--verbose", "-v", is_flag=True, default=False,
              help="List every quality issue, not just the summary counts.")
def main(profile: str | None, dataset_dir: Path | None, verbose: bool) -> None:
    cfg = load_config(profile=profile)
    de = cfg.get("dataset_engineering", {})

    if dataset_dir is not None:
        resolved_dir = Path(dataset_dir)
    elif de.get("output_dir"):
        resolved_dir = Path(de["output_dir"])
    else:
        datasets_dir = Path(de.get("datasets_dir", "data/processed/datasets"))
        latest = _resolve_latest_dataset_dir(datasets_dir)
        if latest is None:
            click.echo(_fail(f"No dataset found under {datasets_dir}"), err=True)
            click.echo("    Run: make build-dataset", err=True)
            sys.exit(1)
        resolved_dir = latest

    manifest_path = resolved_dir / "dataset_manifest.json"
    if not manifest_path.exists():
        click.echo(_fail(f"No dataset manifest found at {manifest_path}"), err=True)
        click.echo("    Run: make build-dataset", err=True)
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    stats_path = resolved_dir / manifest.get("statistics_path", "stats.json")
    stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}

    quality_path = resolved_dir / manifest.get("quality_report_path", "quality_report.json")
    quality = (
        json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else None
    )

    _print_summary(resolved_dir, manifest, stats, quality, verbose)


def _resolve_latest_dataset_dir(datasets_dir: Path) -> Path | None:
    """Return the most recently modified immediate subdirectory of *datasets_dir*.

    Args:
        datasets_dir: Parent directory containing one subdirectory per
            dataset build (see ``dataset_engineering.datasets_dir``).

    Returns:
        The most recently modified subdirectory, or None if *datasets_dir*
        does not exist or contains no subdirectories.
    """
    if not datasets_dir.is_dir():
        return None
    candidates = [p for p in datasets_dir.iterdir() if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ── Rendering ──────────────────────────────────────────────────────────────────

def _print_summary(
    dataset_dir: Path,
    manifest: dict[str, Any],
    stats: dict[str, Any],
    quality: dict[str, Any] | None,
    verbose: bool,
) -> None:
    width = 68
    splits = stats.get("split_counts", {})
    towns = stats.get("towns", {})

    print()
    print("─" * width)
    print("  \033[1mDataset Inspection\033[0m")
    print(f"  {dataset_dir}")
    print("─" * width)
    print(f"  Dataset ID: {manifest.get('dataset_id')}")
    print(f"  Built     : {manifest.get('created_at')}  (git {manifest.get('git_commit')})")
    print(f"  Source    : {manifest.get('raw_episodes_dir')}")
    print(f"  Schema    : {manifest.get('schema_version')}")
    alignment_mode = "partial (truncate)" if manifest.get("allow_partial_alignment") else "strict"
    print(f"  Alignment : {alignment_mode}")
    print()
    print(f"  Episodes  : {manifest.get('episode_count_included')} included / "
          f"{manifest.get('episode_count_discovered')} discovered "
          f"({manifest.get('episode_count_excluded')} excluded)")
    print(f"  Samples   : {manifest.get('sample_count')}")
    print(f"  Splits    : train={splits.get('train', 0)}  "
          f"val={splits.get('val', 0)}  test={splits.get('test', 0)}"
          f"  (files under {manifest.get('splits_dir', 'splits')}/)")

    if towns:
        print()
        print("  Towns:")
        for town, count in sorted(towns.items()):
            print(f"    {town:<20} {count} episode(s)")

    print()
    print("  Signal stats (mean / std / min / max):")
    for name in ("throttle", "brake", "steer", "speed_kph"):
        s = stats.get(name)
        if s is None:
            print(f"    {name:<10} : (no samples)")
        else:
            print(f"    {name:<10} : {s['mean']:.3f} / {s['std']:.3f} "
                  f"/ {s['min']:.3f} / {s['max']:.3f}")

    histogram = stats.get("steering_histogram") or []
    if any(b["count"] for b in histogram):
        print()
        print("  Steering histogram (informational, no sampling applied):")
        max_count = max(b["count"] for b in histogram)
        bar_width = 30
        for b in histogram:
            bar_len = int(bar_width * b["count"] / max_count) if max_count else 0
            label = f"[{b['range_min']:+.2f}, {b['range_max']:+.2f})"
            print(f"    {label:<16} {'#' * bar_len:<{bar_width}} {b['count']}")

    if quality is not None:
        print()
        print(f"  Quality report : {manifest.get('quality_report_path')}")
        print(f"    Valid       : {quality.get('episodes_valid')}")
        print(f"    Invalid     : {quality.get('episodes_invalid')}")
        print(f"    Misaligned  : {quality.get('episodes_misaligned', 0)}")
        print(f"    Truncated   : {quality.get('episodes_truncated', 0)}"
              " (included despite misalignment)")
        print(f"    Outliers    : {quality.get('episodes_with_outliers', 0)} episode(s)"
              " (steering spikes / stuck throttle)")
        print(f"    Duplicates  : {quality.get('duplicate_frame_groups', 0)}"
              " group(s) of identical frames")
        issues = quality.get("issues", [])
        print(f"    Issues      : {len(issues)}"
              + ("" if verbose or not issues else " (use --verbose to list)"))
        if verbose:
            for issue in issues:
                badge = (
                    "\033[31m[ERROR]\033[0m" if issue.get("severity") == "error"
                    else "\033[33m[WARN ]\033[0m"
                )
                print(f"      {badge}  {issue.get('episode_id')}: {issue.get('message')}")

    print("─" * width)
    print()


def _fail(msg: str) -> str:
    return f"\033[31m[FAIL]\033[0m  {msg}"


if __name__ == "__main__":
    main()
