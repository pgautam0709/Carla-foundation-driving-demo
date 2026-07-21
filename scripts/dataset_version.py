"""
scripts/dataset_version.py — Compute and write version.json + CHANGELOG.md for a dataset.

Runs :func:`src.quality.versioning.write_version_artifacts`, which is
idempotent and safe to re-run — it only ever overwrites the two files it
writes, never any Phase 3 artifact. No CARLA, Docker, GPU, or PyTorch
dependency.

Usage::

    make version
    python scripts/dataset_version.py
    python scripts/dataset_version.py --dataset-dir data/processed/datasets/<id>
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
from src.quality.versioning import write_version_artifacts  # noqa: E402
from src.utils.config import load_config  # noqa: E402


@click.command(
    name="dataset-version",
    help="Compute and write version.json + CHANGELOG.md for a dataset.",
)
@click.option("--profile", default=None, envvar="PROFILE",
              help="Runtime profile name (e.g. macos_docker).")
@click.option("--dataset-dir", default=None, type=click.Path(path_type=Path),
              help="Dataset directory to version. Defaults to the most recently"
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

    version = write_version_artifacts(resolved_dir, cfg)

    print_header("Dataset Version", [
        f"Artifact ID : {version.artifact_id}",
        f"Previous    : {version.previous_artifact_id or '(none — first version)'}",
        f"Config hash : {version.config_hash[:12]}…",
        f"Generator   : {version.generator_version}",
    ])
    click.echo(f"  {ok(f'Wrote {resolved_dir / quality_cfg.versioning.version_filename}')}")
    click.echo(f"  {ok(f'Wrote {resolved_dir / quality_cfg.versioning.changelog_filename}')}")
    print()


if __name__ == "__main__":
    main()
