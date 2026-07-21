"""
scripts/recommend_data.py — Print ranked (town, weather) collection recommendations.

Runs :func:`src.quality.coverage.compute_coverage` (writing
``coverage_report.json``) and :func:`src.quality.coverage.recommend_collection`
against one dataset directory, printing the coverage matrix summary and a
ranked, directly-actionable list of what to collect next. No CARLA,
Docker, GPU, or PyTorch dependency.

Usage::

    make recommend-data
    python scripts/recommend_data.py
    python scripts/recommend_data.py --dataset-dir data/processed/datasets/<id>
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._format import fail, ok, print_header, resolve_dataset_dir  # noqa: E402
from src.quality.artifacts import load_dataset_artifacts  # noqa: E402
from src.quality.config import load_quality_config  # noqa: E402
from src.quality.coverage import (  # noqa: E402
    compute_coverage,
    recommend_collection,
    write_coverage_report,
)
from src.utils.config import load_config  # noqa: E402


@click.command(
    name="recommend-data",
    help="Print ranked (town, weather) collection recommendations.",
)
@click.option("--profile", default=None, envvar="PROFILE",
              help="Runtime profile name (e.g. macos_docker).")
@click.option("--dataset-dir", default=None, type=click.Path(path_type=Path),
              help="Dataset directory to analyze. Defaults to the most recently"
                   " built dataset under dataset_engineering.datasets_dir.")
def main(profile: str | None, dataset_dir: Path | None) -> None:
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
    coverage = compute_coverage(artifact, quality_cfg)
    write_coverage_report(resolved_dir, coverage)
    recommendations = recommend_collection(coverage, quality_cfg)

    print_header("Data Collection Recommendations", [
        f"Dataset  : {artifact.artifact_id}",
        f"Coverage : {coverage.cells_met}/{coverage.cells_total} target cells met"
        f" ({coverage.coverage_pct:.1f}%)",
    ])

    if not recommendations:
        click.echo(f"  {ok('All target (town, weather) cells met — nothing to recommend.')}")
        print()
        return

    for i, rec in enumerate(recommendations, start=1):
        print(f"  {i}. {rec.message}"
              f"  [gap: {rec.gap} episode(s)]")
    print()


if __name__ == "__main__":
    main()
