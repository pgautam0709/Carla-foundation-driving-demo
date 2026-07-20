"""
src/data/dataset_io.py — Shared JSONL reading helper for Phase 3 dataset modules.

Centralizes the "parse a JSONL file into a list of dicts, stopping at the
first corrupt line" behavior so :mod:`src.data.dataset_builder` and
:mod:`src.data.dataset_outliers` agree on how a partially-corrupt
``controls.jsonl`` / ``telemetry.jsonl`` file is handled, rather than each
maintaining its own copy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file into a list of dicts, stopping at the first bad line.

    Args:
        path: Path to a ``.jsonl`` file.

    Returns:
        List of parsed records in file order. Empty if the file is missing.
    """
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            break
        records.append(record)
    return records
