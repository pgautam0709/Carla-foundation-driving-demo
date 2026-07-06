#!/usr/bin/env python3
"""
scripts/train.py — Model training entry point (Phase 3+).

This script is a placeholder for the training pipeline.
It validates the configuration and data directory, then exits with a
clear message directing to Phase 3.

Usage::

    python scripts/train.py
    python scripts/train.py --profile linux_gpu
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import click

from src.utils.config import load_config
from src.utils.logging import configure_logging, get_logger

log = get_logger(__name__)


@click.command()
@click.option("--config",     default="config/default.yaml", help="Base config file")
@click.option("--profile",    default="local_dev",           help="Config profile")
@click.option("--checkpoint", default=None,                  help="Resume from checkpoint")
def main(config: str, profile: str, checkpoint: str | None) -> None:
    """Train the driving model (Phase 3+).

    This script will be fully implemented in Phase 3 when model
    architecture and dataset loader are defined.
    """
    cfg = load_config(profile=profile)
    configure_logging(
        level=cfg["logging"]["level"],
        fmt=cfg["logging"]["format"],
    )

    train_cfg = cfg["training"]
    data_dir  = Path(train_cfg["data_dir"])

    log.info(
        "training.config_loaded",
        profile=profile,
        model=train_cfg["model"],
        device=train_cfg["device"],
        epochs=train_cfg["epochs"],
        batch_size=train_cfg["batch_size"],
    )

    # Validate data directory
    if not data_dir.exists():
        log.error(
            "training.data_dir_missing",
            path=str(data_dir),
            hint="Run 'make collect' first to generate training data.",
        )
        sys.exit(1)

    hdf5_files = list(data_dir.glob("*.hdf5"))
    if not hdf5_files:
        log.warning(
            "training.no_data_files",
            path=str(data_dir),
            hint="Run 'make collect' to generate .hdf5 episode files.",
        )
        click.echo(
            "\n[WARN] No .hdf5 files found in data/processed/.\n"
            "       Run 'make collect' first, then process with Phase 2 tools.\n",
            err=True,
        )
    else:
        log.info("training.data_found", n_files=len(hdf5_files))

    # Phase 3 placeholder
    click.echo(
        "\n  Training pipeline not yet implemented.\n"
        "  This script will be fully built in Phase 3:\n"
        "    • Dataset loader (HDF5 → PyTorch DataLoader)\n"
        "    • BC-CNN model architecture\n"
        "    • Training loop with TensorBoard logging\n"
        "    • Checkpoint saving and resumption\n"
        "  See docs/PHASES.md for the Phase 3 roadmap.\n"
    )


if __name__ == "__main__":
    main()
