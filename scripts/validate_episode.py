"""
scripts/validate_episode.py — Validate a collected episode directory.

Checks that the episode contains all required files, that JSONL files are
parseable, that metadata and manifest fields are complete, and that frame
filenames are sequential.

Usage::

    # Validate a specific episode:
    python scripts/validate_episode.py data/raw/episodes/episode_20260707_...

    # Also write the outcome back into manifest.json's validation_status
    # (collection always writes "unchecked" — see ADR-003 in
    # docs/PHASE2_DATA_COLLECTION.md):
    python scripts/validate_episode.py data/raw/episodes/episode_20260707_... --fix-manifest

    # Via Makefile (validates most recent episode by default):
    make validate-episode
    make validate-episode EPISODE_DIR=data/raw/episodes/episode_20260707_...
    make fix-manifest EPISODE_DIR=data/raw/episodes/episode_20260707_...
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

# Ensure src/ is importable when running from repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data.validation import EpisodeValidator, write_validation_status  # noqa: E402


@click.command(
    name="validate-episode",
    help="Validate a collected episode directory (no CARLA required).",
)
@click.argument(
    "episode_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Print all check results, not just failures.",
)
@click.option(
    "--fix-manifest",
    is_flag=True,
    default=False,
    help="Write the validation outcome back into manifest.json's validation_status.",
)
def main(episode_dir: Path, verbose: bool, fix_manifest: bool) -> None:
    """Validate EPISODE_DIR — the root of a single collected episode."""
    validator = EpisodeValidator()
    result = validator.validate(episode_dir)

    width = 64
    print()
    print("─" * width)
    print("  \033[1mEpisode Validation\033[0m")
    print(f"  {episode_dir.name}")
    print("─" * width)

    for check in result.checks:
        if verbose or not check.passed:
            badge = "\033[32m[ OK ]\033[0m" if check.passed else "\033[31m[FAIL]\033[0m"
            print(f"  {badge}  {check.name:<35}  {check.detail}")

    if fix_manifest:
        try:
            write_validation_status(episode_dir, result.valid)
            status = "valid" if result.valid else "invalid"
            print(f"\n  \033[36m[FIX]\033[0m  manifest.json validation_status → {status!r}")
        except FileNotFoundError as exc:
            print(f"\n  \033[33m[WARN]\033[0m  --fix-manifest skipped: {exc}")

    print()
    if result.valid:
        passed = sum(1 for c in result.checks if c.passed)
        print(f"\033[32m  ✓ Valid  —  {passed}/{len(result.checks)} checks passed\033[0m")
        print()
        sys.exit(0)
    else:
        print(f"\033[31m  ✗ Invalid  —  {len(result.errors)} error(s):\033[0m")
        for err in result.errors:
            print(f"    • {err}")
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()
