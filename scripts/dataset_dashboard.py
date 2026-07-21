"""
scripts/dataset_dashboard.py — Generate the self-contained HTML engineering dashboard.

Runs :func:`src.quality.dashboard.generate_dashboard` for one dataset,
writing a single ``.html`` file with quality, coverage, validation-gate,
recent-changes, quality-trend, and lineage sections. No CARLA, Docker,
GPU, or PyTorch dependency; the file opens in any browser, no server
required.

Usage::

    make dashboard
    python scripts/dataset_dashboard.py
    python scripts/dataset_dashboard.py --dataset-dir data/processed/datasets/<id>
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._format import fail, ok, print_header, resolve_dataset_dir  # noqa: E402
from src.quality.config import load_quality_config  # noqa: E402
from src.quality.dashboard import generate_dashboard  # noqa: E402
from src.utils.config import load_config  # noqa: E402


@click.command(
    name="dataset-dashboard",
    help="Generate the self-contained HTML engineering dashboard.",
)
@click.option("--profile", default=None, envvar="PROFILE",
              help="Runtime profile name (e.g. macos_docker).")
@click.option("--dataset-dir", default=None, type=click.Path(path_type=Path),
              help="Dataset directory to build a dashboard for. Defaults to the"
                   " most recently built dataset under dataset_engineering.datasets_dir.")
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

    datasets_dir_path = Path(de.get("datasets_dir", "data/processed/datasets"))
    output_path = generate_dashboard(resolved_dir, quality_cfg, datasets_dir=datasets_dir_path)

    print_header("Dataset Dashboard", [f"Dataset : {resolved_dir.name}"])
    click.echo(f"  {ok(f'Dashboard written: {output_path}')}")
    click.echo(f"  Open in a browser: file://{output_path.resolve()}")
    print()


if __name__ == "__main__":
    main()
