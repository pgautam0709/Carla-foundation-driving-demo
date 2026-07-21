"""
scripts/dataset_review.py — Print a deterministic star review for a dataset.

Runs :func:`src.quality.review.compute_review` (writing ``review.json``)
against one dataset directory, printing the star rating, strengths,
weaknesses, and ranked collection recommendations. No CARLA, Docker, GPU,
or PyTorch dependency.

Usage::

    make review
    python scripts/dataset_review.py
    python scripts/dataset_review.py --dataset-dir data/processed/datasets/<id>
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._format import (  # noqa: E402
    fail,
    print_header,
    resolve_baseline_artifact,
    resolve_dataset_dir,
)
from src.quality.artifacts import load_dataset_artifacts  # noqa: E402
from src.quality.config import load_quality_config  # noqa: E402
from src.quality.review import compute_review, write_review  # noqa: E402
from src.utils.config import load_config  # noqa: E402


@click.command(name="dataset-review", help="Print a deterministic star review for a dataset.")
@click.option("--profile", default=None, envvar="PROFILE",
              help="Runtime profile name (e.g. macos_docker).")
@click.option("--dataset-dir", default=None, type=click.Path(path_type=Path),
              help="Dataset directory to review. Defaults to the most recently"
                   " built dataset under dataset_engineering.datasets_dir.")
@click.option("--baseline", default=None, type=click.Path(path_type=Path),
              help="Dataset directory to compare against. Defaults to this"
                   " dataset's own previous_artifact_id if versioned.")
def main(profile: str | None, dataset_dir: Path | None, baseline: Path | None) -> None:
    cfg = load_config(profile=profile)
    quality_cfg = load_quality_config(cfg)
    de = cfg.get("dataset_engineering", {})

    resolved_dir = resolve_dataset_dir(dataset_dir, de)
    if resolved_dir is None:
        datasets_dir = de.get("datasets_dir", "data/processed/datasets")
        click.echo(fail(f"No dataset found under {datasets_dir}"), err=True)
        click.echo("    Run: make build-dataset", err=True)
        sys.exit(1)

    artifact = load_dataset_artifacts(resolved_dir)
    baseline_artifact = resolve_baseline_artifact(baseline, resolved_dir)

    review = compute_review(artifact, quality_cfg, baseline=baseline_artifact)
    write_review(resolved_dir, review)

    stars = "★" * review.stars + "☆" * (5 - review.stars)
    print_header("Dataset Review", [
        f"Dataset : {artifact.artifact_id}",
        f"Rating  : {stars}  ({review.stars}/5, grade {review.grade}, "
        f"{review.overall_score:.1f}/100)",
    ])

    print("  Strengths:")
    for item in review.strengths:
        print(f"    + {item}")
    if not review.strengths:
        print("    (none)")

    print()
    print("  Weaknesses:")
    for item in review.weaknesses:
        print(f"    - {item}")
    if not review.weaknesses:
        print("    (none)")

    print()
    print("  Recommendations:")
    for item in review.recommendations:
        print(f"    * {item}")
    if not review.recommendations:
        print("    (none — coverage target met)")
    print()


if __name__ == "__main__":
    main()
