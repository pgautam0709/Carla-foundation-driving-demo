"""
scripts/dataset_quality.py — Compute a dataset's quality score and training-gate verdict.

Runs :func:`src.quality.scoring.compute_quality_score` (writing
``quality_score.json``) and :func:`src.quality.gates.evaluate_gate`
(writing ``gate_report.json``) against one dataset directory, printing the
per-metric breakdown and every gate check's pass/fail reason. No CARLA,
Docker, GPU, or PyTorch dependency.

Usage::

    make quality
    python scripts/dataset_quality.py
    python scripts/dataset_quality.py --dataset-dir data/processed/datasets/<id>
    python scripts/dataset_quality.py --baseline data/processed/datasets/<id>
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
    ok,
    print_header,
    resolve_baseline_artifact,
    resolve_dataset_dir,
)
from src.quality.artifacts import load_dataset_artifacts  # noqa: E402
from src.quality.config import load_quality_config  # noqa: E402
from src.quality.gates import evaluate_gate, write_gate_report  # noqa: E402
from src.quality.scoring import compute_quality_score, write_quality_score  # noqa: E402
from src.utils.config import load_config  # noqa: E402


@click.command(
    name="dataset-quality",
    help="Compute a dataset's quality score and training-gate verdict.",
)
@click.option("--profile", default=None, envvar="PROFILE",
              help="Runtime profile name (e.g. macos_docker).")
@click.option("--dataset-dir", default=None, type=click.Path(path_type=Path),
              help="Dataset directory to score. Defaults to the most recently"
                   " built dataset under dataset_engineering.datasets_dir.")
@click.option("--baseline", default=None, type=click.Path(path_type=Path),
              help="Dataset directory to compare against for the gate's regression"
                   " check. Defaults to this dataset's own previous_artifact_id"
                   " (from version.json) if it has been versioned.")
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

    score = compute_quality_score(artifact, quality_cfg)
    write_quality_score(resolved_dir, score)
    gate_report = evaluate_gate(artifact, quality_cfg, baseline=baseline_artifact)
    write_gate_report(resolved_dir, gate_report)

    print_header("Dataset Quality", [
        f"Dataset   : {artifact.artifact_id}",
        f"Directory : {resolved_dir}",
        f"Score     : {score.overall_score:.1f}/100  (grade {score.grade})",
    ])

    for name, result in sorted(score.metrics.items()):
        weight = score.weights_used.get(name, 0.0)
        print(f"  {name:<18} {result.raw_score:6.1f}  (weight {weight:.2f})  {result.detail}")

    print()
    verdict = ok("Training gate: PASS") if gate_report.passed else fail("Training gate: FAIL")
    click.echo(f"  {verdict}")
    for check in gate_report.checks:
        badge = ok(check.name) if check.passed else fail(check.name)
        click.echo(f"    {badge}  {check.detail}")
    print()

    if not gate_report.passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
