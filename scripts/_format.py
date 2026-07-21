"""
scripts/_format.py — Shared console formatting + dataset/baseline resolution for the CLI scripts.

``build_dataset.py`` and ``inspect_dataset.py`` (Phase 3) each defined their
own private ``_ok()`` / ``_warn()`` / ``_fail()`` — the six Phase 3.5 CLIs
(``dataset_quality.py``, ``dataset_review.py``, ``compare_datasets.py``,
``dataset_dashboard.py``, ``recommend_data.py``, ``dataset_version.py``) all
need the identical formatting, so this module is the one place it is
written (docs/PHASE3_5_ARCHITECTURE_REVISION.md "Duplication Caught During
This Revision," Finding D). It also holds :func:`resolve_dataset_dir` and
:func:`resolve_baseline_artifact` — every one of those six CLIs needs the
identical "which dataset, which baseline" defaulting logic too, so it is
written once here rather than six times (or copy-imported between CLI
modules, which would make one script's flags a private implementation
detail of another). Every script in ``scripts/`` imports from here instead
of redefining any of this.
"""

from __future__ import annotations

from pathlib import Path

import click

from src.quality.artifacts import (
    ARTIFACT_LOAD_ERRORS,
    ArtifactNotFoundError,
    DatasetArtifact,
    load_dataset_artifacts,
    load_version_record,
    resolve_latest_dataset_dir,
)
from src.utils.config import ConfigDict


def ok(msg: str) -> str:
    """Format *msg* with a green ``[ OK ]`` prefix."""
    return f"\033[32m[ OK ]\033[0m  {msg}"


def warn(msg: str) -> str:
    """Format *msg* with a yellow ``[WARN]`` prefix."""
    return f"\033[33m[WARN]\033[0m  {msg}"


def fail(msg: str) -> str:
    """Format *msg* with a red ``[FAIL]`` prefix."""
    return f"\033[31m[FAIL]\033[0m  {msg}"


def print_header(title: str, lines: list[str], width: int = 68) -> None:
    """Print a boxed header: a bold title followed by ``label: value`` lines.

    Matches the box style ``build_dataset.py``/``inspect_dataset.py``
    already print by hand — factored out so the six new CLIs render an
    identical header without copy-pasting the box-drawing characters six
    more times.

    Args:
        title: Bold title line, e.g. ``"Dataset Quality"``.
        lines: Pre-formatted body lines, printed one per line inside the box.
        width: Box width in characters.
    """
    print()
    print("─" * width)
    print(f"  \033[1m{title}\033[0m")
    print("─" * width)
    for line in lines:
        print(f"  {line}")
    print("─" * width)
    print()


# ── Dataset / baseline resolution (shared by all six Phase 3.5 CLIs) ────────────

def resolve_dataset_dir(dataset_dir: Path | None, de: ConfigDict) -> Path | None:
    """Resolve the dataset directory a CLI should operate on.

    Args:
        dataset_dir: Explicit ``--dataset-dir`` override, or None.
        de: The merged config's ``dataset_engineering`` section.

    Returns:
        *dataset_dir* if given; else ``dataset_engineering.output_dir`` if
        set; else the most recently built dataset under
        ``dataset_engineering.datasets_dir`` (None if none exists).
    """
    if dataset_dir is not None:
        return Path(dataset_dir)
    if de.get("output_dir"):
        return Path(de["output_dir"])
    datasets_dir = Path(de.get("datasets_dir", "data/processed/datasets"))
    return resolve_latest_dataset_dir(datasets_dir)


def resolve_baseline_artifact(
    baseline: Path | None, resolved_dir: Path,
) -> DatasetArtifact | None:
    """Resolve the baseline dataset artifact for a regression/gate/review comparison.

    Args:
        baseline: Explicit ``--baseline`` override, or None.
        resolved_dir: The dataset directory being operated on.

    Returns:
        A loaded :class:`~src.quality.artifacts.DatasetArtifact`, or None
        if no baseline was given and none can be resolved from
        *resolved_dir*'s own ``version.json`` (either because it has never
        been versioned, has no ``previous_artifact_id``, or the recorded
        previous version's artifacts are no longer on disk).
    """
    if baseline is not None:
        return load_dataset_artifacts(Path(baseline))
    try:
        version = load_version_record(resolved_dir)
    except ArtifactNotFoundError:
        return None
    if version.previous_artifact_id is None:
        return None
    baseline_dir = resolved_dir.parent / version.previous_artifact_id
    try:
        return load_dataset_artifacts(baseline_dir)
    except ARTIFACT_LOAD_ERRORS:
        click.echo(warn(
            f"Previous version {version.previous_artifact_id!r} recorded but not found or"
            " could not be loaded — proceeding without a baseline.",
        ), err=True)
        return None
