"""
src/data/dataset_splits.py — Deterministic train/val/test split assignment.

Splits are assigned **per episode**, not per sample: every sample from a
given episode gets the same split. Frames within one episode are highly
correlated (consecutive ticks of the same drive), so splitting at the
sample level would leak near-duplicate frames across train/val/test and
inflate validation metrics.

Assignment is computed for the **whole batch** of included episodes at
once, using a deterministic hash order plus the largest-remainder method
(the standard way to turn a ratio into integer counts that sum exactly to
the total). This matters for small episode counts: assigning each episode
independently via ``hash(episode_id) mod N`` can — and, in early dry-run
testing with only 1-2 episodes, did — place every episode in the same
minority split by chance, leaving ``train`` empty even though samples
exist. The batch method guarantees exact proportional counts for larger
datasets and an explicit non-empty-train guarantee for tiny ones.
"""

from __future__ import annotations

from collections.abc import Sequence

from src.data.episode import compute_route_hash

#: The split name that must never come back empty when at least one
#: episode is available and its configured ratio is greater than zero.
_PRIORITY_SPLIT = "train"


def assign_splits(
    episode_ids: Sequence[str],
    split_ratios: dict[str, float],
    seed: int,
) -> dict[str, str]:
    """Deterministically assign every episode in *episode_ids* to a split.

    Args:
        episode_ids: Episode identifiers to assign. Input order does not
            affect the result — assignment order is derived from a hash of
            each ID, not list position.
        split_ratios: Mapping of split name to relative weight (e.g.
            ``{"train": 0.8, "val": 0.1, "test": 0.1}``). Need not sum to 1
            — weights are normalized internally.
        seed: Seed mixed into the hash so changing it reshuffles all
            assignments; keeping it fixed reproduces prior assignments
            exactly.

    Returns:
        Mapping of ``episode_id -> split name``. Empty if *episode_ids* is
        empty.

    Raises:
        ValueError: If *split_ratios* is empty or its weights sum to <= 0.
    """
    if not episode_ids:
        return {}
    if not split_ratios:
        raise ValueError("split_ratios must not be empty")
    total = sum(split_ratios.values())
    if total <= 0:
        raise ValueError(f"split_ratios must sum to a positive value, got {total}")

    n = len(episode_ids)
    names = list(split_ratios.keys())
    normalized = {name: split_ratios[name] / total for name in names}

    # Deterministic pseudo-random order: a hash of (episode_id, seed), not
    # the caller's list position, so discovery order never affects the
    # result but the seed does.
    ordered = sorted(
        episode_ids,
        key=lambda eid: compute_route_hash({"episode_id": eid, "seed": seed}),
    )

    counts = _largest_remainder_counts(n, normalized)
    _guarantee_priority_split(counts, normalized)

    assignment: dict[str, str] = {}
    cursor = 0
    for name in names:
        for episode_id in ordered[cursor:cursor + counts[name]]:
            assignment[episode_id] = name
        cursor += counts[name]
    return assignment


# ── Internal helpers ───────────────────────────────────────────────────────────

def _largest_remainder_counts(n: int, normalized: dict[str, float]) -> dict[str, int]:
    """Turn normalized ratios into integer counts summing exactly to *n*.

    Uses the largest-remainder method: floor each split's proportional
    share, then hand out the leftover slots to the splits with the largest
    fractional remainder (ties broken by ratio, then name, for determinism).

    Args:
        n: Total number of episodes to distribute.
        normalized: Mapping of split name to weight in ``[0, 1]`` summing
            to 1.0.

    Returns:
        Mapping of split name to integer count. Counts sum to *n*.
    """
    raw = {name: normalized[name] * n for name in normalized}
    counts = {name: int(raw[name]) for name in normalized}
    remainder = n - sum(counts.values())

    by_remainder = sorted(
        normalized,
        key=lambda name: (-(raw[name] - counts[name]), -normalized[name], name),
    )
    for name in by_remainder[:remainder]:
        counts[name] += 1
    return counts


def _guarantee_priority_split(counts: dict[str, int], normalized: dict[str, float]) -> None:
    """Ensure the priority split is non-empty whenever episodes are available.

    The largest-remainder method already favors ``train`` in the common
    case (it usually has the largest ratio, hence the largest fractional
    remainder for small ``n``), but that is an emergent property of the
    ratios, not a guarantee. This makes the guarantee explicit: if
    ``train`` is configured with a positive ratio and ended up with zero
    episodes while another split has spare episodes, one episode is moved
    from the largest split into ``train``.

    Args:
        counts: Per-split integer counts, mutated in place.
        normalized: Normalized split ratios (same keys as *counts*).
    """
    if _PRIORITY_SPLIT not in counts or normalized.get(_PRIORITY_SPLIT, 0.0) <= 0:
        return
    if counts[_PRIORITY_SPLIT] > 0:
        return

    donor = max(counts, key=lambda name: counts[name])
    if donor != _PRIORITY_SPLIT and counts[donor] > 0:
        counts[donor] -= 1
        counts[_PRIORITY_SPLIT] += 1
