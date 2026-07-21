"""
scripts/compare_datasets.py — Compare two datasets and print/persist regression findings.

Runs :func:`src.quality.regression.compare_datasets` (writing
``regression_report.json`` into the candidate's directory), printing every
finding grouped by severity. No CARLA, Docker, GPU, or PyTorch dependency.

Usage::

    make compare-data
    python scripts/compare_datasets.py
    python scripts/compare_datasets.py --baseline <dir> --candidate <dir>
"""

from __future__ import annotations

import sys
from collections.abc import Callable
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
    warn,
)
from src.quality.artifacts import load_dataset_artifacts  # noqa: E402
from src.quality.config import load_quality_config  # noqa: E402
from src.quality.regression import compare_datasets, write_regression_report  # noqa: E402
from src.utils.config import load_config  # noqa: E402

#: Severity -> console badge formatter, in the order findings are grouped.
_SEVERITY_ORDER = ("failure", "warning", "improvement", "informational")
_SEVERITY_BADGE: dict[str, Callable[[str], str]] = {
    "failure": fail, "warning": warn, "improvement": ok, "informational": str,
}


@click.command(name="compare-datasets", help="Compare two datasets and report regression findings.")
@click.option("--profile", default=None, envvar="PROFILE",
              help="Runtime profile name (e.g. macos_docker).")
@click.option("--candidate", default=None, type=click.Path(path_type=Path),
              help="Candidate dataset directory. Defaults to the most recently"
                   " built dataset under dataset_engineering.datasets_dir.")
@click.option("--baseline", default=None, type=click.Path(path_type=Path),
              help="Baseline dataset directory. Defaults to the candidate's own"
                   " previous_artifact_id (from version.json) if versioned.")
def main(profile: str | None, candidate: Path | None, baseline: Path | None) -> None:
    cfg = load_config(profile=profile)
    quality_cfg = load_quality_config(cfg)
    de = cfg.get("dataset_engineering", {})

    candidate_dir = resolve_dataset_dir(candidate, de)
    if candidate_dir is None:
        datasets_dir = de.get("datasets_dir", "data/processed/datasets")
        click.echo(fail(f"No dataset found under {datasets_dir}"), err=True)
        click.echo("    Run: make build-dataset", err=True)
        sys.exit(1)

    candidate_artifact = load_dataset_artifacts(candidate_dir)
    baseline_artifact = resolve_baseline_artifact(baseline, candidate_dir)

    if baseline_artifact is None:
        click.echo(warn(
            "No baseline available — every dimension will be reported informational."
            " Pass --baseline explicitly, or run `make version` first.",
        ), err=True)

    report = compare_datasets(baseline_artifact, candidate_artifact, quality_cfg)
    write_regression_report(candidate_dir, report)

    print_header("Dataset Comparison", [
        f"Baseline  : {report.baseline_artifact_id or '(none)'}",
        f"Candidate : {report.candidate_artifact_id}",
        f"Findings  : {len(report.findings)}",
    ])

    by_severity: dict[str, list[str]] = {sev: [] for sev in _SEVERITY_ORDER}
    for finding in report.findings:
        by_severity.setdefault(finding.severity, []).append(finding.message)

    any_blocking = False
    for severity in _SEVERITY_ORDER:
        messages = by_severity.get(severity, [])
        if not messages:
            continue
        badge_fn = _SEVERITY_BADGE[severity]
        print(f"  {severity.upper()} ({len(messages)}):")
        for message in messages:
            print(f"    {badge_fn(message)}")
        print()
        if severity == "failure":
            any_blocking = True

    if any_blocking:
        sys.exit(1)


if __name__ == "__main__":
    main()
